"""Synthetic deployment approvals and SQLite fixtures only; never provision real users."""

from io import StringIO
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, models
from django.test import TestCase, override_settings
from django.utils import timezone

from activities import bootstrap
from activities.access_rules import ADMINISTRATIVE_PERMISSIONS, GRANT_PERMISSIONS
from activities.grant_services import OperationConflict, _registry, issue_grant
from activities.models import Activity, ActivityPermissionGrant
from activities.test_history import traced_locks
from assessments.models import AuditEvent

User = get_user_model()


class BootstrapTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.password = "Synthetic-Bootstrap-Actor-728!"
        cls.actor = User.objects.create_user("bootstrap-actor@example.com", cls.password)
        cls.recipient = User.objects.create_user("bootstrap-recipient@example.com", "Synthetic-Recipient-849!")
        cls.other = User.objects.create_user("bootstrap-other@example.com", cls.password, is_staff=True, is_superuser=True)
        cls.activity = Activity.objects.create(name="Synthetic A", training_date=timezone.localdate(), status="active", created_by=cls.actor)
        cls.activity_b = Activity.objects.create(name="Synthetic B", training_date=timezone.localdate(), status="active", created_by=cls.other)
        cls.registry = _registry("default")

    def setUp(self):
        self.plan = dict(actor_id=self.actor.pk, activity_id=self.activity.pk, recipient_id=self.recipient.pk,
            permissions=sorted(GRANT_PERMISSIONS), operation_id=str(uuid4()), reason="Approved synthetic initialization",
            approval_reference="Synthetic approval REF-001")
        self.enterContext(self.approved())

    def approved(self, **changes):
        return override_settings(ACTIVITY_ADMIN_BOOTSTRAP_ENABLED=True,
            ACTIVITY_ADMIN_BOOTSTRAP_PLANS=({**self.plan, **changes},))

    def call(self, **changes):
        command = {key: value for key, value in self.plan.items() if key != "actor_id"}
        return bootstrap.bootstrap_activity_administrator(actor_email=changes.pop("actor_email", self.actor.email),
            actor_password=changes.pop("actor_password", self.password), **{**command, **changes})

    def state(self):
        return (list(User.user_permissions.through.objects.order_by("pk").values()),
                list(ActivityPermissionGrant.objects.order_by("pk").values()),
                list(AuditEvent.objects.order_by("pk").values()),
                list(User.objects.order_by("pk").values("id", "email", "password", "is_active", "is_staff", "is_superuser")))

    def grant(self, name, *, activity=None, user=None, active=True):
        return ActivityPermissionGrant.objects.create(user=user or self.recipient, activity=activity or self.activity,
            permission=self.registry[name], created_by=self.actor, is_active=active)

    def test_approved_plain_actor_bootstraps_exactly_eight_permissions_and_grants(self):
        self.assertFalse(self.actor.is_staff)
        self.assertFalse(self.actor.is_superuser)
        self.assertFalse(self.actor.user_permissions.exists())
        before_users = self.state()[3]
        receipt = self.call()
        self.assertEqual(receipt.activity_id, self.activity.pk)
        self.assertEqual(receipt.recipient_id, self.recipient.pk)
        self.assertEqual(len(receipt.grant_versions), 8)
        self.assertTrue(all(v == 1 for pk, v in receipt.grant_versions))
        self.assertEqual(set(self.recipient.user_permissions.values_list("pk", flat=True)), {p.pk for p in self.registry.values()})
        self.assertEqual(ActivityPermissionGrant.objects.count(), 8)
        self.assertFalse(ActivityPermissionGrant.objects.exclude(user=self.recipient, activity=self.activity, is_active=True).exists())
        self.assertEqual(self.state()[3], before_users)
        self.assertEqual(AuditEvent.objects.count(), 1)
        event = AuditEvent.objects.get(pk=receipt.audit_event_id)
        self.assertEqual(event.actor_user_id, self.actor.pk)
        self.assertEqual(event.actor_kind, "user")
        self.assertEqual(event.activity_id, self.activity.pk)
        for field in ("session_id", "participation_id", "assessment_id", "authentication_context", "version_before", "version_after"):
            self.assertIsNone(getattr(event, field))
        self.assertEqual(len(event.after_state["grants"]), 8)
        self.assertEqual(event.before_state["direct_permission_ids"], [])
        self.assertEqual(event.after_state["bootstrap_result"]["added_global_permission_ids"], sorted(p.pk for p in self.registry.values()))

    def test_wrong_unknown_or_inactive_actor_cannot_authenticate(self):
        for changes in ({"actor_password": "wrong"}, {"actor_email": "unknown@example.com"}, {"actor_password": None}):
            before = self.state()
            with self.subTest(changes=tuple(changes)), self.assertRaises(PermissionDenied):
                self.call(**changes)
            self.assertEqual(self.state(), before)
        User.objects.filter(pk=self.actor.pk).update(is_active=False)
        with self.assertRaises(PermissionDenied):
            self.call()

    def test_actor_id_or_instance_is_not_an_authentication_interface(self):
        for changes in ({"actor_id": self.actor.pk}, {"actor": self.actor}):
            with self.assertRaises(TypeError):
                self.call(**changes)
        with self.assertRaises(PermissionDenied):
            self.call(actor_email=self.actor)

    def test_staff_superuser_creator_and_group_do_not_supply_deployment_authority(self):
        group = Group.objects.create(name="Synthetic authority")
        group.permissions.add(*self.registry.values())
        self.other.groups.add(group)
        before = self.state()
        with self.assertRaises(PermissionDenied):
            self.call(actor_email=self.other.email)
        with override_settings(ACTIVITY_ADMIN_BOOTSTRAP_ENABLED=False):
            with self.assertRaises(PermissionDenied):
                self.call()
        self.assertEqual(self.state(), before)

    def test_default_disabled_and_invalid_deployment_config_fail_before_database_or_prompt(self):
        bad = (
            {"ACTIVITY_ADMIN_BOOTSTRAP_ENABLED": False},
            {"ACTIVITY_ADMIN_BOOTSTRAP_ENABLED": "true"},
            {"ACTIVITY_ADMIN_BOOTSTRAP_PLANS": ()},
            {"ACTIVITY_ADMIN_BOOTSTRAP_PLANS": {"not": "a plan"}},
            {"ACTIVITY_ADMIN_BOOTSTRAP_PLANS": ({**self.plan, "secret": "must not be printed"},)},
            {"ACTIVITY_ADMIN_BOOTSTRAP_PLANS": (self.plan, self.plan)},
        )
        for settings in bad:
            with self.subTest(keys=list(settings)), override_settings(**settings), self.assertNumQueries(0):
                with self.assertRaises((PermissionDenied, ImproperlyConfigured)) as error:
                    self.call()
                self.assertNotIn("must not be printed", str(error.exception))

    def test_unapproved_scope_recipient_reason_or_reference_is_denied(self):
        for changes in ({"activity_id": self.activity_b.pk}, {"recipient_id": self.other.pk},
                        {"reason": "Not the approved reason"}, {"approval_reference": "Fake approval"},
                        {"operation_id": str(uuid4())}):
            before = self.state()
            with self.subTest(changes=changes), self.assertRaises(PermissionDenied):
                self.call(**changes)
            self.assertEqual(self.state(), before)

    def test_permission_plan_requires_exact_whitelist_without_duplicates(self):
        names = sorted(GRANT_PERMISSIONS)
        for permissions in (names[:-1], names + [names[0]], names[:-1] + ["auth.change_group"], [], "all", [True] * 8):
            with self.subTest(plan_type=type(permissions).__name__), self.assertRaises(ValidationError):
                self.call(permissions=permissions)
        self.assertFalse(ActivityPermissionGrant.objects.exists())
        self.assertFalse(self.recipient.user_permissions.exists())

    def test_missing_reason_approval_uuid_and_invalid_ids_are_rejected(self):
        for changes in ({"reason": " "}, {"approval_reference": ""}, {"operation_id": "not-uuid"},
                        {"operation_id": None}, {"recipient_id": True}, {"activity_id": "1"}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                self.call(**changes)

    def test_recipient_inactive_and_closed_activity_reject_without_partial_admin(self):
        User.objects.filter(pk=self.recipient.pk).update(is_active=False)
        before = self.state()
        with self.assertRaises(ValidationError):
            self.call()
        self.assertEqual(self.state(), before)
        User.objects.filter(pk=self.recipient.pk).update(is_active=True)
        self.activity.status = "closed"
        self.activity.save()
        with self.assertRaises(ValidationError):
            self.call()
        self.assertFalse(self.recipient.user_permissions.exists())

    def test_approved_but_missing_database_resources_fail_closed(self):
        for changes in ({"recipient_id": 999999}, {"activity_id": 999999}):
            before = self.state()
            with self.approved(**changes), self.assertRaises(PermissionDenied):
                self.call(**changes)
            self.assertEqual(self.state(), before)

    def test_disabled_approval_denies_even_an_identical_historical_retry(self):
        self.call()
        before = self.state()
        with override_settings(ACTIVITY_ADMIN_BOOTSTRAP_ENABLED=False), self.assertRaises(PermissionDenied):
            self.call()
        self.assertEqual(self.state(), before)

    def test_actor_password_change_after_authentication_invalidates_proof(self):
        from django.contrib.auth import authenticate
        original = authenticate
        def authenticate_then_change(**credentials):
            actor = original(**credentials)
            User.objects.filter(pk=self.actor.pk).update(password="revoked-credential-fixture")
            return actor
        with patch.object(bootstrap, "authenticate", side_effect=authenticate_then_change):
            with self.assertRaises(PermissionDenied):
                self.call()
        self.assertFalse(ActivityPermissionGrant.objects.exists())

    def test_deployment_approval_is_checked_again_inside_transaction(self):
        original = bootstrap._approved
        def approve_then_disable(plan):
            original(plan)
            if wrapped.call_count == 1:
                from django.conf import settings
                settings.ACTIVITY_ADMIN_BOOTSTRAP_ENABLED = False
        with patch.object(bootstrap, "_approved", side_effect=approve_then_disable) as wrapped:
            with self.assertRaises(PermissionDenied):
                self.call()
        self.assertFalse(ActivityPermissionGrant.objects.exists())

    def test_identical_retry_canonicalizes_plan_order_and_does_not_mutate(self):
        first = self.call()
        before = self.state()
        second = self.call(permissions=list(reversed(self.plan["permissions"])), operation_id=self.plan["operation_id"])
        self.assertEqual(first, second)
        self.assertEqual(self.state(), before)

    def test_second_bootstrap_with_new_authorized_uuid_is_rejected(self):
        self.call()
        operation = str(uuid4())
        before = self.state()
        with self.approved(operation_id=operation), self.assertRaises(ValidationError):
            self.call(operation_id=operation)
        self.assertEqual(self.state(), before)

    def test_revoked_administrator_does_not_reopen_bootstrap_or_repair_on_replay(self):
        receipt = self.call()
        for row in ActivityPermissionGrant.objects.filter(activity=self.activity):
            row.is_active = False
            row.save()
        self.recipient.user_permissions.clear()
        before = self.state()
        self.assertEqual(self.call(), receipt)
        self.assertEqual(self.state(), before)
        operation = str(uuid4())
        with self.approved(operation_id=operation), self.assertRaises(ValidationError):
            self.call(operation_id=operation)
        self.assertEqual(self.state(), before)

    def test_existing_active_or_inactive_administrative_mandate_is_not_recovery(self):
        name = sorted(ADMINISTRATIVE_PERMISSIONS)[0]
        row = self.grant(name)
        for active in (True, False):
            row.is_active = active
            row.save()
            before = self.state()
            with self.assertRaises(ValidationError):
                self.call()
            self.assertEqual(self.state(), before)

    def test_existing_inactive_operational_grant_is_not_reactivated(self):
        self.grant("assessments.add_assessment", active=False)
        before = self.state()
        with self.assertRaises(ValidationError):
            self.call()
        self.assertEqual(self.state(), before)

    def test_existing_active_operational_history_is_preserved(self):
        row = self.grant("assessments.add_assessment")
        models.QuerySet(model=ActivityPermissionGrant).filter(pk=row.pk).update(edit_version=7)
        before = ActivityPermissionGrant.objects.filter(pk=row.pk).values().get()
        extra = Permission.objects.get(content_type__app_label="accounts", codename="view_user")
        self.recipient.user_permissions.add(extra)
        self.call()
        self.assertEqual(ActivityPermissionGrant.objects.filter(pk=row.pk).values().get(), before)
        self.assertTrue(self.recipient.user_permissions.filter(pk=extra.pk).exists())
        self.assertEqual(ActivityPermissionGrant.objects.count(), 8)

    def test_global_additions_cannot_activate_other_activity_grants(self):
        self.grant("assessments.add_assessment", activity=self.activity_b)
        before = self.state()
        with self.assertRaises(ValidationError):
            self.call()
        self.assertEqual(self.state(), before)

    def test_same_recipient_can_initialize_another_activity_without_new_global_rights(self):
        self.call()
        grants_before = list(ActivityPermissionGrant.objects.filter(activity=self.activity).values())
        operation = str(uuid4())
        with self.approved(activity_id=self.activity_b.pk, operation_id=operation):
            receipt = self.call(activity_id=self.activity_b.pk, operation_id=operation)
        self.assertEqual(receipt.added_global_permission_ids, ())
        self.assertEqual(list(ActivityPermissionGrant.objects.filter(activity=self.activity).values()), grants_before)
        self.assertEqual(AuditEvent.objects.count(), 2)

    def test_uuid_reuse_for_different_approved_plan_is_a_conflict(self):
        self.call()
        before = self.state()
        with self.approved(recipient_id=self.other.pk), self.assertRaises(OperationConflict):
            self.call(recipient_id=self.other.pk)
        self.assertEqual(self.state(), before)

    def test_uuid_collision_with_another_audit_action_is_a_conflict(self):
        AuditEvent.objects.create(operation_id=self.plan["operation_id"], actor_kind="user", actor_user=self.actor,
            activity=self.activity, action="activity_grant.issued", before_state={"schema_version": 1}, after_state={"schema_version": 1})
        before = self.state()
        with self.assertRaises(OperationConflict):
            self.call()
        self.assertEqual(self.state(), before)

    def test_collision_at_audit_insert_rolls_back_grants_and_global_permissions(self):
        AuditEvent.objects.create(operation_id=self.plan["operation_id"], actor_kind="user", actor_user=self.actor,
            activity=self.activity, action="activity_grant.issued", before_state={"schema_version": 1}, after_state={"schema_version": 1})
        before = self.state()
        with patch.object(bootstrap, "_replay", return_value=None), self.assertRaises(OperationConflict):
            self.call()
        self.assertEqual(self.state(), before)

    def test_audit_failure_rolls_back_all_grants_and_global_permission_changes(self):
        for error in (RuntimeError("synthetic failure"), IntegrityError("synthetic audit insert failure")):
            before = self.state()
            with self.subTest(error_type=type(error).__name__), patch.object(AuditEvent, "save", side_effect=error) as writer:
                with self.assertRaises(type(error)):
                    self.call()
                writer.assert_called_once()
            self.assertEqual(self.state(), before)

    def test_missing_permission_configuration_creates_nothing(self):
        name = sorted(GRANT_PERMISSIONS)[0]
        row = self.registry[name]
        row.codename = "unavailable-fixture-permission"
        row.save()
        before = self.state()
        with self.assertRaises(PermissionDenied):
            self.call()
        self.assertEqual(self.state(), before)

    def test_lock_order_is_activity_users_then_grants_without_join_locks(self):
        self.grant("assessments.add_assessment")
        with traced_locks() as trace:
            self.call()
        self.assertEqual(list(dict.fromkeys(row[0] for row in trace)), ["Activity", "User", "ActivityPermissionGrant"])
        for model, ids, joins in trace:
            self.assertEqual(ids, sorted(ids))
            self.assertFalse(joins)

    def test_snapshot_never_contains_credentials_or_user_profiles(self):
        self.call()
        event = AuditEvent.objects.get(action=bootstrap.ACTION)
        rendered = str(event.before_state) + str(event.after_state)
        for forbidden in (self.password, self.actor.password, self.actor.email, self.recipient.email,
                          "password", "cookie", "session_key", "secret_hash"):
            self.assertNotIn(forbidden, rendered)
        self.assertEqual(event.after_state["approval_reference"], self.plan["approval_reference"])

    def test_bootstrap_recipient_can_use_ordinary_service_without_bypassing_its_rules(self):
        self.call()
        recipient = User.objects.create_user("ordinary-recipient@example.com")
        permission = self.registry["assessments.add_assessment"]
        recipient.user_permissions.add(permission)
        receipt = issue_grant(actor=self.recipient, activity_id=self.activity.pk, recipient_id=recipient.pk,
            permission_id=permission.pk, expected_edit_version=0, operation_id=uuid4(), reason="Approved ordinary grant")
        self.assertTrue(receipt.is_active)
        with self.assertRaises(PermissionDenied):
            issue_grant(actor=self.recipient, activity_id=self.activity.pk, recipient_id=self.actor.pk,
                permission_id=self.registry[sorted(ADMINISTRATIVE_PERMISSIONS)[0]].pk,
                expected_edit_version=0, operation_id=uuid4(), reason="Not delegable")

    def test_deployment_command_authenticates_without_password_arguments_or_output(self):
        stdout = StringIO()
        kwargs = {key: value for key, value in self.plan.items() if key != "actor_id"}
        module = "activities.management.commands.bootstrap_activity_admin"
        with patch(module + ".sys.stdin.isatty", return_value=True), patch(module + ".getpass", return_value=self.password):
            call_command("bootstrap_activity_admin", actor_email=self.actor.email, stdout=stdout, **kwargs)
        self.assertIn("Bootstrap recorded", stdout.getvalue())
        self.assertNotIn(self.password, stdout.getvalue())
        self.assertNotIn(self.actor.email, stdout.getvalue())
        self.assertEqual(AuditEvent.objects.count(), 1)

    def test_command_bad_authentication_never_outputs_password(self):
        module = "activities.management.commands.bootstrap_activity_admin"
        kwargs = {key: value for key, value in self.plan.items() if key != "actor_id"}
        stdout = StringIO()
        before = self.state()
        wrong = "Synthetic-Wrong-Secret-728!"
        with patch(module + ".sys.stdin.isatty", return_value=True), patch(module + ".getpass", return_value=wrong):
            with self.assertRaises(CommandError) as error:
                call_command("bootstrap_activity_admin", actor_email=self.actor.email, stdout=stdout, **kwargs)
        self.assertNotIn(wrong, str(error.exception) + stdout.getvalue())
        self.assertEqual(self.state(), before)

    def test_command_disabled_invalid_config_or_noninteractive_does_not_prompt(self):
        module = "activities.management.commands.bootstrap_activity_admin"
        kwargs = {key: value for key, value in self.plan.items() if key != "actor_id"}
        for settings in ({"ACTIVITY_ADMIN_BOOTSTRAP_ENABLED": False}, {"ACTIVITY_ADMIN_BOOTSTRAP_PLANS": ()}):
            with override_settings(**settings), patch(module + ".getpass") as prompt:
                with self.assertRaises(CommandError):
                    call_command("bootstrap_activity_admin", actor_email=self.actor.email, **kwargs)
                prompt.assert_not_called()
        with patch(module + ".sys.stdin.isatty", return_value=False), patch(module + ".getpass") as prompt:
            with self.assertRaises(CommandError):
                call_command("bootstrap_activity_admin", actor_email=self.actor.email, **kwargs)
            prompt.assert_not_called()
        self.assertFalse(ActivityPermissionGrant.objects.exists())

    def test_bootstrap_to_self_requires_the_same_explicit_approved_plan(self):
        with self.approved(recipient_id=self.actor.pk):
            self.call(recipient_id=self.actor.pk)
        self.assertEqual(ActivityPermissionGrant.objects.filter(user=self.actor).count(), 8)
        self.assertFalse(self.actor.is_staff)
        self.assertFalse(self.actor.is_superuser)
