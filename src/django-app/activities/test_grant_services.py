"""Grant commands: synthetic trusted authority fixtures, no bootstrap or real database."""

from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group, Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from activities import grant_services as commands
from activities.access_rules import ADMINISTRATIVE_PERMISSIONS, DELEGABLE_PERMISSIONS
from activities.models import Activity, ActivityPermissionGrant
from activities.test_history import traced_locks
from assessments.models import AuditEvent

User = get_user_model()


class GrantCommandTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user("grant-admin@example.com")
        cls.recipient = User.objects.create_user("grant-recipient@example.com")
        cls.other_admin = User.objects.create_user("other-grant-admin@example.com")
        cls.activity = Activity.objects.create(name="A", training_date=timezone.localdate(), status="active", created_by=cls.admin)
        cls.activity_b = Activity.objects.create(name="B", training_date=timezone.localdate(), status="active", created_by=cls.admin)
        cls.registry = {}
        for name in DELEGABLE_PERMISSIONS | ADMINISTRATIVE_PERMISSIONS:
            app, code = name.split(".")
            cls.registry[name] = Permission.objects.get(content_type__app_label=app, codename=code)
        cls.permission = cls.registry["assessments.add_assessment"]
        cls.authority(cls.admin, DELEGABLE_PERMISSIONS | ADMINISTRATIVE_PERMISSIONS)
        cls.authority(cls.other_admin, DELEGABLE_PERMISSIONS | ADMINISTRATIVE_PERMISSIONS)
        cls.recipient.user_permissions.add(*(cls.registry[name] for name in DELEGABLE_PERMISSIONS))

    @classmethod
    def authority(cls, user, names, activity=None, direct=True):
        for name in names:
            permission = cls.registry[name]
            if direct:
                user.user_permissions.add(permission)
            ActivityPermissionGrant.objects.create(user=user, activity=activity or cls.activity,
                permission=permission, created_by=cls.admin)

    def call(self, command=commands.issue_grant, **changes):
        expected = changes.get("expected_edit_version")
        if "expected_edit_version" not in changes:
            expected = 0 if command == commands.issue_grant else self.row().edit_version
        values = dict(actor=self.admin, activity_id=self.activity.pk, recipient_id=self.recipient.pk,
            permission_id=self.permission.pk, expected_edit_version=expected,
            operation_id=uuid4(), reason="Approved synthetic change")
        values.update(changes)
        return command(**values)

    def row(self):
        return ActivityPermissionGrant.objects.get(user=self.recipient, activity=self.activity, permission=self.permission)

    def state(self):
        return (list(ActivityPermissionGrant.objects.order_by("pk").values()), list(AuditEvent.objects.order_by("pk").values()))

    def test_lifecycle_reuses_identity_and_emits_one_activity_audit_per_mutation(self):
        first = self.call()
        creator = self.row().created_by_id
        second = self.call(commands.revoke_grant)
        third = self.call(commands.reactivate_grant)
        self.assertEqual([first.grant_id, second.grant_id, third.grant_id], [first.grant_id] * 3)
        self.assertEqual([first.edit_version, second.edit_version, third.edit_version], [1, 2, 3])
        self.assertEqual([first.is_active, second.is_active, third.is_active], [True, False, True])
        self.assertEqual(self.row().created_by_id, creator)
        events = list(AuditEvent.objects.order_by("pk"))
        self.assertEqual(len(events), 3)
        for event, receipt in zip(events, (first, second, third)):
            self.assertEqual(event.pk, receipt.audit_event_id)
            self.assertEqual(event.activity_id, self.activity.pk)
            self.assertEqual(event.actor_user_id, self.admin.pk)
            self.assertEqual(event.actor_kind, "user")
            self.assertIsNone(event.session_id)
            self.assertIsNone(event.participation_id)
            self.assertIsNone(event.assessment_id)
            self.assertEqual(event.version_after, receipt.edit_version)

    def test_permission_without_scope_and_scope_without_direct_permission_are_denied(self):
        issuer = self.registry["activities.issue_activitypermissiongrant"]
        self.admin.user_permissions.remove(issuer)
        with self.assertRaises(PermissionDenied):
            self.call()
        self.admin.user_permissions.add(issuer)
        scope = ActivityPermissionGrant.objects.get(user=self.admin, activity=self.activity, permission=issuer)
        scope.is_active = False
        scope.save()
        with self.assertRaises(PermissionDenied):
            self.call()
        self.assertFalse(AuditEvent.objects.exists())

    def test_group_staff_and_superuser_are_not_administrative_authority(self):
        group = Group.objects.create(name="Synthetic full permissions")
        group.permissions.add(*self.registry.values())
        for superuser in (False, True):
            actor = User.objects.create_user(str(uuid4()) + "@example.com", is_staff=True, is_superuser=superuser)
            actor.groups.add(group)
            self.authority(actor, DELEGABLE_PERMISSIONS | ADMINISTRATIVE_PERMISSIONS, direct=False)
            self.assertTrue(actor.has_perm("activities.issue_activitypermissiongrant"))
            with self.subTest(superuser=superuser), self.assertRaises(PermissionDenied):
                self.call(actor=actor)

    def test_inactive_deleted_and_untrusted_actor_are_denied(self):
        dead = User.objects.create_user("deleted-issuer@example.com")
        dead.delete()
        for actor in (None, AnonymousUser(), self.admin.pk, {"id": self.admin.pk}, User(pk=self.admin.pk), dead):
            with self.subTest(actor_type=type(actor).__name__), self.assertRaises(PermissionDenied):
                self.call(actor=actor)
        User.objects.filter(pk=self.admin.pk).update(is_active=False)
        with self.assertRaises(PermissionDenied):
            self.call()

    def test_self_changes_are_denied_for_all_commands(self):
        for command in (commands.issue_grant, commands.revoke_grant, commands.reactivate_grant):
            with self.subTest(command=command.__name__), self.assertRaises(PermissionDenied):
                self.call(command, recipient_id=self.admin.pk, expected_edit_version=0 if command == commands.issue_grant else 1)

    def test_cross_activity_is_denied_even_for_activity_creator(self):
        with self.assertRaises(PermissionDenied):
            self.call(activity_id=self.activity_b.pk)

    def test_delegation_requires_own_global_and_scoped_draft_permission(self):
        self.admin.user_permissions.remove(self.permission)
        with self.assertRaises(PermissionDenied):
            self.call()
        self.admin.user_permissions.add(self.permission)
        row = ActivityPermissionGrant.objects.get(user=self.admin, activity=self.activity, permission=self.permission)
        row.is_active = False
        row.save()
        with self.assertRaises(PermissionDenied):
            self.call()

    def test_administrative_and_unregistered_permissions_cannot_be_delegated(self):
        bad = Permission.objects.get(content_type__app_label="accounts", codename="change_user")
        for permission in [bad] + [self.registry[name] for name in ADMINISTRATIVE_PERMISSIONS]:
            with self.subTest(permission=permission.codename), self.assertRaises(PermissionDenied):
                self.call(permission_id=permission.pk)

    def test_recipient_must_be_active_and_globally_eligible_for_activation(self):
        self.recipient.user_permissions.clear()
        with self.assertRaises(PermissionDenied):
            self.call()
        self.recipient.user_permissions.add(self.permission)
        User.objects.filter(pk=self.recipient.pk).update(is_active=False)
        with self.assertRaises(PermissionDenied):
            self.call()

    def test_revoke_can_close_inactive_recipient_without_global_permission(self):
        self.call()
        self.recipient.user_permissions.clear()
        User.objects.filter(pk=self.recipient.pk).update(is_active=False)
        receipt = self.call(commands.revoke_grant)
        self.assertFalse(receipt.is_active)
        self.assertFalse(self.row().is_active)
        self.assertEqual(self.row().edit_version, 2)

    def test_revoke_does_not_require_issuer_to_hold_target_permission(self):
        self.call()
        self.admin.user_permissions.remove(self.permission)
        self.assertFalse(self.call(commands.revoke_grant).is_active)

    def test_reactivation_enforces_delegation_again(self):
        self.call()
        self.call(commands.revoke_grant)
        self.admin.user_permissions.remove(self.permission)
        with self.assertRaises(PermissionDenied):
            self.call(commands.reactivate_grant)
        self.assertFalse(self.row().is_active)

    def test_versions_and_invalid_transitions_do_not_mutate(self):
        self.call()
        before = self.state()
        for command, changes in ((commands.issue_grant, {}),
                (commands.reactivate_grant, {}), (commands.revoke_grant, {"expected_edit_version": 9})):
            with self.subTest(command=command.__name__), self.assertRaises(ValidationError):
                self.call(command, **changes)
            self.assertEqual(self.state(), before)
        self.call(commands.revoke_grant)
        before = self.state()
        with self.assertRaises(ValidationError):
            self.call(commands.revoke_grant)
        self.assertEqual(self.state(), before)

    def test_required_fields_are_strict(self):
        for changes in ({"expected_edit_version": True}, {"expected_edit_version": 1},
                        {"operation_id": None}, {"operation_id": "invalid"}, {"reason": "  "},
                        {"reason": None}, {"recipient_id": True}, {"activity_id": "1"}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                self.call(**changes)
        with self.assertRaises(TypeError):
            commands.issue_grant(actor=self.admin)
        self.assertFalse(AuditEvent.objects.exists())

    def test_identical_retries_return_historical_receipts_for_all_commands(self):
        for command, expected in ((commands.issue_grant, 0), (commands.revoke_grant, 1), (commands.reactivate_grant, 2)):
            operation = uuid4()
            first = self.call(command, operation_id=operation, expected_edit_version=expected)
            before = self.state()
            second = self.call(command, operation_id=str(operation), expected_edit_version=expected)
            self.assertEqual(first, second)
            self.assertEqual(self.state(), before)

    def test_old_issue_receipt_after_later_revoke_does_not_reactivate(self):
        operation = uuid4()
        first = self.call(operation_id=operation)
        self.call(commands.revoke_grant)
        before = self.state()
        self.assertEqual(self.call(operation_id=operation), first)
        self.assertFalse(self.row().is_active)
        self.assertEqual(self.state(), before)

    def test_historical_receipt_survives_recipient_ineligibility(self):
        operation = uuid4()
        first = self.call(operation_id=operation)
        User.objects.filter(pk=self.recipient.pk).update(is_active=False)
        self.recipient.user_permissions.clear()
        before = self.state()
        self.assertEqual(self.call(operation_id=operation), first)
        self.assertEqual(self.state(), before)

    def test_recipient_group_permissions_remain_valid_for_operational_use(self):
        self.recipient.user_permissions.clear()
        group = Group.objects.create(name="Synthetic draft capability")
        group.permissions.add(self.permission)
        self.recipient.groups.add(group)
        self.assertTrue(self.call().is_active)

    def test_uuid_cannot_be_reused_for_different_commands(self):
        operation = uuid4()
        self.call(operation_id=operation)
        for changes in ({"reason": "Different"}, {"recipient_id": self.other_admin.pk},
                        {"actor": self.other_admin}, {"permission_id": self.registry["assessments.change_assessment"].pk}):
            before = self.state()
            with self.subTest(changes=changes), self.assertRaises(commands.OperationConflict):
                self.call(operation_id=operation, **changes)
            self.assertEqual(self.state(), before)
        with self.assertRaises(commands.OperationConflict):
            self.call(commands.revoke_grant, operation_id=operation)

    def test_uuid_collision_between_authorized_activities_is_rejected(self):
        self.authority(self.admin, DELEGABLE_PERMISSIONS | ADMINISTRATIVE_PERMISSIONS, activity=self.activity_b)
        operation = uuid4()
        self.call(operation_id=operation)
        before = self.state()
        with self.assertRaises(commands.OperationConflict):
            self.call(operation_id=operation, activity_id=self.activity_b.pk)
        self.assertEqual(self.state(), before)

    def test_replay_is_denied_after_administrative_revocation(self):
        operation = uuid4()
        self.call(operation_id=operation)
        permission = self.registry["activities.issue_activitypermissiongrant"]
        row = ActivityPermissionGrant.objects.get(user=self.admin, activity=self.activity, permission=permission)
        row.is_active = False
        row.save()
        before = self.state()
        with self.assertRaises(PermissionDenied):
            self.call(operation_id=operation)
        self.assertEqual(self.state(), before)

    def test_stale_permission_cache_does_not_authorize_replay(self):
        operation = uuid4()
        self.call(operation_id=operation)
        self.assertTrue(self.admin.has_perm("activities.issue_activitypermissiongrant"))
        self.admin.user_permissions.remove(self.registry["activities.issue_activitypermissiongrant"])
        with self.assertRaises(PermissionDenied):
            self.call(operation_id=operation)

    def test_audit_failure_rolls_back_every_transition_and_version(self):
        before = self.state()
        with patch.object(commands, "_append_audit", side_effect=RuntimeError("synthetic audit failure")) as writer:
            with self.assertRaises(RuntimeError):
                self.call()
            writer.assert_called_once()
        self.assertEqual(self.state(), before)
        self.call()
        for command in (commands.revoke_grant, commands.reactivate_grant):
            if command == commands.reactivate_grant:
                self.call(commands.revoke_grant)
            before = self.state()
            with patch.object(commands, "_append_audit", side_effect=RuntimeError("synthetic audit failure")):
                with self.assertRaises(RuntimeError):
                    self.call(command)
            self.assertEqual(self.state(), before)

    def test_actual_audit_insert_failure_also_rolls_back(self):
        before = self.state()
        with patch.object(AuditEvent, "save", side_effect=IntegrityError("synthetic insert failure")) as writer:
            with self.assertRaises(IntegrityError):
                self.call()
            writer.assert_called_once()
        self.assertEqual(self.state(), before)

    def test_collision_at_audit_insert_rolls_back_domain_write(self):
        occupied = uuid4()
        self.call(operation_id=occupied)
        self.recipient = User.objects.create_user("collision-recipient@example.com")
        self.recipient.user_permissions.add(self.permission)
        original = commands._append_audit
        def collide(using, **values):
            values["operation_id"] = occupied
            return original(using, **values)
        before = self.state()
        with patch.object(commands, "_append_audit", side_effect=collide):
            with self.assertRaises(commands.OperationConflict):
                self.call()
        self.assertEqual(self.state(), before)

    def test_final_authorization_rejects_revocation_after_precheck(self):
        original = commands._authorize
        def change_after_first_check(*args, **kwargs):
            result = original(*args, **kwargs)
            if "grants" not in kwargs and len(args) == 6:
                self.admin.user_permissions.remove(self.registry["activities.issue_activitypermissiongrant"])
            return result
        before = self.state()
        with patch.object(commands, "_authorize", side_effect=change_after_first_check):
            with self.assertRaises(PermissionDenied):
                self.call()
        self.assertEqual(self.state(), before)

    def test_lock_order_and_no_join_locks(self):
        with traced_locks() as trace:
            self.call()
        self.assertEqual(list(dict.fromkeys(t[0] for t in trace)), ["Activity", "User", "ActivityPermissionGrant"])
        self.assertTrue(all(not entry[2] for entry in trace))
        for model, identifiers, joins in trace:
            self.assertEqual(identifiers, sorted(identifiers))

    def test_closed_activity_disallows_activation_but_allows_revocation(self):
        self.call()
        self.activity.status = "closed"
        self.activity.save()
        self.call(commands.revoke_grant)
        with self.assertRaises(ValidationError):
            self.call(commands.reactivate_grant)
        with self.assertRaises(ValidationError):
            self.call(permission_id=self.registry["assessments.change_assessment"].pk)

    def test_local_last_administrator_guard_is_not_bypassed_by_partial_revoker(self):
        # Remove the second full mandate through trusted fixtures, not a business workflow.
        for row in ActivityPermissionGrant.objects.filter(user=self.other_admin):
            row.is_active = False
            row.save()
        revoker = User.objects.create_user("partial-revoker@example.com")
        self.authority(revoker, {"activities.revoke_activitypermissiongrant"})
        target = ActivityPermissionGrant.objects.get(user=self.admin, activity=self.activity, permission=self.permission)
        before = self.state()
        with self.assertRaises(ValidationError):
            self.call(commands.revoke_grant, actor=revoker, recipient_id=self.admin.pk, expected_edit_version=target.edit_version)
        self.assertEqual(self.state(), before)

    def test_snapshot_allowlist_and_services_do_not_modify_global_permissions(self):
        direct_before = list(User.user_permissions.through.objects.order_by("pk").values())
        receipt = self.call()
        event = AuditEvent.objects.get(pk=receipt.audit_event_id)
        self.assertEqual(set(event.before_state), {"schema_version", "grant", "permission"})
        self.assertEqual(set(event.after_state), {"schema_version", "grant", "permission", "grant_command_sha256", "grant_command_result"})
        rendered = str(event.after_state).lower()
        for secret in ("password", "secret_hash", "session_key", "cookie", "email"):
            self.assertNotIn(secret, rendered)
        self.assertEqual(list(User.user_permissions.through.objects.order_by("pk").values()), direct_before)


class GrantCommandMigrationTests(TransactionTestCase):
    def test_permission_metadata_migration_preserves_grant_state(self):
        before = ("activities", "0003_activity_permission_grant")
        after = ("activities", "0004_grant_management_permissions")
        executor = MigrationExecutor(connection)
        executor.migrate([before])
        try:
            old = executor.loader.project_state([before]).apps
            user = old.get_model("accounts", "User").objects.create(email="grant-migration@example.com")
            activity = old.get_model("activities", "Activity").objects.create(name="Historical", training_date=timezone.localdate(), created_by_id=user.pk)
            ct, _ = old.get_model("contenttypes", "ContentType").objects.get_or_create(app_label="assessments", model="assessment")
            permission, _ = old.get_model("auth", "Permission").objects.get_or_create(content_type_id=ct.pk, codename="add_assessment", defaults={"name": "Add assessment"})
            grant = old.get_model("activities", "ActivityPermissionGrant").objects.create(
                user_id=user.pk, activity_id=activity.pk, permission_id=permission.pk, created_by_id=user.pk,
                is_active=False, edit_version=7)
            values = type(grant).objects.filter(pk=grant.pk).values().get()
            MigrationExecutor(connection).migrate([after])
            new = MigrationExecutor(connection).loader.project_state([after]).apps
            model = new.get_model("activities", "ActivityPermissionGrant")
            self.assertEqual(model.objects.filter(pk=grant.pk).values().get(), values)
            self.assertEqual(model.objects.count(), 1)
            self.assertEqual({p[0] for p in model._meta.permissions}, {p.split(".")[1] for p in ADMINISTRATIVE_PERMISSIONS})
            MigrationExecutor(connection).migrate([before])
            self.assertEqual(type(grant).objects.filter(pk=grant.pk).values().get(), values)
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
