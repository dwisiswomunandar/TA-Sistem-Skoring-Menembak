"""Synthetic deployment approvals only; SQLite does not prove concurrency safety."""

from copy import deepcopy
from decimal import Decimal
from io import StringIO
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import Participant
from activities import administrator_transfer as transfer
from activities.authority_policy import (
    ACTIVITY_ADMIN_POLICY, ACTIVITY_ADMIN_PERMISSIONS, ACTIVITY_ADMIN_MANAGEMENT_PERMISSIONS,
    administrator_permissions, effective_administrator_ids,
)
from activities.grant_services import OperationConflict, revoke_grant
from activities.models import Activity, ActivityParticipation, ActivityPermissionGrant, TrainingSession
from activities.test_history import traced_locks
from assessments.models import AuditEvent

User = get_user_model()


class AdministratorTransferTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.password = "Synthetic-Transfer-Source-278!"
        cls.successor_password = "Synthetic-Transfer-Successor-983!"
        cls.source = User.objects.create_user("source-transfer@example.com", cls.password)
        cls.successor = User.objects.create_user("successor-transfer@example.com", cls.successor_password)
        cls.other = User.objects.create_user("other-transfer@example.com", cls.password, is_superuser=True, is_staff=True)
        cls.activity = Activity.objects.create(name="Synthetic transfer A", training_date=timezone.localdate(),
            status="active", created_by=cls.source)
        cls.activity_b = Activity.objects.create(name="Synthetic transfer B", training_date=timezone.localdate(),
            status="active", created_by=cls.source)
        cls.registry = administrator_permissions("default")
        cls.source.user_permissions.add(*cls.registry.values())
        for permission in cls.registry.values():
            ActivityPermissionGrant.objects.create(user=cls.source, activity=cls.activity,
                permission=permission, created_by=cls.source)

    def setUp(self):
        self.plan = dict(actor_id=self.source.pk, activity_id=self.activity.pk, recipient_id=self.successor.pk,
            policy=ACTIVITY_ADMIN_POLICY, operation_id=str(uuid4()), reason="Approved synthetic handover",
            approval_reference="Synthetic TRANSFER-001", **self.preconditions())
        self.enterContext(self.approved())

    def preconditions(self):
        return transfer.transfer_preconditions(activity_id=self.activity.pk, source_id=self.source.pk,
                                               recipient_id=self.successor.pk)

    def approved(self, **changes):
        return override_settings(ACTIVITY_ADMIN_TRANSFER_ENABLED=True,
                                 ACTIVITY_ADMIN_TRANSFER_PLANS=({**self.plan, **changes},))

    def call(self, **changes):
        args = dict(source_email=self.source.email, source_password=self.password,
            successor_email=self.successor.email, successor_password=self.successor_password,
            operation_id=self.plan["operation_id"])
        return transfer.transfer_activity_administrator(**{**args, **changes})

    def state(self):
        return (list(User.objects.order_by("pk").values()),
            list(User.user_permissions.through.objects.order_by("pk").values()),
            list(User.groups.through.objects.order_by("pk").values()),
            list(ActivityPermissionGrant.objects.order_by("pk").values()),
            list(AuditEvent.objects.order_by("pk").values()))

    def grant(self, name, *, user=None, activity=None, active=True):
        return ActivityPermissionGrant.objects.create(user=user or self.successor,
            activity=activity or self.activity, permission=self.registry[name], created_by=self.other, is_active=active)

    def test_normal_transfer_keeps_operational_source_and_completes_successor(self):
        before_users = list(User.objects.order_by("pk").values())
        source_direct = set(self.source.user_permissions.values_list("pk", flat=True))
        old = {row["permission_id"]: row for row in ActivityPermissionGrant.objects.filter(user=self.source).values()}
        receipt = self.call()
        self.assertEqual(effective_administrator_ids(self.activity.pk, using="default"), {self.successor.pk})
        self.assertEqual(len(receipt.grant_versions), 16)
        self.assertEqual(set(receipt.added_global_permission_ids), set(source_direct))
        self.assertEqual(list(User.objects.order_by("pk").values()), before_users)
        self.assertEqual(set(self.source.user_permissions.values_list("pk", flat=True)), source_direct)
        self.assertEqual(ActivityPermissionGrant.objects.count(), 16)
        for name, permission in self.registry.items():
            row = ActivityPermissionGrant.objects.filter(user=self.source, permission=permission).values().get()
            self.assertEqual(row["id"], old[permission.pk]["id"])
            self.assertEqual(row["created_by_id"], old[permission.pk]["created_by_id"])
            self.assertEqual(row["created_at"], old[permission.pk]["created_at"])
            if name in ACTIVITY_ADMIN_MANAGEMENT_PERMISSIONS:
                self.assertFalse(row["is_active"])
                self.assertEqual(row["edit_version"], 2)
            else:
                self.assertEqual(row, old[permission.pk])
        self.assertEqual(AuditEvent.objects.count(), 1)
        event = AuditEvent.objects.get(pk=receipt.audit_event_id)
        self.assertEqual(event.action, transfer.ACTION)
        self.assertEqual(event.actor_user_id, self.source.pk)
        self.assertEqual(event.activity_id, self.activity.pk)
        for field in ("participation_id", "session_id", "assessment_id", "authentication_context", "version_before", "version_after"):
            self.assertIsNone(getattr(event, field))
        self.assertEqual(event.after_state["effective_administrator_ids"], [self.successor.pk])
        self.assertEqual(event.after_state["policy"], "activity_admin_v1")
        self.assertEqual(event.before_state["approval_reference"], self.plan["approval_reference"])

    def test_source_authentication_is_required_even_with_approved_ids(self):
        before = self.state()
        for changes in ({"source_password": "wrong"}, {"source_email": "unknown@example.com"},
                        {"source_password": None}, {"source_email": self.other.email}):
            with self.subTest(changes=tuple(changes)), self.assertRaises(PermissionDenied):
                self.call(**changes)
            self.assertEqual(self.state(), before)

    def test_successor_must_authenticate_as_exact_recipient(self):
        before = self.state()
        for changes in ({"successor_password": "wrong"}, {"successor_email": "unknown@example.com"},
                        {"successor_password": None}, {"successor_email": self.other.email, "successor_password": self.password}):
            with self.subTest(changes=tuple(changes)), self.assertRaises(PermissionDenied):
                self.call(**changes)
            self.assertEqual(self.state(), before)

    def test_actor_objects_ids_and_client_plan_are_not_authentication_interfaces(self):
        for field, value in (("actor_id", self.source.pk), ("actor", self.source), ("permissions", list(self.registry)),
                             ("activity_id", self.activity_b.pk), ("recipient_id", self.other.pk)):
            with self.subTest(field=field), self.assertRaises(TypeError):
                self.call(**{field: value})

    def test_inactive_source_and_successor_are_rejected(self):
        for user in (self.source, self.successor):
            User.objects.filter(pk=user.pk).update(is_active=False)
            before = self.state()
            with self.assertRaises(PermissionDenied):
                self.call()
            self.assertEqual(self.state(), before)
            User.objects.filter(pk=user.pk).update(is_active=True)

    def test_invalid_or_missing_recipient_is_rejected(self):
        with self.approved(recipient_id=999999), self.assertRaises(PermissionDenied):
            self.call()
        self.assertFalse(AuditEvent.objects.exists())

    def test_non_effective_source_global_permission_is_denied_despite_cached_superuser(self):
        self.source.is_staff = self.source.is_superuser = True
        self.source.save()
        self.source.has_perm("assessments.add_assessment")
        self.source.user_permissions.remove(self.registry["assessments.add_assessment"])
        before = self.state()
        with self.assertRaises(PermissionDenied):
            self.call()
        self.assertEqual(self.state(), before)

    def test_non_effective_source_missing_scope_is_denied(self):
        row = ActivityPermissionGrant.objects.get(user=self.source, permission=self.registry["assessments.add_shotpoint"])
        row.is_active = False
        row.save()
        before = self.state()
        with self.assertRaises(PermissionDenied):
            self.call()
        self.assertEqual(self.state(), before)

    def test_cross_activity_source_mandate_does_not_authorize(self):
        with self.approved(activity_id=self.activity_b.pk), self.assertRaises(PermissionDenied):
            self.call()
        self.assertFalse(AuditEvent.objects.exists())

    def test_disabled_or_absent_approval_denies_without_writes(self):
        before = self.state()
        for configuration in ({"ACTIVITY_ADMIN_TRANSFER_ENABLED": False}, {"ACTIVITY_ADMIN_TRANSFER_PLANS": ()}):
            with override_settings(**configuration), self.assertRaises((PermissionDenied, ImproperlyConfigured)):
                self.call()
        with self.assertRaises(PermissionDenied):
            self.call(operation_id=uuid4())
        self.assertEqual(self.state(), before)

    def test_malformed_or_ambiguous_configuration_fails_closed(self):
        bad = [None, {}, {**self.plan, "permissions": ["auth.change_permission"]},
            {**self.plan, "recipient_id": self.source.pk}, {**self.plan, "policy": "activity_admin_v2"},
            {**self.plan, "actor_id": True}, {**self.plan, "expected_state_sha256": "invalid"},
            {**self.plan, "operation_id": "invalid"}, {**self.plan, "reason": " "}]
        versions = deepcopy(self.plan["expected_grant_versions"])
        versions["successor"]["auth.change_permission"] = 0
        bad.append({**self.plan, "expected_grant_versions": versions})
        versions = deepcopy(self.plan["expected_grant_versions"])
        versions["source"]["assessments.add_assessment"] = True
        bad.append({**self.plan, "expected_grant_versions": versions})
        before = self.state()
        for plan in bad:
            with self.subTest(plan_type=type(plan)), override_settings(ACTIVITY_ADMIN_TRANSFER_PLANS=(plan,)):
                with self.assertRaises(ImproperlyConfigured):
                    self.call()
        with override_settings(ACTIVITY_ADMIN_TRANSFER_PLANS=(self.plan, self.plan)), self.assertRaises(ImproperlyConfigured):
            self.call()
        with override_settings(ACTIVITY_ADMIN_TRANSFER_ENABLED="true"), self.assertRaises(ImproperlyConfigured):
            self.call()
        self.assertEqual(self.state(), before)

    def test_approval_revoked_during_authentication_denies_mutation(self):
        from django.conf import settings
        def login(**credentials):
            result = authenticate(**credentials)
            settings.ACTIVITY_ADMIN_TRANSFER_ENABLED = False
            return result
        before = self.state()
        with patch.object(transfer, "authenticate", side_effect=login), self.assertRaises(PermissionDenied):
            self.call()
        self.assertEqual(self.state(), before)

    def test_changed_source_or_successor_credential_after_authentication_is_rejected(self):
        for user in (self.source, self.successor):
            original_hash = User.objects.get(pk=user.pk).password
            def login(**credentials):
                result = authenticate(**credentials)
                if result is not None and result.pk == user.pk:
                    User.objects.filter(pk=user.pk).update(password="changed-synthetic-credential")
                return result
            with patch.object(transfer, "authenticate", side_effect=login), self.assertRaises(PermissionDenied):
                self.call()
            self.assertFalse(AuditEvent.objects.exists())
            self.assertEqual(ActivityPermissionGrant.objects.count(), 8)
            self.assertFalse(self.successor.user_permissions.exists())
            User.objects.filter(pk=user.pk).update(password=original_hash)

    def test_stale_successor_grant_version_is_rejected_without_refresh(self):
        row = self.grant("assessments.add_assessment", active=False)
        expected = self.preconditions()
        row.is_active = True
        row.save()
        before = self.state()
        with self.approved(**expected), self.assertRaisesMessage(ValidationError, "versions are stale"):
            self.call()
        self.assertEqual(self.state(), before)

    def test_stale_source_grant_aba_is_detected_by_version(self):
        row = ActivityPermissionGrant.objects.get(user=self.source, permission=self.registry["assessments.add_assessment"])
        row.is_active = False
        row.save()
        row.is_active = True
        row.save()
        before = self.state()
        with self.assertRaisesMessage(ValidationError, "versions are stale"):
            self.call()
        self.assertEqual(self.state(), before)

    def test_unexpected_new_successor_grant_conflicts_with_expected_absence(self):
        self.grant("assessments.add_assessment")
        before = self.state()
        with self.assertRaisesMessage(ValidationError, "versions are stale"):
            self.call()
        self.assertEqual(self.state(), before)

    def test_direct_permission_state_change_invalidates_approved_digest(self):
        self.successor.user_permissions.add(self.registry["assessments.add_assessment"])
        before = self.state()
        with self.assertRaisesMessage(ValidationError, "authority state is stale"):
            self.call()
        self.assertEqual(self.state(), before)

    def test_existing_successor_grants_reactivate_in_place_and_preserve_unchanged_rows(self):
        inactive = self.grant("activities.issue_activitypermissiongrant", active=False)
        active = self.grant("assessments.add_assessment")
        inactive_before = ActivityPermissionGrant.objects.filter(pk=inactive.pk).values().get()
        active_before = ActivityPermissionGrant.objects.filter(pk=active.pk).values().get()
        with self.approved(**self.preconditions()):
            self.call()
        inactive.refresh_from_db()
        self.assertTrue(inactive.is_active)
        self.assertEqual(inactive.edit_version, 2)
        self.assertEqual(inactive.created_by_id, inactive_before["created_by_id"])
        self.assertEqual(inactive.created_at, inactive_before["created_at"])
        self.assertEqual(ActivityPermissionGrant.objects.filter(pk=active.pk).values().get(), active_before)

    def test_new_globals_cannot_activate_external_activity_scope_even_if_inactive(self):
        row = self.grant("assessments.add_assessment", activity=self.activity_b, active=False)
        for active in (False, True):
            row.is_active = active
            row.save()
            before = self.state()
            with self.assertRaisesMessage(ValidationError, "another activity scope"):
                self.call()
            self.assertEqual(self.state(), before)

    def test_external_scope_is_unchanged_when_all_direct_globals_already_exist(self):
        self.successor.user_permissions.add(*self.registry.values())
        self.grant("assessments.add_assessment", activity=self.activity_b)
        external = list(ActivityPermissionGrant.objects.filter(activity=self.activity_b).values())
        with self.approved(**self.preconditions()):
            receipt = self.call()
        self.assertEqual(receipt.added_global_permission_ids, ())
        self.assertEqual(list(ActivityPermissionGrant.objects.filter(activity=self.activity_b).values()), external)

    def test_audit_failure_rolls_back_all_grants_versions_and_global_additions(self):
        before = self.state()
        with patch.object(AuditEvent, "save", side_effect=ValidationError("Synthetic audit failure")):
            with self.assertRaises(ValidationError):
                self.call()
        self.assertEqual(self.state(), before)
        self.call()  # A failed operation did not reserve its UUID.
        self.assertEqual(AuditEvent.objects.count(), 1)

    def test_continuity_failure_rolls_back_handover(self):
        before = self.state()
        original = transfer.require_administrator_continuity
        def guard(*args, **kwargs):
            result = original(*args, **kwargs)
            if wrapped.call_count == 2:
                raise ValidationError("Synthetic final continuity failure")
            return result
        with patch.object(transfer, "require_administrator_continuity", side_effect=guard) as wrapped:
            with self.assertRaises(ValidationError):
                self.call()
        self.assertEqual(wrapped.call_count, 2)
        self.assertEqual(self.state(), before)

    def test_uuid_collision_with_another_action_is_rejected(self):
        AuditEvent.objects.create(operation_id=self.plan["operation_id"], actor_kind="user", actor_user=self.source,
            activity=self.activity, action="synthetic.other", before_state={"schema_version": 1}, after_state={"schema_version": 1})
        before = self.state()
        with self.assertRaises(OperationConflict):
            self.call()
        self.assertEqual(self.state(), before)

    def test_same_uuid_changed_approved_plan_conflicts(self):
        self.call()
        before = self.state()
        with self.approved(reason="Different approved operation"), self.assertRaises(OperationConflict):
            self.call()
        self.assertEqual(self.state(), before)

    def test_retry_after_transfer_reads_history_without_reactivating_any_grant(self):
        first = self.call()
        # A later trusted fixture disables the successor; replay must not repair it.
        row = ActivityPermissionGrant.objects.get(user=self.successor, permission=self.registry["activities.issue_activitypermissiongrant"])
        row.is_active = False
        row.save()
        before = self.state()
        self.assertEqual(self.call(successor_password="unavailable"), first)
        self.assertEqual(self.state(), before)

    def test_revoked_approval_denies_receipt_replay(self):
        self.call()
        before = self.state()
        with override_settings(ACTIVITY_ADMIN_TRANSFER_ENABLED=False), self.assertRaises(PermissionDenied):
            self.call()
        with self.approved(approval_reference="Changed approval"), self.assertRaises(OperationConflict):
            self.call()
        self.assertEqual(self.state(), before)

    def test_inactive_or_unauthenticated_source_cannot_read_receipt(self):
        self.call()
        with self.assertRaises(PermissionDenied):
            self.call(source_password="wrong")
        User.objects.filter(pk=self.source.pk).update(is_active=False)
        before = self.state()
        with self.assertRaises(PermissionDenied):
            self.call()
        self.assertEqual(self.state(), before)

    def test_new_uuid_after_transfer_cannot_reuse_source_former_authority(self):
        self.call()
        before = self.state()
        operation = str(uuid4())
        with self.approved(operation_id=operation):
            with self.assertRaises(PermissionDenied):
                self.call(operation_id=operation)
        self.assertEqual(self.state(), before)

    def test_closed_activity_denies_new_transfer(self):
        self.activity.status = "closed"
        self.activity.save()
        before = self.state()
        with self.assertRaises(ValidationError):
            self.call()
        self.assertEqual(self.state(), before)

    def test_identity_groups_session_assignment_and_existing_audit_remain_unchanged(self):
        group = Group.objects.create(name="Synthetic profile group")
        self.source.groups.add(group)
        participant = Participant.objects.create(full_name="Synthetic transfer participant", identity_status="verified",
            verified_by=self.source, verified_at=timezone.now())
        participation = ActivityParticipation.objects.create(activity=self.activity, participant=participant,
            registration_status="approved", participant_identifier="SYNTHETIC-01", decided_by=self.source, decided_at=timezone.now())
        session = TrainingSession.objects.create(participation=participation, session_number=1,
            assigned_assessor=self.source, created_by=self.source, weapon_type="pistol", shooting_position="berdiri",
            distance_m=Decimal("10"), caliber_mm=Decimal("9"))
        event = AuditEvent.objects.create(operation_id=uuid4(), actor_kind="user", actor_user=self.source,
            session=session, action="synthetic.history", before_state={"schema_version": 1}, after_state={"schema_version": 1})
        history = AuditEvent.objects.filter(pk=event.pk).values().get()
        session_before = TrainingSession.objects.filter(pk=session.pk).values().get()
        group_before = list(User.groups.through.objects.values())
        self.call()
        self.assertEqual(AuditEvent.objects.filter(pk=event.pk).values().get(), history)
        self.assertEqual(TrainingSession.objects.filter(pk=session.pk).values().get(), session_before)
        self.assertEqual(list(User.groups.through.objects.values()), group_before)

    def test_snapshots_have_no_authentication_secrets_or_personal_profiles(self):
        self.call()
        event = AuditEvent.objects.get(action=transfer.ACTION)
        rendered = str(event.before_state) + str(event.after_state)
        for value in (self.password, self.successor_password, self.source.password, self.successor.password,
                      self.source.email, self.successor.email, "password", "session_key", "cookie"):
            self.assertNotIn(value, rendered)

    def test_lock_order_is_activity_users_sorted_grants_sorted_without_joins(self):
        with traced_locks() as locks:
            self.call()
        nonempty = [(name, ids, joined) for name, ids, joined in locks if ids]
        self.assertEqual([row[0] for row in nonempty[:3]], ["Activity", "User", "ActivityPermissionGrant"])
        self.assertEqual(nonempty[1][1], sorted(nonempty[1][1]))
        self.assertEqual(nonempty[2][1], sorted(nonempty[2][1]))
        self.assertTrue(all(not joined for name, ids, joined in nonempty))
        self.assertTrue(all(name == "ActivityPermissionGrant" for name, ids, joined in nonempty[3:]))

    def test_shared_policy_is_fixed_despite_registry_growth_and_ignores_group_rights(self):
        from activities import access_rules
        self.assertEqual(len(ACTIVITY_ADMIN_PERMISSIONS), 8)
        with patch.object(access_rules, "GRANT_PERMISSIONS", ACTIVITY_ADMIN_PERMISSIONS | {"accounts.change_user"}):
            self.assertEqual(effective_administrator_ids(self.activity.pk, using="default"), {self.source.pk})
        group = Group.objects.create(name="Synthetic global group")
        group.permissions.add(*self.registry.values())
        self.source.groups.add(group)
        self.source.user_permissions.remove(self.registry["assessments.add_assessment"])
        self.assertEqual(effective_administrator_ids(self.activity.pk, using="default"), set())

    def test_grant_revoke_uses_shared_policy_to_protect_new_last_administrator(self):
        self.call()
        revoker = self.other
        permission = self.registry["activities.revoke_activitypermissiongrant"]
        revoker.user_permissions.add(permission)
        self.grant("activities.revoke_activitypermissiongrant", user=revoker)
        target = ActivityPermissionGrant.objects.get(user=self.successor, permission=self.registry["assessments.add_assessment"])
        before = self.state()
        with self.assertRaises(ValidationError):
            revoke_grant(actor=revoker, activity_id=self.activity.pk, recipient_id=self.successor.pk,
                permission_id=target.permission_id, expected_edit_version=target.edit_version,
                operation_id=uuid4(), reason="Synthetic continuity regression")
        self.assertEqual(self.state(), before)

    def test_command_prompts_both_users_without_exposing_credentials(self):
        module = "activities.management.commands.transfer_activity_administrator"
        output = StringIO()
        with patch(module + ".sys.stdin.isatty", return_value=True), patch(module + ".getpass",
                side_effect=[self.password, self.successor_password]) as prompt:
            call_command("transfer_activity_administrator", operation_id=self.plan["operation_id"],
                source_email=self.source.email, successor_email=self.successor.email, stdout=output)
        self.assertEqual(prompt.call_count, 2)
        self.assertIn("Transfer recorded", output.getvalue())
        for secret in (self.password, self.successor_password, self.source.email, self.successor.email):
            self.assertNotIn(secret, output.getvalue())

    def test_disabled_invalid_or_noninteractive_command_does_not_prompt(self):
        module = "activities.management.commands.transfer_activity_administrator"
        for overrides in ({"ACTIVITY_ADMIN_TRANSFER_ENABLED": False}, {"ACTIVITY_ADMIN_TRANSFER_PLANS": ()}):
            with override_settings(**overrides), patch(module + ".getpass") as prompt:
                with self.assertRaises(CommandError):
                    call_command("transfer_activity_administrator", operation_id=self.plan["operation_id"],
                        source_email=self.source.email, successor_email=self.successor.email)
                prompt.assert_not_called()
        with patch(module + ".sys.stdin.isatty", return_value=False), patch(module + ".getpass") as prompt:
            with self.assertRaises(CommandError):
                call_command("transfer_activity_administrator", operation_id=self.plan["operation_id"],
                    source_email=self.source.email, successor_email=self.successor.email)
            prompt.assert_not_called()

    def test_command_rejection_is_generic_and_never_echoes_credentials(self):
        module = "activities.management.commands.transfer_activity_administrator"
        output = StringIO()
        with patch(module + ".sys.stdin.isatty", return_value=True), patch(module + ".getpass",
                side_effect=[self.password, "Synthetic-wrong-password"]):
            with self.assertRaises(CommandError) as error:
                call_command("transfer_activity_administrator", operation_id=self.plan["operation_id"],
                    source_email=self.source.email, successor_email=self.successor.email, stdout=output)
        for secret in (self.password, "Synthetic-wrong-password"):
            self.assertNotIn(secret, str(error.exception) + output.getvalue())
        self.assertFalse(AuditEvent.objects.exists())
