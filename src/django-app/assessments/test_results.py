"""04A schema/ORM regression. Official states are privileged synthetic fixtures only."""

import copy
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, models, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.models.deletion import ProtectedError
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from activities.models import Activity, ActivityParticipation, TrainingSession
from assessments.models import Assessment, AuditEvent, ScoringRuleVersion, ShotPoint
from assessments.test_evidence import EvidenceTestCase, RESULT

User = get_user_model()


class ResultTestCase(EvidenceTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.rule = ScoringRuleVersion.objects.create(
            code="result-fixture", version=1, created_by=cls.actor,
            algorithm_version="synthetic-v1", boundary_policy="Synthetic fixture only",
            numeric_precision="Synthetic fixture only",
        ).publish()

    def make_assessment(self, **overrides):
        values = {"session": self.session, "revision_number": 1, "created_by": self.actor}
        values.update(overrides)
        for field in ("session", "created_by", "photo", "detection_run", "calibration", "scoring_rule_version"):
            if f"{field}_id" in overrides:
                values.pop(field, None)
        return Assessment.objects.create(**values)

    def complete_draft(self, **overrides):
        photo = self.make_photo()
        values = {"photo": photo, "calibration": self.accept(self.make_calibration(photo=photo)),
                  "scoring_rule_version": self.rule}
        values.update(overrides)
        return self.make_assessment(**values)

    def make_point(self, assessment, **overrides):
        assessment.refresh_from_db()
        values = {"assessment": assessment, "point_number": 1, "x_px": Decimal("10.125"),
                  "y_px": Decimal("20.5"), "source": "manual"}
        values.update(overrides)
        return self.save_point(values, assessment.edit_version)

    def save_point(self, values, version):
        point = ShotPoint(**values)
        point.save(expected_edit_version=version)
        return point

    def privileged_state(self, *, status="locked", with_point=True):
        """Deliberate SQL bypass; not an exposed transition or a real scoring result."""
        assessment = self.complete_draft()
        if with_point:
            point = self.make_point(assessment)
            self.raw(ShotPoint).filter(pk=point.pk).update(score=1)
        assessment.refresh_from_db()
        now = timezone.now()
        values = {
            "status": status, "verified_by_id": self.actor.pk, "verified_at": now,
            "total_score": 1 if with_point else 0, "scored_at": now,
            "scored_edit_version": assessment.edit_version, "scoring_engine_version": "synthetic-unexecuted-fixture",
            "scoring_input_sha256": "f" * 64, "scoring_context": {"schema_version": 1, "fixture_only": True},
        }
        if status == "locked":
            values.update(locked_by_id=self.actor.pk, locked_at=now)
        self.raw(Assessment).filter(pk=assessment.pk).update(**values)
        assessment.refresh_from_db()
        return assessment

    def privileged_revision(self, previous, *, status="draft"):
        now = timezone.now()
        revision = Assessment(
            session=previous.session, revision_number=previous.revision_number + 1,
            previous_assessment=previous, created_by=self.actor, revision_reason="Synthetic review",
            photo=previous.photo, calibration=previous.calibration, scoring_rule_version=self.rule,
            historical_context=copy.deepcopy(previous.historical_context), created_at=now, updated_at=now,
        )
        revision.full_clean()
        revision.save_base(raw=True, force_insert=True)
        if status == "locked":
            self.raw(Assessment).filter(pk=revision.pk).update(
                status="locked", verified_by_id=self.actor.pk, verified_at=now, locked_by_id=self.actor.pk,
                locked_at=now, total_score=0, scored_at=now, scored_edit_version=1,
                scoring_engine_version="synthetic-unexecuted-fixture", scoring_input_sha256="f" * 64,
                scoring_context={"schema_version": 1, "fixture_only": True},
            )
            revision.refresh_from_db()
        return revision

    def auth_context(self, participation=None):
        participation = participation or self.session.participation
        return {"schema_version": 1, "participation_id": participation.pk,
                "method": "synthetic-unimplemented-auth", "authenticated_at": timezone.now().isoformat(),
                "authentication_id": str(uuid4())}

    def audit_values(self, **overrides):
        values = {"actor_kind": "user", "actor_user": self.actor, "session": self.session,
                  "action": "synthetic.schema_test", "before_state": {"schema_version": 1},
                  "after_state": {"schema_version": 1}}
        values.update(overrides)
        return values


class AssessmentModelTests(ResultTestCase):
    def test_initial_draft_is_unscored_and_captures_persisted_context(self):
        assessment = self.make_assessment()
        self.assertEqual((assessment.status, assessment.revision_number, assessment.edit_version), ("draft", 1, 1))
        self.assertIsNone(assessment.photo_id)
        self.assertIsNone(assessment.total_score)
        self.assertIsNone(assessment.scored_at)
        self.assertIsNone(assessment.verified_at)
        self.assertEqual(assessment.historical_context["session_id"], self.session.pk)
        self.assertEqual(assessment.historical_context["participant_id"], self.session.participation.participant_id)
        self.assertEqual(assessment.historical_context["caliber_mm"], "9.00")

    def test_caller_cannot_forge_historical_metadata(self):
        for context in ({}, {"schema_version": 1}, {"schema_version": 1, "value": float("nan")}):
            with self.subTest(context=context), self.assertRaises(ValidationError):
                self.make_assessment(historical_context=context)
        self.assertEqual(Assessment.objects.count(), 0)

    def test_edit_requires_current_version_and_increments_once(self):
        assessment = self.make_assessment()
        stale = Assessment.objects.get(pk=assessment.pk)
        assessment.notes = "Draft only"
        for expected in (None, 0, True, "1", 2):
            with self.subTest(expected=expected), self.assertRaises(ValidationError):
                assessment.save(expected_edit_version=expected)
        assessment.save(expected_edit_version=1)
        self.assertEqual(assessment.edit_version, 2)
        stale.notes = "Stale overwrite"
        with self.assertRaises(ValidationError):
            stale.save(expected_edit_version=1)
        assessment.refresh_from_db()
        self.assertEqual(assessment.notes, "Draft only")

    def test_manual_version_identity_authorship_and_history_changes_are_rejected(self):
        assessment = self.make_assessment()
        for field, value in (
            ("edit_version", 7), ("revision_number", 2), ("created_by_id", 99999),
            ("created_at", timezone.now()), ("historical_context", {"schema_version": 1}),
        ):
            assessment.refresh_from_db()
            setattr(assessment, field, value)
            with self.subTest(field=field), self.assertRaises(ValidationError):
                assessment.save(expected_edit_version=1)

    def test_failed_validation_does_not_increment_version(self):
        assessment = self.make_assessment()
        assessment.verified_at = timezone.now()
        with self.assertRaises(ValidationError):
            assessment.save(expected_edit_version=1)
        self.assertEqual(assessment.edit_version, 1)
        assessment.refresh_from_db()
        self.assertEqual(assessment.edit_version, 1)

    def test_official_creation_and_all_public_transitions_are_closed(self):
        for status in ("verified", "locked", "invalid"):
            with self.subTest(status=status), self.assertRaises(ValidationError):
                self.make_assessment(status=status)
        assessment = self.make_assessment()
        for status in ("verified", "locked"):
            assessment.status = status
            with self.subTest(status=status), self.assertRaises(ValidationError):
                assessment.save(expected_edit_version=1)
        assessment.refresh_from_db()
        self.assertEqual(assessment.status, "draft")

    def test_client_scores_and_provenance_cannot_be_written_even_if_zero(self):
        for changes in (
            {"total_score": 0}, {"total_score": 100}, {"scoring_context": {"schema_version": 1}},
            {"scored_at": timezone.now()}, {"scored_edit_version": 1},
            {"scoring_engine_version": "claimed"}, {"scoring_input_sha256": "a" * 64},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                self.make_assessment(**changes)
        self.assertFalse(Assessment.objects.exists())

    def test_existing_draft_also_rejects_claimed_scores_without_changing_version(self):
        assessment = self.make_assessment()
        for value in (0, 1):
            assessment.total_score = value
            with self.subTest(value=value), self.assertRaises(ValidationError):
                assessment.save(expected_edit_version=1)
        assessment.refresh_from_db()
        self.assertEqual(assessment.edit_version, 1)
        self.assertIsNone(assessment.total_score)

    def test_photo_run_calibration_must_share_context(self):
        photo, other = self.make_photo(), self.make_photo()
        run = self.finish_run(result=RESULT)
        calibration = self.accept(self.make_calibration(photo=other))
        other_session = TrainingSession.objects.create(
            participation=self.session.participation, session_number=2, assigned_assessor=self.actor,
            created_by=self.actor, weapon_type="pistol", shooting_position="berdiri", distance_m=10, caliber_mm=9,
        )
        alien_photo = self.make_photo(session=other_session)
        for values in (
            {"photo": alien_photo}, {"photo": photo, "detection_run": run},
            {"photo": photo, "calibration": calibration}, {"detection_run": run},
            {"calibration": calibration}, {"photo": photo, "calibration": self.make_calibration(photo=photo)},
        ):
            with self.subTest(values=list(values)), self.assertRaises(ValidationError):
                self.make_assessment(**values)

    def test_pending_running_and_failed_run_cannot_be_selected(self):
        run = self.make_run()
        for status in ("pending", "running", "failed"):
            if status == "running":
                run.status = status
                run.save()
            elif status == "failed":
                run.status, run.failure_code = status, "synthetic_failure"
                run.save()
            with self.subTest(status=status), self.assertRaises(ValidationError):
                self.make_assessment(photo=run.photo, detection_run=run)

    def test_valid_evidence_and_succeeded_empty_result_are_allowed_in_draft(self):
        run = self.finish_run()
        assessment = self.make_assessment(
            photo=run.photo, detection_run=run,
            calibration=self.accept(self.make_calibration(photo=run.photo)), scoring_rule_version=self.rule,
        )
        self.assertEqual(assessment.detection_run.raw_result["detections"], [])
        self.assertIsNone(assessment.total_score)

    def test_draft_rule_is_rejected_but_published_or_retired_history_is_valid(self):
        rule = ScoringRuleVersion.objects.create(code="incomplete", version=1, created_by=self.actor)
        with self.assertRaises(ValidationError):
            self.make_assessment(scoring_rule_version=rule)
        assessment = self.make_assessment(scoring_rule_version=self.rule)
        self.rule.retire()
        assessment.full_clean()

    def test_evidence_switch_with_existing_points_is_blocked_even_in_same_photo_frame(self):
        assessment = self.complete_draft()
        self.make_point(assessment)
        alternatives = {
            "photo": self.make_photo(), "calibration": self.accept(self.make_calibration(photo=assessment.photo)),
            "detection_run": self.make_run(photo=assessment.photo), "scoring_rule_version": None,
        }
        for field, value in alternatives.items():
            assessment.refresh_from_db()
            setattr(assessment, field, value)
            with self.subTest(field=field), self.assertRaises(ValidationError):
                assessment.save(expected_edit_version=assessment.edit_version)

    def test_persisted_and_stale_verified_locked_assessments_are_frozen(self):
        for state in ("verified", "locked"):
            if Assessment.objects.exists():
                # Separate context for the next fixture; never delete a historical result.
                self.session = TrainingSession.objects.create(
                    participation=self.session.participation, session_number=2, assigned_assessor=self.actor,
                    created_by=self.actor, weapon_type="pistol", shooting_position="berdiri", distance_m=10, caliber_mm=9,
                )
            assessment = self.privileged_state(status=state)
            assessment.full_clean()
            stale = Assessment.objects.get(pk=assessment.pk)
            stale.status, stale.notes = "draft", "Bypass"
            for instance in (assessment, stale):
                with self.subTest(state=state), self.assertRaises(ValidationError):
                    instance.save(expected_edit_version=instance.edit_version)
                with self.assertRaises(ValidationError):
                    instance.delete()

    def test_no_public_reopening_and_predecessor_domain_validation(self):
        locked = self.privileged_state()
        with self.assertRaises(ValidationError):
            self.make_assessment(revision_number=2, previous_assessment=locked, revision_reason="Claimed review")
        revision = self.privileged_revision(locked)
        self.assertEqual(revision.previous_assessment_id, locked.pk)
        for changes in (
            {"revision_number": 3}, {"revision_reason": ""},
            {"previous_assessment": revision},
        ):
            candidate = Assessment.objects.get(pk=revision.pk)
            for field, value in changes.items():
                setattr(candidate, field, value)
            with self.subTest(changes=list(changes)), self.assertRaises(ValidationError):
                candidate.full_clean()

    def test_predecessor_must_be_locked_and_in_same_session(self):
        draft = self.complete_draft()
        candidate = Assessment(
            session_id=999999, revision_number=2, previous_assessment=draft, created_by=self.actor,
            revision_reason="Synthetic", historical_context=copy.deepcopy(draft.historical_context),
        )
        with self.assertRaises(ValidationError):
            candidate.full_clean()

    def test_latest_official_remains_visible_while_new_revision_is_draft(self):
        first = self.privileged_state()
        second = self.privileged_revision(first)
        self.assertEqual(Assessment.objects.latest_working(session_id=self.session.pk), second)
        self.assertEqual(Assessment.objects.latest_official(session_id=self.session.pk), first)
        self.assertIsNone(Assessment.objects.latest_official(session_id=999999))
        self.assertEqual(Assessment.objects.get(pk=first.pk).status, "locked")

    def test_new_locked_revision_supersedes_by_query_without_mutating_old_result(self):
        first = self.privileged_state()
        original_time, original_score = first.locked_at, first.total_score
        second = self.privileged_revision(first, status="locked")
        self.assertEqual(Assessment.objects.latest_official(session_id=self.session.pk), second)
        first.refresh_from_db()
        self.assertEqual((first.locked_at, first.total_score, first.status), (original_time, original_score, "locked"))

    def test_database_assessment_identity_lifecycle_and_provenance_constraints(self):
        assessment = self.make_assessment()
        now = timezone.now()
        for changes in (
            {"revision_number": 0}, {"edit_version": 0}, {"revision_number": 2},
            {"previous_assessment_id": assessment.pk}, {"status": "unknown"},
            {"revision_number": 2, "previous_assessment_id": assessment.pk},
            {"status": "verified"}, {"status": "locked"}, {"verified_at": now},
            {"locked_by_id": self.actor.pk}, {"total_score": -1}, {"total_score": 0},
            {"scored_edit_version": 1}, {"scoring_engine_version": "claimed"},
        ):
            with self.subTest(changes=changes):
                self.assert_database_rejects(assessment, **changes)

    def test_database_enforces_complete_scoring_provenance_and_time_order(self):
        locked = self.privileged_state()
        for changes in (
            {"photo_id": None}, {"calibration_id": None}, {"scoring_rule_version_id": None},
            {"total_score": None}, {"scoring_context": None}, {"scored_at": None},
            {"scored_edit_version": None}, {"scored_edit_version": locked.edit_version + 1},
            {"scoring_engine_version": ""}, {"scoring_input_sha256": "a" * 64 + "\n"},
            {"verified_at": locked.created_at - timedelta(seconds=1)},
            {"locked_at": locked.verified_at - timedelta(seconds=1)},
        ):
            with self.subTest(changes=changes):
                self.assert_database_rejects(locked, **changes)

    def test_database_enforces_single_working_revision_and_single_successor(self):
        first = self.privileged_state()
        second = self.privileged_revision(first)
        values = {field.attname: getattr(second, field.attname) for field in Assessment._meta.concrete_fields if not field.primary_key}
        values["revision_number"] = 3
        values["previous_assessment_id"] = second.pk
        with self.assertRaises(IntegrityError), transaction.atomic():
            Assessment(**values).save_base(raw=True, force_insert=True)
        values = {field.attname: getattr(first, field.attname) for field in Assessment._meta.concrete_fields if not field.primary_key}
        values.update(revision_number=3, previous_assessment_id=first.pk)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Assessment(**values).save_base(raw=True, force_insert=True)
        self.assertEqual(Assessment.objects.count(), 2)

    def test_existing_model_context_drift_is_detected_without_expanding_old_models(self):
        assessment = self.make_assessment()
        context = copy.deepcopy(assessment.historical_context)
        self.session.distance_m = Decimal("12")
        self.session.save()  # Current iteration 01 API still allows this; 04B must freeze it.
        assessment.notes = "Reject an edit against changed context"
        with self.assertRaises(ValidationError):
            assessment.save(expected_edit_version=1)
        assessment.refresh_from_db()
        self.assertEqual(assessment.historical_context, context)
        self.assertEqual(assessment.edit_version, 1)


class ShotPointModelTests(ResultTestCase):
    def test_manual_subpixel_point_is_unscored_and_updates_parent_version(self):
        assessment = self.complete_draft()
        point = self.make_point(assessment)
        self.assertEqual(point.x_px, Decimal("10.125"))
        self.assertIsNone(point.score)
        self.assertEqual(point.origin_detection_id, "")
        assessment.refresh_from_db()
        self.assertEqual(assessment.edit_version, 2)

    def test_points_require_photo_and_current_parent_version(self):
        assessment = self.make_assessment()
        with self.assertRaises(ValidationError):
            self.make_point(assessment)
        assessment.photo = self.make_photo()
        assessment.save(expected_edit_version=1)
        point = ShotPoint(assessment=assessment, point_number=1, x_px=1, y_px=1, source="manual")
        with self.assertRaises(ValidationError):
            point.save()
        self.assertEqual(ShotPoint.objects.count(), 0)

    def test_point_changes_edit_and_delete_increment_version_and_stale_requests_fail(self):
        assessment = self.complete_draft()
        point = self.make_point(assessment)
        point.x_px = Decimal("11")
        point.save(expected_edit_version=2)
        with self.assertRaises(ValidationError):
            point.delete(expected_edit_version=2)
        point.delete(expected_edit_version=3)
        assessment.refresh_from_db()
        self.assertEqual(assessment.edit_version, 4)
        self.assertFalse(ShotPoint.objects.exists())

    def test_included_excluded_state_and_reason(self):
        assessment = self.complete_draft()
        with self.assertRaises(ValidationError):
            self.make_point(assessment, is_included=False, exclusion_reason=" \t ")
        point = self.make_point(assessment, is_included=False, exclusion_reason="Synthetic exclusion")
        self.assertFalse(point.is_included)
        self.assertIsNone(point.score)

    def test_client_point_score_including_zero_is_rejected(self):
        assessment = self.complete_draft()
        for score in (0, 1, 999, -1):
            with self.subTest(score=score), self.assertRaises(ValidationError):
                self.make_point(assessment, score=score)
        assessment.refresh_from_db()
        self.assertEqual(assessment.edit_version, 1)

    def test_coordinates_are_finite_half_open_analysis_pixels_without_silent_rounding(self):
        photo = self.make_photo(analysis_width_px=40, analysis_height_px=30)
        assessment = self.make_assessment(photo=photo)
        for values in (
            {"x_px": -1}, {"y_px": -1}, {"x_px": 40}, {"y_px": 30},
            {"x_px": Decimal("NaN")}, {"x_px": Decimal("Infinity")}, {"y_px": Decimal("-Infinity")},
            {"x_px": Decimal("1.0000001")}, {"x_px": True}, {"y_px": "bad"},
        ):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                self.make_point(assessment, **values)
        point = self.make_point(assessment, x_px=Decimal("39.999999"), y_px=Decimal("29.999999"))
        self.assertEqual(point.x_px, Decimal("39.999999"))

    def test_ai_point_requires_existing_detection_in_selected_succeeded_run(self):
        run = self.finish_run(result=RESULT)
        assessment = self.make_assessment(photo=run.photo, detection_run=run)
        for identifier in ("", "unknown"):
            with self.subTest(identifier=identifier), self.assertRaises(ValidationError):
                self.make_point(assessment, source="ai", origin_detection_id=identifier)
        point = self.make_point(assessment, source="ai", origin_detection_id="synthetic-1")
        self.assertEqual(point.source, "ai")
        point.x_px = Decimal("25")  # Human correction preserves the original AI provenance.
        point.save(expected_edit_version=2)
        self.assertEqual(point.origin_detection_id, "synthetic-1")

    def test_manual_points_cannot_claim_ai_origin_and_ai_cannot_claim_empty_success(self):
        run = self.finish_run()
        assessment = self.make_assessment(photo=run.photo, detection_run=run)
        for values in ({"origin_detection_id": "synthetic-1"}, {"source": "ai", "origin_detection_id": "synthetic-1"}):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                self.make_point(assessment, **values)

    def test_point_provenance_parent_and_number_are_immutable(self):
        assessment = self.complete_draft()
        point = self.make_point(assessment)
        for field, value in (("point_number", 2), ("source", "ai"), ("origin_detection_id", "claimed"), ("created_at", timezone.now())):
            point.refresh_from_db()
            setattr(point, field, value)
            with self.subTest(field=field), self.assertRaises(ValidationError):
                point.save(expected_edit_version=2)

    def test_locked_point_cannot_escape_by_changing_parent_or_using_stale_status(self):
        locked = self.privileged_state()
        point = locked.shot_points.get()
        second_session = TrainingSession.objects.create(
            participation=self.session.participation, session_number=2, assigned_assessor=self.actor,
            created_by=self.actor, weapon_type="pistol", shooting_position="berdiri", distance_m=10, caliber_mm=9,
        )
        draft = self.make_assessment(session=second_session, photo=self.make_photo(session=second_session))
        for operation in ("save", "delete"):
            point.refresh_from_db()
            point.assessment = draft
            with self.subTest(operation=operation), self.assertRaises(ValidationError):
                getattr(point, operation)(expected_edit_version=1)
        point.refresh_from_db()
        self.assertEqual(point.assessment_id, locked.pk)

    def test_verified_and_locked_parent_block_all_point_mutations(self):
        assessment = self.privileged_state(status="verified")
        point = assessment.shot_points.get()
        for status in ("verified", "locked"):
            if status == "locked":
                self.raw(Assessment).filter(pk=assessment.pk).update(status="locked", locked_by_id=self.actor.pk, locked_at=timezone.now())
            point.score, point.x_px = None, Decimal("2")
            with self.subTest(status=status), self.assertRaises(ValidationError):
                point.save(expected_edit_version=assessment.edit_version)
            with self.assertRaises(ValidationError):
                point.delete(expected_edit_version=assessment.edit_version)
            with self.assertRaises(ValidationError):
                self.make_point(assessment, point_number=2)

    def test_invalid_point_does_not_write_or_increment_parent(self):
        assessment = self.complete_draft()
        with self.assertRaises(ValidationError):
            self.make_point(assessment, x_px=-1)
        assessment.refresh_from_db()
        self.assertEqual(assessment.edit_version, 1)
        self.assertEqual(assessment.shot_points.count(), 0)

    def test_deleted_point_cannot_be_resurrected_by_stale_instance(self):
        assessment = self.complete_draft()
        point = self.make_point(assessment)
        stale = ShotPoint.objects.get(pk=point.pk)
        point.delete(expected_edit_version=2)
        with self.assertRaises(ValidationError):
            stale.save(expected_edit_version=3)
        assessment.refresh_from_db()
        self.assertEqual(assessment.edit_version, 3)
        self.assertFalse(ShotPoint.objects.exists())

    def test_point_write_and_parent_version_are_one_model_transaction(self):
        assessment = self.complete_draft()
        from unittest.mock import patch
        with patch.object(ShotPoint, "_bump_parent", side_effect=ValidationError("Synthetic failure")):
            with self.assertRaises(ValidationError):
                self.make_point(assessment)
        self.assertFalse(ShotPoint.objects.exists())
        assessment.refresh_from_db()
        self.assertEqual(assessment.edit_version, 1)

    def test_database_point_constraints_and_uniqueness(self):
        assessment = self.complete_draft()
        first = self.make_point(assessment)
        second = self.make_point(assessment, point_number=2)
        for changes in (
            {"point_number": 0}, {"point_number": first.point_number}, {"x_px": -1}, {"y_px": -1},
            {"x_px": Decimal("10000000000")}, {"source": "unknown"},
            {"source": "ai"}, {"origin_detection_id": "claimed"}, {"score": -1},
            {"is_included": False}, {"is_included": False, "exclusion_reason": "Synthetic", "score": 0},
        ):
            with self.subTest(changes=changes):
                self.assert_database_rejects(second, **changes)

    def test_duplicate_ai_detection_is_rejected_in_database(self):
        run = self.finish_run(result=RESULT)
        assessment = self.make_assessment(photo=run.photo, detection_run=run)
        first = self.make_point(assessment, source="ai", origin_detection_id="synthetic-1")
        second = self.make_point(assessment, point_number=2)
        self.assert_database_rejects(second, source="ai", origin_detection_id=first.origin_detection_id)

    def test_excluded_point_with_score_or_wrong_total_invalidates_official_fixture(self):
        locked = self.privileged_state()
        self.raw(ShotPoint).filter(assessment=locked).update(score=0)
        with self.assertRaises(ValidationError):
            locked.full_clean()


class AuditEventModelTests(ResultTestCase):
    def test_user_and_system_events_append_without_dummy_users(self):
        user = AuditEvent.objects.create(**self.audit_values())
        system = AuditEvent.objects.create(**self.audit_values(
            actor_kind="system", actor_user=None, system_component="synthetic-worker",
            system_version="fixture-v1", caused_by_event=user,
        ))
        self.assertEqual(user.actor_user_id, self.actor.pk)
        self.assertIsNone(system.actor_user_id)
        self.assertEqual(system.caused_by_event_id, user.pk)

    def test_all_three_actor_shapes_validate_but_claimed_participant_cannot_be_inserted(self):
        principal = self.session.participation
        self.assertIsNone(principal.participant.account_id)
        event = AuditEvent(**self.audit_values(
            actor_kind="participant", actor_user=None, actor_participation=principal,
            authentication_context=self.auth_context(),
        ))
        event.full_clean()  # Shape is not proof of authentication.
        with self.assertRaises(ValidationError):
            event.save()
        self.assertFalse(AuditEvent.objects.exists())

    def test_actor_xor_rejects_missing_mixed_and_wrong_kind_identities(self):
        for changes in (
            {"actor_user": None}, {"actor_kind": "unknown"},
            {"actor_participation": self.session.participation},
            {"system_component": "claimed", "system_version": "v1"},
            {"actor_kind": "system", "actor_user": None},
            {"actor_kind": "participant", "actor_user": None, "actor_participation": self.session.participation},
            {"authentication_context": self.auth_context()},
        ):
            with self.subTest(changes=list(changes)), self.assertRaises(ValidationError):
                AuditEvent(**self.audit_values(**changes)).full_clean()

    def test_scope_xor_and_assessment_scope_match(self):
        assessment = self.make_assessment()
        for changes in (
            {"session": None}, {"activity": self.session.participation.activity},
            {"participation": self.session.participation},
            {"session": None, "participation": self.session.participation, "assessment": assessment},
        ):
            with self.subTest(changes=list(changes)), self.assertRaises(ValidationError):
                AuditEvent.objects.create(**self.audit_values(**changes))
        event = AuditEvent.objects.create(**self.audit_values(assessment=assessment))
        self.assertEqual(event.assessment_id, assessment.pk)

    def test_activity_and_participation_scopes_can_stand_alone(self):
        for changes in ({"activity": self.session.participation.activity}, {"participation": self.session.participation}):
            event = AuditEvent.objects.create(**self.audit_values(session=None, **changes))
            self.assertIsNone(event.session_id)

    def test_cross_session_assessment_reference_is_rejected(self):
        assessment = self.make_assessment()
        other = TrainingSession.objects.create(
            participation=self.session.participation, session_number=2, assigned_assessor=self.actor,
            created_by=self.actor, weapon_type="pistol", shooting_position="berdiri", distance_m=10, caliber_mm=9,
        )
        with self.assertRaises(ValidationError):
            AuditEvent.objects.create(**self.audit_values(session=other, assessment=assessment))

    def test_participant_cannot_claim_another_scope_or_authentication_identity(self):
        other_activity = Activity.objects.create(name="Synthetic other", training_date=timezone.localdate(), created_by=self.actor)
        for changes in (
            {"session": None, "activity": other_activity},
            {"authentication_context": {**self.auth_context(), "participation_id": 999999}},
        ):
            values = self.audit_values(
                actor_kind="participant", actor_user=None, actor_participation=self.session.participation,
                authentication_context=self.auth_context(),
            )
            values.update(changes)
            with self.subTest(changes=list(changes)), self.assertRaises(ValidationError):
                AuditEvent(**values).full_clean()

    def test_authentication_context_shape_and_timezone_are_validated(self):
        for context in (
            {"schema_version": 1}, {**self.auth_context(), "authenticated_at": "2026-01-01T12:00:00"},
            {**self.auth_context(), "authentication_id": "session-key"},
            {**self.auth_context(), "credential_generation": True},
        ):
            with self.subTest(context=context), self.assertRaises(ValidationError):
                AuditEvent(**self.audit_values(
                    actor_kind="participant", actor_user=None, actor_participation=self.session.participation,
                    authentication_context=context,
                )).full_clean()

    def test_snapshots_reject_empty_nonfinite_non_json_and_secret_payloads(self):
        for payload in (
            None, {}, [], {"schema_version": True}, {"schema_version": 2},
            {"schema_version": 1, "nested": [float("nan")]},
            {"schema_version": 1, "nested": {1: "bad key"}},
            {"schema_version": 1, "value": (1, 2)},
            {"schema_version": 1, "nested": {"TOKEN": "secret"}},
            {"schema_version": 1, "nested": [{"session_key": "secret"}]},
        ):
            for field in ("before_state", "after_state"):
                with self.subTest(payload=payload, field=field), self.assertRaises(ValidationError):
                    AuditEvent.objects.create(**self.audit_values(**{field: payload}))

    def test_valid_structured_snapshots_round_trip(self):
        before = {"schema_version": 1, "point": {"id": 7, "x_px": "12.250000", "is_included": True}}
        after = {"schema_version": 1, "point": {"id": 7, "x_px": "13.250000", "is_included": False}}
        event = AuditEvent.objects.create(**self.audit_values(before_state=before, after_state=after, version_before=1, version_after=2))
        event.refresh_from_db()
        self.assertEqual((event.before_state, event.after_state), (before, after))

    def test_blank_action_and_system_identifiers_are_rejected(self):
        for changes in (
            {"action": " \t "}, {"actor_kind": "system", "actor_user": None, "system_component": " ", "system_version": "v1"},
            {"actor_kind": "system", "actor_user": None, "system_component": "worker", "system_version": " "},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                AuditEvent.objects.create(**self.audit_values(**changes))

    def test_append_only_rejects_existing_stale_and_same_pk_new_objects(self):
        event = AuditEvent.objects.create(**self.audit_values())
        stale = AuditEvent.objects.get(pk=event.pk)
        duplicate = AuditEvent(pk=event.pk, **self.audit_values())
        for instance in (event, stale, duplicate):
            instance.reason = "Overwrite"
            with self.assertRaises(ValidationError):
                instance.save()
            with self.assertRaises(ValidationError):
                instance.delete()

    def test_new_explicit_pk_audit_does_not_issue_an_update_before_insert(self):
        event = AuditEvent(pk=500000, **self.audit_values())
        with self.assertRaises(ValidationError):
            event.save(force_update=True)
        with CaptureQueriesContext(connection) as queries:
            event.save()
        self.assertFalse(any(query["sql"].lstrip().upper().startswith('UPDATE "ASSESSMENTS_AUDITEVENT"') for query in queries))
        self.assertEqual(AuditEvent.objects.get(pk=500000).action, "synthetic.schema_test")

    def test_database_actor_scope_version_and_unique_operation_constraints(self):
        first = AuditEvent.objects.create(**self.audit_values())
        second = AuditEvent.objects.create(**self.audit_values())
        for changes in (
            {"actor_kind": "unknown"}, {"actor_user_id": None}, {"actor_participation_id": self.session.participation_id},
            {"system_component": "mixed"}, {"session_id": None},
            {"participation_id": self.session.participation_id}, {"action": ""},
            {"version_before": 1}, {"version_after": 2}, {"version_before": 1, "version_after": 3},
            {"caused_by_event_id": second.pk}, {"operation_id": first.operation_id},
        ):
            with self.subTest(changes=changes):
                self.assert_database_rejects(second, **changes)

    def test_cause_and_historical_foreign_keys_are_protected(self):
        actor = User.objects.create_user("audit-only@example.com")
        assessment = self.make_assessment()
        event = AuditEvent.objects.create(**self.audit_values(actor_user=actor, assessment=assessment))
        caused = AuditEvent.objects.create(**self.audit_values(caused_by_event=event))
        for parent, child in ((actor, event), (assessment, event), (event, caused)):
            with self.subTest(parent=type(parent).__name__), self.assertRaises(ProtectedError) as error:
                models.Model.delete(parent)
            self.assertIn(child, error.exception.protected_objects)


class ResultWriteBoundaryTests(ResultTestCase):
    def test_all_model_foreign_keys_use_protect(self):
        for model in (Assessment, ShotPoint, AuditEvent):
            for field in model._meta.fields:
                if isinstance(field, models.ForeignKey):
                    with self.subTest(model=model.__name__, field=field.name):
                        self.assertIs(field.remote_field.on_delete, models.PROTECT)

    def test_bulk_partial_base_and_related_manager_guards(self):
        assessment = self.complete_draft()
        point = self.make_point(assessment)
        event = AuditEvent.objects.create(**self.audit_values(assessment=assessment))
        for instance in (assessment, point, event):
            manager = type(instance).objects
            for operation in (
                lambda: manager.filter(pk=instance.pk).update(created_at=timezone.now()),
                lambda: manager.filter(pk=instance.pk).delete(),
                lambda: manager.bulk_create([instance]),
                lambda: manager.bulk_update([instance], ["created_at"]),
                lambda: type(instance)._base_manager.filter(pk=instance.pk).update(created_at=timezone.now()),
                lambda: instance.save(update_fields=["created_at"]),
            ):
                with self.subTest(model=type(instance).__name__), self.assertRaises(ValidationError):
                    operation()
        for manager in (self.session.assessments, assessment.shot_points, assessment.audit_events):
            with self.assertRaises(ValidationError):
                manager.update(created_at=timezone.now())

    def test_async_guards_and_custom_optimistic_version_are_preserved(self):
        assessment = self.complete_draft()
        assessment.notes = "Async draft only"
        async_to_sync(assessment.asave)(expected_edit_version=1)
        point = ShotPoint(assessment=assessment, point_number=1, x_px=1, y_px=1, source="manual")
        async_to_sync(point.asave)(expected_edit_version=2)
        async_to_sync(point.adelete)(expected_edit_version=3)
        event = AuditEvent.objects.create(**self.audit_values())
        for operation in (
            lambda: async_to_sync(event.asave)(), lambda: async_to_sync(event.adelete)(),
            lambda: async_to_sync(Assessment.objects.all().aupdate)(notes="Bypass"),
        ):
            with self.assertRaises(ValidationError):
                operation()

    def test_invalid_foreign_keys_raise_domain_validation_errors(self):
        for field in ("session_id", "created_by_id", "photo_id", "detection_run_id", "calibration_id", "scoring_rule_version_id"):
            for value in ("bad-id", 999999):
                with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                    self.make_assessment(**{field: value})
        for field in ("session_id", "actor_user_id", "assessment_id", "caused_by_event_id"):
            for value in ("bad-id", 999999):
                values = self.audit_values()
                values.pop(field.removesuffix("_id"), None)
                values[field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                    AuditEvent.objects.create(**values)

    def test_invalid_point_parent_and_reparenting_draft_are_rejected(self):
        assessment = self.complete_draft()
        point = self.make_point(assessment)
        for value in (None, "bad-id", 999999):
            point.refresh_from_db()
            point.assessment_id = value
            with self.subTest(value=value), self.assertRaises(ValidationError):
                point.save(expected_edit_version=2)

    def test_point_parent_fk_protects_assessment_and_assessment_protects_evidence(self):
        assessment = self.complete_draft()
        point = self.make_point(assessment)
        with self.assertRaises(ProtectedError) as error:
            models.Model.delete(assessment)
        self.assertIn(point, error.exception.protected_objects)
        for parent in (assessment.photo, assessment.calibration, assessment.scoring_rule_version, self.session):
            with self.subTest(parent=type(parent).__name__), self.assertRaises(ProtectedError) as error:
                models.Model.delete(parent)
            self.assertIn(assessment, error.exception.protected_objects)

    def test_raw_querysets_remain_a_documented_privileged_bypass(self):
        locked = self.privileged_state()
        event = AuditEvent.objects.create(**self.audit_values(assessment=locked))
        self.raw(Assessment).filter(pk=locked.pk).update(notes="Privileged SQL bypass")
        self.raw(ShotPoint).filter(assessment=locked).update(x_px=Decimal("5"))
        self.raw(AuditEvent).filter(pk=event.pk).update(reason="Privileged SQL bypass")
        locked.refresh_from_db()
        event.refresh_from_db()
        self.assertEqual(locked.notes, "Privileged SQL bypass")
        self.assertEqual(event.reason, "Privileged SQL bypass")


class ResultMigrationTests(TransactionTestCase):
    def test_additive_migration_preserves_iteration_three_evidence_and_can_reverse(self):
        before = ("assessments", "0002_photo_detection_calibration")
        after = ("assessments", "0003_assessment_shotpoint_auditevent")
        executor = MigrationExecutor(connection)
        executor.migrate([before])
        try:
            old = executor.loader.project_state([before]).apps
            actor = old.get_model("accounts", "User").objects.create(email="04a-migration@example.com")
            person = old.get_model("accounts", "Participant").objects.create(full_name="Synthetic migration participant")
            activity = old.get_model("activities", "Activity").objects.create(name="Synthetic", training_date=timezone.localdate(), created_by_id=actor.pk)
            participation = old.get_model("activities", "ActivityParticipation").objects.create(activity_id=activity.pk, participant_id=person.pk)
            session = old.get_model("activities", "TrainingSession").objects.create(
                participation_id=participation.pk, session_number=1, assigned_assessor_id=actor.pk, created_by_id=actor.pk,
                weapon_type="pistol", shooting_position="berdiri", distance_m=10, caliber_mm=9,
            )
            target = old.get_model("assessments", "TargetVersion").objects.create(
                code="04a-fixture", version=1, name="Synthetic", width_mm=200, height_mm=300, created_by_id=actor.pk,
            )
            photo = old.get_model("assessments", "TargetPhoto").objects.create(
                session_id=session.pk, storage_key="originals/synthetic-migration.png", sha256="a" * 64, byte_size=1,
                width_px=80, height_px=60, verified_mime_type="image/png", uploaded_by_id=actor.pk,
                preprocessing_version="synthetic", analysis_width_px=80, analysis_height_px=60,
            )
            run = old.get_model("assessments", "DetectionRun").objects.create(
                photo_id=photo.pk, triggered_by_id=actor.pk, model_name="synthetic", weights_sha256="b" * 64,
                runtime_version="synthetic", adapter_version="synthetic", parameters={},
            )
            calibration = old.get_model("assessments", "Calibration").objects.create(
                photo_id=photo.pk, target_version_id=target.pk, method_version="synthetic", created_by_id=actor.pk,
            )
            executor = MigrationExecutor(connection)
            executor.migrate([after])
            new = executor.loader.project_state([after]).apps
            self.assertEqual(new.get_model("assessments", "TargetPhoto").objects.get(pk=photo.pk).sha256, "a" * 64)
            self.assertEqual(new.get_model("assessments", "DetectionRun").objects.get(pk=run.pk).photo_id, photo.pk)
            self.assertEqual(new.get_model("assessments", "Calibration").objects.get(pk=calibration.pk).photo_id, photo.pk)
            for name in ("Assessment", "ShotPoint", "AuditEvent"):
                self.assertEqual(new.get_model("assessments", name).objects.count(), 0)
            executor = MigrationExecutor(connection)
            executor.migrate([before])
            self.assertNotIn("assessments_assessment", connection.introspection.table_names())
            self.assertEqual(old.get_model("assessments", "TargetPhoto").objects.get(pk=photo.pk).sha256, "a" * 64)
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
