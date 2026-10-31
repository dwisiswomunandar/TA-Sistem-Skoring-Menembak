"""Scoped authorization and grant integrity using trusted synthetic fixtures."""

from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import Group, Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connection, models, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.models.deletion import ProtectedError
from django.test import TransactionTestCase
from django.utils import timezone

from activities.authorization import authorize_draft, draft_sessions
from activities.models import Activity, ActivityParticipation, ActivityPermissionGrant, TrainingSession
from assessments import services
from assessments.models import Assessment, AuditEvent
from assessments.test_services import ServiceTestCase, User


class ActivityAuthorizationTests(ServiceTestCase):
    def setUp(self):
        super().setUp()
        self.permission = Permission.objects.get(content_type__app_label="assessments", codename="add_assessment")
        self.other = self.grant(User.objects.create_user("other-assessor@example.com"))
        self.activity_b = Activity.objects.create(name="B", training_date=timezone.localdate(), status="active", created_by=self.actor)
        participation = ActivityParticipation.objects.create(
            activity=self.activity_b, participant=self.session.participation.participant,
            registration_status="approved", participant_identifier="B-001", decided_by=self.actor, decided_at=timezone.now())
        self.session_b = self.session_for(participation, self.actor)
        self.other_session = self.session_for(self.session.participation, self.other, number=2)

    def session_for(self, participation, actor, number=1):
        return TrainingSession.objects.create(participation=participation, session_number=number,
            assigned_assessor=actor, created_by=self.actor, weapon_type="pistol", shooting_position="berdiri",
            distance_m=10, caliber_mm=9)

    def check(self, **options):
        return authorize_draft(**{"actor": self.actor, "action": "assessment.draft_created",
                                  "session_id": self.session.pk, **options})

    def deactivate(self, permission=None):
        row = ActivityPermissionGrant.objects.get(user=self.actor, activity=self.session.participation.activity,
                                                  permission=permission or self.permission)
        row.is_active = False
        row.save()
        return row

    def test_permission_without_grant_is_denied(self):
        self.deactivate()
        with self.assertRaises(PermissionDenied):
            self.command(services.create_initial_draft)

    def test_grant_without_global_permission_is_denied(self):
        self.actor.user_permissions.clear()
        with self.assertRaises(PermissionDenied):
            self.command(services.create_initial_draft)

    def test_cross_activity_even_if_assigned_and_creator_is_denied(self):
        with self.assertRaises(PermissionDenied):
            self.command(services.create_initial_draft, session_id=self.session_b.pk)

    def test_cross_session_even_with_activity_grants_is_denied(self):
        with self.assertRaises(PermissionDenied):
            self.command(services.create_initial_draft, session_id=self.other_session.pk)

    def test_staff_and_superuser_do_not_bypass_grants_or_assignment(self):
        for superuser in (False, True):
            actor = User.objects.create_user(str(uuid4()) + "@example.com", is_staff=True, is_superuser=superuser)
            actor.user_permissions.add(self.permission)
            assigned = self.session_for(self.session.participation, actor, number=3 + int(superuser))
            with self.subTest(superuser=superuser), self.assertRaises(PermissionDenied):
                self.command(services.create_initial_draft, actor=actor, session_id=assigned.pk)
            ActivityPermissionGrant.objects.create(user=actor, activity=self.session.participation.activity,
                                                   permission=self.permission, created_by=self.actor)
            with self.assertRaises(PermissionDenied):
                self.command(services.create_initial_draft, actor=actor)

    def test_selector_excludes_foreign_activity_and_unassigned_sessions(self):
        rows = draft_sessions(actor=self.actor, action="assessment.draft_created")
        self.assertEqual(list(rows.values_list("pk", flat=True)), [self.session.pk])
        self.deactivate()
        self.assertFalse(draft_sessions(actor=self.actor, action="assessment.draft_created").exists())

    def test_unknown_actions_and_untrusted_identifiers_fail_closed(self):
        for options in ({"action": []}, {"action": {}}, {"action": "assessment.lock"}, {"session_id": True}, {"session_id": "1"},
                        {"actor": self.actor.pk}, {"session_id": 999999}):
            with self.subTest(options=options), self.assertRaises(PermissionDenied):
                self.check(**options)

    def test_inactive_user_and_stale_permission_cache_are_denied(self):
        self.assertTrue(self.actor.has_perm("assessments.add_assessment"))
        self.actor.user_permissions.clear()
        with self.assertRaises(PermissionDenied):
            self.check()
        User.objects.filter(pk=self.other.pk).update(is_active=False)
        with self.assertRaises(PermissionDenied):
            self.check(actor=self.other, session_id=self.other_session.pk)

    def test_group_permission_revocation_ignores_stale_caller_cache(self):
        self.actor.user_permissions.clear()
        group = Group.objects.create(name="Synthetic operators")
        group.permissions.add(self.permission)
        self.actor.groups.add(group)
        self.assertTrue(self.actor.has_perm("assessments.add_assessment"))
        self.check()
        group.permissions.clear()
        with self.assertRaises(PermissionDenied):
            self.check()

    def test_retry_after_grant_revocation_does_not_return_receipt(self):
        operation_id = uuid4()
        self.command(services.create_initial_draft, operation_id=operation_id)
        before = self.database_state()
        self.deactivate()
        with self.assertRaises(PermissionDenied):
            self.command(services.create_initial_draft, operation_id=operation_id)
        self.assertEqual(self.database_state(), before)

    def test_final_check_rejects_grant_revoked_after_precheck(self):
        original = services.lock_session_reference
        def revoke_then_lock(*args):
            self.deactivate()
            return original(*args)
        with patch.object(services, "lock_session_reference", side_effect=revoke_then_lock):
            with self.assertRaises(PermissionDenied):
                self.command(services.create_initial_draft)
        self.assertFalse(Assessment.objects.exists())
        self.assertFalse(AuditEvent.objects.exists())

    def test_all_five_commands_recheck_action_grants_and_keep_versions(self):
        receipt = self.command(services.create_initial_draft)
        assessment = Assessment.objects.get(pk=receipt.assessment_id)
        self.command(services.update_draft, assessment=assessment, data={"notes": "Allowed"})
        self.command(services.update_draft, assessment=assessment, data={"photo_id": self.make_photo().pk})
        receipt = self.command(services.add_shot_point, assessment=assessment, data={"x_px": 2, "y_px": 3})
        from assessments.models import ShotPoint
        point = ShotPoint.objects.get(pk=receipt.point_id)
        for function, permission, data in (
            (services.update_draft, "change_assessment", {"notes": "Denied"}),
            (services.add_shot_point, "add_shotpoint", {"x_px": 4, "y_px": 5}),
            (services.update_shot_point, "change_shotpoint", {"x_px": 4}),
            (services.delete_shot_point, "delete_shotpoint", None),
        ):
            row = self.deactivate(Permission.objects.get(content_type__app_label="assessments", codename=permission))
            before = self.database_state()
            kwargs = {"assessment": assessment}
            if function in (services.update_shot_point, services.delete_shot_point):
                kwargs["point"] = point
            if data is not None:
                kwargs["data"] = data
            with self.subTest(function=function.__name__), self.assertRaises(PermissionDenied):
                self.command(function, **kwargs)
            self.assertEqual(self.database_state(), before)
            row.is_active = True
            row.save()
        self.command(services.update_shot_point, assessment=assessment, point=point, data={"x_px": 4})
        self.command(services.delete_shot_point, assessment=assessment, point=point)
        assessment.refresh_from_db()
        self.assertEqual(assessment.edit_version, 6)
        self.assertEqual(AuditEvent.objects.count(), 6)

    def test_assignment_change_before_history_revokes_old_operator(self):
        row = TrainingSession.objects.get(pk=self.session.pk)
        row.assigned_assessor = self.other
        row.save()
        with self.assertRaises(PermissionDenied):
            self.check()
        self.check(actor=self.other)

    def test_assignment_change_after_audit_remains_blocked(self):
        self.command(services.create_initial_draft)
        row = TrainingSession.objects.get(pk=self.session.pk)
        row.assigned_assessor = self.other
        with self.assertRaises(ValidationError):
            row.save()

    def test_grant_tokens_identity_and_public_orm_guards(self):
        row = self.deactivate()
        stale = ActivityPermissionGrant.objects.get(pk=row.pk)
        row.is_active = True
        row.save()
        with self.assertRaises(ValidationError):
            stale.save()
        row.user = self.other
        with self.assertRaises(ValidationError):
            row.save()
        for action in (lambda: row.delete(), lambda: ActivityPermissionGrant.objects.update(is_active=True),
                       lambda: ActivityPermissionGrant.objects.bulk_create([])):
            with self.assertRaises(ValidationError):
                action()

    def test_grant_validation_constraints_and_protect(self):
        row = ActivityPermissionGrant.objects.get(user=self.actor, activity=self.session.participation.activity,
                                                  permission=self.permission)
        with self.assertRaises(ValidationError):
            ActivityPermissionGrant.objects.create(user=self.actor, activity=row.activity,
                permission=self.permission, created_by=self.actor)
        bad = Permission.objects.get(content_type__app_label="accounts", codename="change_user")
        with self.assertRaises(ValidationError):
            ActivityPermissionGrant.objects.create(user=self.actor, activity=row.activity, permission=bad, created_by=self.actor)
        with self.assertRaises(IntegrityError), transaction.atomic():
            models.QuerySet(model=ActivityPermissionGrant).filter(pk=row.pk).update(edit_version=0)
        with self.assertRaises(IntegrityError), transaction.atomic():
            models.QuerySet(model=ActivityPermissionGrant).bulk_create([ActivityPermissionGrant(
                user=row.user, activity=row.activity, permission=row.permission, created_by=row.created_by)])
        for parent in (row.user, row.activity, row.permission):
            with self.subTest(parent=type(parent).__name__), self.assertRaises(ProtectedError):
                parent.delete()

    def test_partial_writes_and_missing_row_resurrection_are_rejected(self):
        row = self.deactivate()
        with self.assertRaises(ValidationError):
            row.save(update_fields=["is_active"])
        models.QuerySet(model=ActivityPermissionGrant).filter(pk=row.pk).delete()
        row.is_active = True
        with self.assertRaises(ValidationError):
            row.save()
        self.assertFalse(ActivityPermissionGrant.objects.filter(pk=row.pk).exists())


class GrantMigrationTests(TransactionTestCase):
    def test_additive_migration_preserves_history_and_creates_no_implicit_grants(self):
        before = ("activities", "0002_historical_parent_managers")
        after = ("activities", "0003_activity_permission_grant")
        executor = MigrationExecutor(connection)
        executor.migrate([before])
        try:
            old = executor.loader.project_state([before]).apps
            user = old.get_model("accounts", "User").objects.create(email="migration-grant@example.com", is_superuser=True)
            activity = old.get_model("activities", "Activity").objects.create(
                name="Historical", training_date=timezone.localdate(), created_by_id=user.pk)
            values = type(activity).objects.filter(pk=activity.pk).values().get()
            MigrationExecutor(connection).migrate([after])
            current = MigrationExecutor(connection).loader.project_state([after]).apps
            self.assertFalse(current.get_model("activities", "ActivityPermissionGrant").objects.exists())
            self.assertEqual(current.get_model("activities", "Activity").objects.filter(pk=activity.pk).values().get(), values)
            MigrationExecutor(connection).migrate([before])
            self.assertEqual(type(activity).objects.filter(pk=activity.pk).values().get(), values)
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
