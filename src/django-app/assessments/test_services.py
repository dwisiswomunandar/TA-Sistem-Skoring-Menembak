"""Draft command security/atomicity. Synthetic fixtures, no scorer or real DBMS."""

from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Permission
from django.core.exceptions import PermissionDenied, ValidationError

from activities.models import ActivityPermissionGrant, TrainingSession
from activities.test_history import traced_locks
from assessments import services
from assessments.models import Assessment, AuditEvent, ShotPoint
from assessments.test_evidence import RESULT
from assessments.test_results import ResultTestCase

User = get_user_model()


class ServiceTestCase(ResultTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.grant(cls.actor)

    @classmethod
    def grant(cls, actor, codes=None):
        codes = codes or {name.split(".")[1] for values in services.PERMISSIONS.values() for name in values}
        actor.user_permissions.add(*Permission.objects.filter(content_type__app_label="assessments", codename__in=codes))
        for permission in Permission.objects.filter(content_type__app_label="assessments", codename__in=codes):
            ActivityPermissionGrant.objects.get_or_create(user=actor, activity=cls.session.participation.activity,
                                                        permission=permission, defaults={"created_by": cls.actor})
        return actor

    def command(self, function, *, assessment=None, point=None, **overrides):
        values = {"actor": self.actor, "session_id": self.session.pk, "operation_id": uuid4(),
                  "expected_edit_version": 0, "reason": "Synthetic command reason"}
        if function != services.delete_shot_point:
            values["data"] = {}
        if assessment is not None:
            assessment.refresh_from_db()
            values.update(assessment_id=assessment.pk, expected_edit_version=assessment.edit_version)
        if point is not None:
            values["point_id"] = point.pk
        values.update(overrides)
        return function(**values)

    def database_state(self):
        return {model.__name__: list(model.objects.order_by("pk").values())
                for model in (Assessment, ShotPoint, AuditEvent, TrainingSession)}

    def assert_audited(self, receipt, *, action, previous_version):
        event = AuditEvent.objects.get(pk=receipt.audit_event_id)
        self.assertEqual(event.operation_id, receipt.operation_id)
        self.assertEqual(event.action, action)
        self.assertEqual(event.actor_kind, "user")
        self.assertEqual(event.actor_user_id, self.actor.pk)
        self.assertIsNone(event.actor_participation_id)
        self.assertEqual(event.session_id, self.session.pk)
        self.assertEqual(event.assessment_id, receipt.assessment_id)
        self.assertEqual(event.version_before, previous_version)
        self.assertEqual(event.version_after, receipt.edit_version)
        self.assertEqual(event.after_state["assessment"]["edit_version"], receipt.edit_version)
        return event


class DraftCommandTests(ServiceTestCase):
    def test_initial_draft_creation_is_unscored_and_audited(self):
        receipt = self.command(services.create_initial_draft, data={"notes": " Draft notes "})
        assessment = Assessment.objects.get(pk=receipt.assessment_id)
        event = self.assert_audited(receipt, action="assessment.draft_created", previous_version=None)
        self.assertEqual((assessment.status, assessment.revision_number, assessment.edit_version), ("draft", 1, 1))
        self.assertEqual(assessment.created_by_id, self.actor.pk)
        self.assertEqual(assessment.notes, "Draft notes")
        self.assertIsNone(assessment.total_score)
        self.assertIsNone(event.before_state["assessment"])
        self.assertEqual(AuditEvent.objects.count(), 1)

    def test_draft_update_increments_once_and_records_before_after(self):
        assessment = self.complete_draft()
        receipt = self.command(services.update_draft, assessment=assessment, data={"notes": "Updated"})
        event = self.assert_audited(receipt, action="assessment.draft_updated", previous_version=1)
        self.assertEqual(receipt.edit_version, 2)
        self.assertEqual(event.before_state["assessment"]["notes"], "")
        self.assertEqual(event.after_state["assessment"]["notes"], "Updated")

    def test_manual_point_add_uses_server_number_and_provenance(self):
        assessment = self.complete_draft()
        first = self.command(services.add_shot_point, assessment=assessment, data={"x_px": 1.1, "y_px": "2.25"})
        second = self.command(services.add_shot_point, assessment=assessment, data={"x_px": "3", "y_px": "4"})
        point = ShotPoint.objects.get(pk=first.point_id)
        self.assertEqual((point.point_number, point.source, point.origin_detection_id), (1, "manual", ""))
        self.assertEqual(point.x_px, Decimal("1.1"))
        self.assertIsNone(point.score)
        self.assertEqual(ShotPoint.objects.get(pk=second.point_id).point_number, 2)
        self.assertEqual((first.edit_version, second.edit_version), (2, 3))
        self.assert_audited(first, action="shotpoint.added", previous_version=1)
        self.assert_audited(second, action="shotpoint.added", previous_version=2)

    def test_point_move_and_exclusion_have_one_version_step_each(self):
        assessment = self.complete_draft()
        point = self.make_point(assessment)
        moved = self.command(services.update_shot_point, assessment=assessment, point=point,
                             data={"x_px": "12.125", "y_px": 15})
        excluded = self.command(services.update_shot_point, assessment=assessment, point=point,
                                data={"is_included": False, "exclusion_reason": "Synthetic exclusion"})
        self.assertEqual((moved.edit_version, excluded.edit_version), (3, 4))
        point.refresh_from_db()
        self.assertEqual(point.x_px, Decimal("12.125"))
        self.assertFalse(point.is_included)
        self.assertIsNone(point.score)
        event = self.assert_audited(excluded, action="shotpoint.updated", previous_version=3)
        self.assertTrue(event.before_state["shot_points"][0]["is_included"])
        self.assertFalse(event.after_state["shot_points"][0]["is_included"])

    def test_edit_existing_ai_point_preserves_origin(self):
        run = self.finish_run(result=RESULT)
        assessment = self.make_assessment(photo=run.photo, detection_run=run)
        point = self.make_point(assessment, source="ai", origin_detection_id="synthetic-1")
        self.command(services.update_shot_point, assessment=assessment, point=point, data={"x_px": "11"})
        point.refresh_from_db()
        self.assertEqual((point.source, point.origin_detection_id), ("ai", "synthetic-1"))

    def test_point_delete_records_deleted_payload_and_updates_parent_once(self):
        assessment = self.complete_draft()
        point = self.make_point(assessment)
        receipt = self.command(services.delete_shot_point, assessment=assessment, point=point)
        self.assertEqual(receipt.point_id, point.pk)
        self.assertFalse(ShotPoint.objects.filter(pk=point.pk).exists())
        self.assertEqual(receipt.edit_version, 3)
        event = self.assert_audited(receipt, action="shotpoint.deleted", previous_version=2)
        self.assertEqual(event.before_state["shot_points"][0]["id"], point.pk)
        self.assertEqual(event.after_state["shot_points"], [])

    def test_all_cached_point_scores_and_parent_provenance_are_invalidated(self):
        assessment = self.complete_draft()
        first = self.make_point(assessment)
        second = self.make_point(assessment, point_number=2)
        assessment.refresh_from_db()
        self.raw(ShotPoint).filter(assessment_id=assessment.pk).update(score=1)
        self.raw(Assessment).filter(pk=assessment.pk).update(
            total_score=2, scoring_context={"schema_version": 1, "fixture_only": True},
            scoring_engine_version="unexecuted-cache-fixture", scoring_input_sha256="f" * 64,
            scored_at=assessment.updated_at, scored_edit_version=assessment.edit_version,
        )
        receipt = self.command(services.update_shot_point, assessment=assessment, point=first, data={"x_px": "13"})
        assessment.refresh_from_db()
        for field, empty in services.SCORING_FIELDS.items():
            self.assertEqual(getattr(assessment, field), empty)
        self.assertEqual(list(ShotPoint.objects.filter(pk__in=[first.pk, second.pk]).values_list("score", flat=True)), [None, None])
        self.assertEqual(receipt.edit_version, 4)
        event = AuditEvent.objects.get(pk=receipt.audit_event_id)
        self.assertEqual(event.before_state["assessment"]["total_score"], 2)
        self.assertIsNone(event.after_state["assessment"]["total_score"])

    def test_update_notes_also_invalidates_cached_values_without_extra_versions(self):
        assessment = self.complete_draft()
        point = self.make_point(assessment)
        self.raw(ShotPoint).filter(pk=point.pk).update(score=0)
        receipt = self.command(services.update_draft, assessment=assessment, data={"notes": "New note"})
        point.refresh_from_db()
        self.assertIsNone(point.score)
        self.assertEqual(receipt.edit_version, 3)

    def test_cross_session_and_cross_photo_evidence_are_rejected(self):
        assessment = self.complete_draft()
        other_session = TrainingSession.objects.create(
            participation=self.session.participation, session_number=2, assigned_assessor=self.actor,
            created_by=self.actor, weapon_type="pistol", shooting_position="berdiri", distance_m=10, caliber_mm=9,
        )
        other_photo = self.make_photo(session=other_session)
        alien_calibration = self.accept(self.make_calibration())
        for data in ({"photo_id": other_photo.pk}, {"calibration_id": alien_calibration.pk}, {"detection_run_id": self.make_run().pk}):
            before = self.database_state()
            with self.subTest(data=data), self.assertRaises(ValidationError):
                self.command(services.update_draft, assessment=assessment, data=data)
            self.assertEqual(self.database_state(), before)

    def test_switching_photo_with_points_rolls_back_cache_invalidation(self):
        assessment = self.complete_draft()
        point = self.make_point(assessment)
        self.raw(ShotPoint).filter(pk=point.pk).update(score=1)
        before = self.database_state()
        with self.assertRaises(ValidationError):
            self.command(services.update_draft, assessment=assessment,
                         data={"photo_id": self.make_photo().pk, "calibration_id": None})
        self.assertEqual(self.database_state(), before)

    def test_invalid_point_input_does_not_consume_version_or_operation_id(self):
        assessment = self.complete_draft()
        operation_id = uuid4()
        for data in ({"x_px": True, "y_px": 1}, {"x_px": "NaN", "y_px": 1},
                     {"x_px": "1.0000001", "y_px": 1}, {"x_px": 80, "y_px": 1},
                     {"x_px": 1, "y_px": 60}, {"x_px": 1},
                     {"x_px": 1, "y_px": 1, "is_included": False}):
            before = self.database_state()
            with self.subTest(data=data), self.assertRaises(ValidationError):
                self.command(services.add_shot_point, assessment=assessment, operation_id=operation_id, data=data)
            self.assertEqual(self.database_state(), before)
        receipt = self.command(services.add_shot_point, assessment=assessment, operation_id=operation_id, data={"x_px": 1, "y_px": 1})
        self.assertEqual(receipt.edit_version, 2)


class DraftAuthorizationTests(ServiceTestCase):
    def test_staff_flag_alone_does_not_authorize_commands(self):
        staff = User.objects.create_user("staff-only@example.com", is_staff=True)
        before = self.database_state()
        with self.assertRaises(PermissionDenied):
            self.command(services.create_initial_draft, actor=staff)
        self.assertEqual(self.database_state(), before)

    def test_assigned_assessor_without_permission_is_denied(self):
        self.actor.user_permissions.clear()
        with self.assertRaises(PermissionDenied):
            self.command(services.create_initial_draft)
        self.assertFalse(Assessment.objects.exists())

    def test_permissions_and_grants_do_not_bypass_assignment(self):
        operator = self.grant(User.objects.create_user("permission-granted@example.com"))
        self.assertFalse(operator.is_staff)
        self.assertNotEqual(operator.pk, self.session.assigned_assessor_id)
        with self.assertRaises(PermissionDenied):
            self.command(services.create_initial_draft, actor=operator)
        self.assertFalse(Assessment.objects.exists())

    def test_point_permission_without_parent_change_permission_is_insufficient(self):
        assessment = self.complete_draft()
        actor = self.grant(User.objects.create_user("point-only@example.com"), {"add_shotpoint"})
        with self.assertRaises(PermissionDenied):
            self.command(services.add_shot_point, actor=actor, assessment=assessment, data={"x_px": 1, "y_px": 1})
        self.assertFalse(ShotPoint.objects.exists())

    def test_each_point_action_requires_its_explicit_permission(self):
        assessment = self.complete_draft()
        point = self.make_point(assessment)
        actor = self.grant(User.objects.create_user("parent-only@example.com"), {"change_assessment"})
        for function, kwargs in (
            (services.add_shot_point, {"data": {"x_px": 1, "y_px": 1}}),
            (services.update_shot_point, {"point": point, "data": {"x_px": 2}}),
            (services.delete_shot_point, {"point": point}),
        ):
            before = self.database_state()
            with self.subTest(action=function.__name__), self.assertRaises(PermissionDenied):
                self.command(function, actor=actor, assessment=assessment, **kwargs)
            self.assertEqual(self.database_state(), before)

    def test_anonymous_unsaved_ids_and_payload_actors_are_rejected(self):
        for actor in (None, AnonymousUser(), self.actor.pk, {"id": self.actor.pk}, User(pk=self.actor.pk)):
            with self.subTest(actor_type=type(actor).__name__), self.assertRaises(PermissionDenied):
                self.command(services.create_initial_draft, actor=actor)
        self.assertFalse(Assessment.objects.exists())

    def test_inactive_and_deleted_user_are_not_trusted_from_cached_objects(self):
        for deleted in (False, True):
            actor = User.objects.create_user(str(uuid4()) + "@example.com")
            if deleted:
                actor.user_permissions.add(*Permission.objects.filter(content_type__app_label="assessments"))
                User.objects.filter(pk=actor.pk).delete()
            else:
                self.grant(actor)
                User.objects.filter(pk=actor.pk).update(is_active=False)
            with self.subTest(deleted=deleted), self.assertRaises(PermissionDenied):
                self.command(services.create_initial_draft, actor=actor)

    def test_stale_permission_cache_does_not_authorize_an_edit(self):
        assessment = self.complete_draft()
        self.assertTrue(self.actor.has_perm("assessments.change_assessment"))
        self.actor.user_permissions.clear()
        with self.assertRaises(PermissionDenied):
            self.command(services.update_draft, assessment=assessment, data={"notes": "Denied"})
        assessment.refresh_from_db()
        self.assertEqual(assessment.edit_version, 1)

    def test_client_cannot_set_actor_status_scores_or_provenance(self):
        assessment = self.complete_draft()
        for field, value in (("actor_user_id", self.actor.pk), ("actor_participation", 1), ("actor_kind", "system"),
                             ("status", "locked"), ("total_score", 0), ("edit_version", 99),
                             ("historical_context", {}), ("scoring_context", {}), ("created_by_id", self.actor.pk),
                             ("previous_assessment_id", assessment.pk)):
            before = self.database_state()
            with self.subTest(field=field), self.assertRaises(ValidationError):
                self.command(services.update_draft, assessment=assessment, data={field: value})
            self.assertEqual(self.database_state(), before)
        for field, value in (("source", "ai"), ("origin_detection_id", "claimed"), ("score", 0),
                             ("point_number", 20), ("assessment_id", assessment.pk)):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                self.command(services.add_shot_point, assessment=assessment, data={"x_px": 1, "y_px": 1, field: value})

    def test_cross_context_assessment_and_point_ids_cannot_be_used(self):
        assessment = self.complete_draft()
        point = self.make_point(assessment)
        other_session = TrainingSession.objects.create(
            participation=self.session.participation, session_number=2, assigned_assessor=self.actor,
            created_by=self.actor, weapon_type="pistol", shooting_position="berdiri", distance_m=10, caliber_mm=9,
        )
        other_assessment = self.make_assessment(session=other_session, photo=self.make_photo(session=other_session))
        other_point = self.make_point(other_assessment)
        before = self.database_state()
        with self.assertRaises(PermissionDenied):
            self.command(services.update_draft, assessment=assessment, session_id=999999, data={"notes": "Denied"})
        with self.assertRaises(ValidationError):
            self.command(services.update_shot_point, assessment=assessment, point_id=point.pk + 99999, data={"x_px": 2})
        with self.assertRaises(ValidationError):
            self.command(services.update_draft, assessment=assessment, assessment_id=other_assessment.pk, data={"notes": "Denied"})
        with self.assertRaises(ValidationError):
            self.command(services.update_shot_point, assessment=assessment, point=other_point, data={"x_px": 2})
        self.assertEqual(self.database_state(), before)


class DraftIdempotencyTests(ServiceTestCase):
    def test_identical_create_retry_returns_same_receipt_without_duplicate_rows(self):
        operation_id = uuid4()
        first = self.command(services.create_initial_draft, operation_id=operation_id)
        state = self.database_state()
        second = self.command(services.create_initial_draft, operation_id=str(operation_id), data={"photo_id": None})
        self.assertEqual(first, second)
        self.assertEqual(self.database_state(), state)

    def test_identical_update_retry_precedes_stale_version_check(self):
        assessment = self.complete_draft()
        operation_id = uuid4()
        first = self.command(services.update_draft, assessment=assessment, operation_id=operation_id,
                             expected_edit_version=1, data={"notes": "One edit"})
        second = self.command(services.update_draft, assessment=assessment, operation_id=operation_id,
                              expected_edit_version=1, data={"notes": " One edit "})
        self.assertEqual(first, second)
        assessment.refresh_from_db()
        self.assertEqual(assessment.edit_version, 2)
        self.assertEqual(AuditEvent.objects.count(), 1)

    def test_identical_add_retry_canonicalizes_decimal_input(self):
        assessment = self.complete_draft()
        operation_id = uuid4()
        first = self.command(services.add_shot_point, assessment=assessment, operation_id=operation_id,
                             expected_edit_version=1, data={"x_px": 1.5, "y_px": "2.250"})
        second = self.command(services.add_shot_point, assessment=assessment, operation_id=operation_id,
                              expected_edit_version=1, data={"x_px": Decimal("1.500000"), "y_px": "2.25"})
        self.assertEqual(first, second)
        self.assertEqual(ShotPoint.objects.count(), 1)
        self.assertEqual(AuditEvent.objects.count(), 1)

    def test_identical_delete_retry_does_not_require_deleted_point(self):
        assessment = self.complete_draft()
        point = self.make_point(assessment)
        operation_id = uuid4()
        first = self.command(services.delete_shot_point, assessment=assessment, point=point,
                             operation_id=operation_id, expected_edit_version=2)
        second = self.command(services.delete_shot_point, assessment=assessment, point=point,
                              operation_id=operation_id, expected_edit_version=2)
        self.assertEqual(first, second)
        self.assertEqual(AuditEvent.objects.count(), 1)

    def test_identical_point_update_retry_returns_the_committed_receipt(self):
        assessment = self.complete_draft()
        point = self.make_point(assessment)
        operation_id = uuid4()
        first = self.command(services.update_shot_point, assessment=assessment, point=point,
                             operation_id=operation_id, expected_edit_version=2, data={"x_px": 12.5})
        replay = self.command(services.update_shot_point, assessment=assessment, point=point,
                              operation_id=operation_id, expected_edit_version=2, data={"x_px": "12.500000"})
        self.assertEqual(first, replay)
        self.assertEqual(AuditEvent.objects.count(), 1)
        assessment.refresh_from_db()
        self.assertEqual(assessment.edit_version, 3)

    def test_operation_uuid_is_bound_globally_across_sessions(self):
        operation_id = uuid4()
        self.command(services.create_initial_draft, operation_id=operation_id)
        other_session = TrainingSession.objects.create(
            participation=self.session.participation, session_number=2, assigned_assessor=self.actor,
            created_by=self.actor, weapon_type="pistol", shooting_position="berdiri", distance_m=10, caliber_mm=9,
        )
        before = self.database_state()
        with self.assertRaises(services.OperationConflict):
            self.command(services.create_initial_draft, session_id=other_session.pk, operation_id=operation_id)
        self.assertEqual(self.database_state(), before)

    def test_same_uuid_with_different_payload_reason_or_version_is_rejected(self):
        assessment = self.complete_draft()
        operation_id = uuid4()
        self.command(services.update_draft, assessment=assessment, operation_id=operation_id,
                     expected_edit_version=1, data={"notes": "Original"})
        original = self.database_state()
        for changes in ({"data": {"notes": "Different"}}, {"reason": "Different reason"}, {"expected_edit_version": 2}):
            values = {"assessment": assessment, "operation_id": operation_id, "expected_edit_version": 1, "data": {"notes": "Original"}}
            values.update(changes)
            with self.subTest(changes=changes), self.assertRaises(services.OperationConflict):
                self.command(services.update_draft, **values)
            self.assertEqual(self.database_state(), original)

    def test_same_uuid_cannot_be_reused_by_another_actor_or_action(self):
        assessment = self.complete_draft()
        operation_id = uuid4()
        self.command(services.update_draft, assessment=assessment, operation_id=operation_id,
                     expected_edit_version=1, data={"notes": "Original"})
        other = self.grant(User.objects.create_user("other-command-actor@example.com"))
        with self.assertRaises(PermissionDenied):
            self.command(services.update_draft, actor=other, assessment=assessment, operation_id=operation_id,
                         expected_edit_version=1, data={"notes": "Original"})
        with self.assertRaises(services.OperationConflict):
            self.command(services.add_shot_point, assessment=assessment, operation_id=operation_id, data={"x_px": 1, "y_px": 1})
        self.assertEqual(AuditEvent.objects.count(), 1)

    def test_historical_receipt_after_later_edits_does_not_undo_current_state(self):
        assessment = self.complete_draft()
        operation_id = uuid4()
        first = self.command(services.update_draft, assessment=assessment, operation_id=operation_id,
                             expected_edit_version=1, data={"notes": "First"})
        self.command(services.update_draft, assessment=assessment, data={"notes": "Second"})
        replay = self.command(services.update_draft, assessment=assessment, operation_id=operation_id,
                              expected_edit_version=1, data={"notes": "First"})
        self.assertEqual(replay, first)
        assessment.refresh_from_db()
        self.assertEqual((assessment.notes, assessment.edit_version), ("Second", 3))

    def test_retry_after_permission_revocation_is_denied(self):
        operation_id = uuid4()
        self.command(services.create_initial_draft, operation_id=operation_id)
        self.actor.user_permissions.clear()
        with self.assertRaises(PermissionDenied):
            self.command(services.create_initial_draft, operation_id=operation_id)

    def test_missing_invalid_uuid_and_initial_version_are_rejected(self):
        for changes in ({"operation_id": None}, {"operation_id": "bad"}, {"operation_id": True},
                        {"expected_edit_version": True}, {"expected_edit_version": 1}, {"reason": " \t "}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                self.command(services.create_initial_draft, **changes)
        self.assertFalse(Assessment.objects.exists())
        self.assertFalse(AuditEvent.objects.exists())

    def test_database_uuid_collision_rolls_back_domain_changes(self):
        assessment = self.complete_draft()
        operation_id = uuid4()
        self.command(services.update_draft, assessment=assessment, operation_id=operation_id, data={"notes": "First"})
        state = self.database_state()
        # Simulate an event becoming visible after the initial lookup, without claiming a SQLite race test.
        with patch.object(services, "_replay", return_value=None):
            with self.assertRaises(services.OperationConflict):
                self.command(services.update_draft, assessment=assessment, operation_id=operation_id, data={"notes": "Second"})
        self.assertEqual(self.database_state(), state)


class DraftAtomicityTests(ServiceTestCase):
    def test_audit_failure_rolls_back_initial_creation(self):
        before = self.database_state()
        with patch.object(AuditEvent, "save", side_effect=ValidationError("Synthetic audit failure")) as audit:
            with self.assertRaises(ValidationError):
                self.command(services.create_initial_draft)
            audit.assert_called_once()
        self.assertEqual(self.database_state(), before)

    def test_unexpected_audit_exception_also_rolls_back_all_domain_writes(self):
        before = self.database_state()
        with patch.object(AuditEvent, "save", side_effect=RuntimeError("Synthetic unexpected audit failure")) as audit:
            with self.assertRaises(RuntimeError):
                self.command(services.create_initial_draft)
            audit.assert_called_once()
        self.assertEqual(self.database_state(), before)

    def test_audit_failure_rolls_back_draft_edit_and_cache_invalidation(self):
        assessment = self.complete_draft()
        point = self.make_point(assessment)
        self.raw(ShotPoint).filter(pk=point.pk).update(score=0)
        before = self.database_state()
        with patch.object(AuditEvent, "save", side_effect=ValidationError("Synthetic audit failure")) as audit:
            with self.assertRaises(ValidationError):
                self.command(services.update_draft, assessment=assessment, data={"notes": "Rollback"})
            audit.assert_called_once()
        self.assertEqual(self.database_state(), before)

    def test_audit_failure_rolls_back_point_add_update_and_delete(self):
        assessment = self.complete_draft()
        point = self.make_point(assessment)
        for function, kwargs in (
            (services.add_shot_point, {"data": {"x_px": 1, "y_px": 1}}),
            (services.update_shot_point, {"point": point, "data": {"x_px": 2}}),
            (services.delete_shot_point, {"point": point}),
        ):
            before = self.database_state()
            with self.subTest(action=function.__name__), patch.object(AuditEvent, "save", side_effect=ValidationError("Synthetic audit failure")) as audit:
                with self.assertRaises(ValidationError):
                    self.command(function, assessment=assessment, **kwargs)
                audit.assert_called_once()
            self.assertEqual(self.database_state(), before)

    def test_stale_request_does_not_change_points_audit_or_version(self):
        assessment = self.complete_draft()
        point = self.make_point(assessment)
        for function, kwargs in ((services.update_draft, {"data": {"notes": "Stale"}}),
                                 (services.add_shot_point, {"data": {"x_px": 1, "y_px": 1}}),
                                 (services.update_shot_point, {"point": point, "data": {"x_px": 1}}),
                                 (services.delete_shot_point, {"point": point})):
            before = self.database_state()
            with self.subTest(action=function.__name__), self.assertRaises(ValidationError):
                self.command(function, assessment=assessment, expected_edit_version=1, **kwargs)
            self.assertEqual(self.database_state(), before)

    def test_verified_and_locked_stay_immutable(self):
        assessment = self.privileged_state(status="verified")
        for status in ("verified", "locked"):
            if status == "locked":
                self.raw(Assessment).filter(pk=assessment.pk).update(status="locked", locked_by_id=self.actor.pk, locked_at=assessment.verified_at)
            assessment.refresh_from_db()
            point = assessment.shot_points.get()
            for function, kwargs in ((services.update_draft, {"data": {"notes": "Forbidden"}}),
                                     (services.add_shot_point, {"data": {"x_px": 1, "y_px": 1}}),
                                     (services.update_shot_point, {"point": point, "data": {"x_px": 1}}),
                                     (services.delete_shot_point, {"point": point})):
                before = self.database_state()
                with self.subTest(status=status, action=function.__name__), self.assertRaises(ValidationError):
                    self.command(function, assessment=assessment, **kwargs)
                self.assertEqual(self.database_state(), before)
        with self.assertRaises(ValidationError):
            self.command(services.create_initial_draft)

    def test_sql_context_drift_is_rejected_without_rewriting_history(self):
        assessment = self.complete_draft()
        self.raw(TrainingSession).filter(pk=self.session.pk).update(distance_m=Decimal("20"))
        before = self.database_state()
        with self.assertRaises(ValidationError):
            self.command(services.update_draft, assessment=assessment, data={"notes": "Forbidden"})
        self.assertEqual(self.database_state(), before)


class DraftBoundaryTests(ServiceTestCase):
    def test_snapshot_allowlist_excludes_credentials_files_and_raw_ai_parameters(self):
        assessment = self.complete_draft()
        receipt = self.command(services.add_shot_point, assessment=assessment, data={"x_px": 1, "y_px": 1})
        event = AuditEvent.objects.get(pk=receipt.audit_event_id)
        def inspect(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    self.assertNotIn(key, {"password", "secret_hash", "token", "pin", "session_key", "authorization",
                                           "storage_key", "parameters", "raw_result", "authentication_context"})
                    inspect(child)
            elif isinstance(value, list):
                for child in value:
                    inspect(child)
        inspect(event.before_state)
        inspect(event.after_state)
        self.assertIsNone(event.authentication_context)

    def test_first_acquisition_order_and_no_join_locks_follow_04b1(self):
        assessment = self.complete_draft()
        point = self.make_point(assessment)
        with traced_locks() as trace:
            self.command(services.update_shot_point, assessment=assessment, point=point, data={"x_px": 11})
        names = list(dict.fromkeys(entry[0] for entry in trace))
        self.assertEqual(names, ["TrainingSession", "ActivityParticipation", "Assessment", "ShotPoint", "User", "ActivityPermissionGrant"])
        self.assertTrue(all(not entry[2] for entry in trace))

    def test_direct_model_writes_remain_a_documented_unaudited_boundary(self):
        assessment = self.complete_draft()
        assessment.notes = "Trusted low-level model write"
        assessment.save(expected_edit_version=1)
        self.make_point(assessment)
        self.assertFalse(AuditEvent.objects.exists())

    def test_no_participant_audit_or_official_score_path_is_opened(self):
        receipt = self.command(services.create_initial_draft)
        assessment = Assessment.objects.get(pk=receipt.assessment_id)
        self.assertIsNone(assessment.total_score)
        self.assertEqual(assessment.status, "draft")
        event = AuditEvent(**self.audit_values(
            actor_kind="participant", actor_user=None, actor_participation=self.session.participation,
            authentication_context=self.auth_context(),
        ))
        with self.assertRaises(ValidationError):
            event.save()
