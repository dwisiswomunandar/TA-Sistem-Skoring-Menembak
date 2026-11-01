"""Audited draft scoring only; geometry and official states are synthetic fixtures."""

import copy
import hashlib
import json
from decimal import Decimal, localcontext
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError

from activities.models import Activity, ActivityParticipation, ActivityPermissionGrant, TrainingSession
from activities.test_history import traced_locks
from assessments import scoring, scoring_services as commands, services
from assessments.models import Assessment, AuditEvent, ScoringRuleVersion, ShotPoint, TargetRing, TargetVersion
from assessments.test_evidence import RESULT
from assessments.test_services import ServiceTestCase

User = get_user_model()


class DraftScoringTests(ServiceTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.rule = ScoringRuleVersion.objects.create(code="synthetic-audited-scoring", version=1,
            created_by=cls.actor, algorithm_version=scoring.ENGINE_VERSION,
            boundary_policy=scoring.BOUNDARY_POLICY, numeric_precision=scoring.NUMERIC_PRECISION).publish()
        cls.target = TargetVersion.objects.create(code="synthetic-scoring-target", version=1,
            name="Synthetic concentric geometry", width_mm=Decimal("200"), height_mm=Decimal("300"), created_by=cls.actor)
        for number, radius, score in ((1, "10", 10), (2, "30", 8), (3, "50", 5)):
            TargetRing.objects.create(target_version=cls.target, ring_number=number,
                outer_radius_mm=Decimal(radius), score=score)
        cls.target.refresh_from_db()
        cls.target.publish()

    def draft(self, *, points=True):
        assessment = self.complete_draft()
        if points:
            for number, x, included in ((1, "40", True), (2, "47.25", True), (3, "77.25", True), (4, "10", False)):
                self.make_point(assessment, point_number=number, x_px=Decimal(x), y_px=Decimal("30"),
                    is_included=included, exclusion_reason="" if included else "Synthetic exclusion")
        assessment.refresh_from_db()
        return assessment

    def args(self, assessment, **changes):
        assessment.refresh_from_db()
        calibration, rule = assessment.calibration, assessment.scoring_rule_version
        target = calibration.target_version
        return {**dict(actor=self.actor, session_id=self.session.pk, assessment_id=assessment.pk,
            expected_edit_version=assessment.edit_version, operation_id=uuid4(), reason="Synthetic draft score",
            expected_evidence_versions={name: {"id": row.pk, "edit_version": row.edit_version}
                for name, row in (("calibration", calibration), ("target_version", target), ("scoring_rule_version", rule))}), **changes}

    def test_scores_entire_aggregate_and_persists_one_version_step_and_audit(self):
        assessment = self.draft()
        before_version = assessment.edit_version
        result = commands.score_draft(**self.args(assessment))
        assessment.refresh_from_db()
        event = self.assert_audited(result, action=commands.ACTION, previous_version=before_version)
        self.assertEqual(list(assessment.shot_points.order_by("point_number").values_list("score", flat=True)), [10, 10, 0, None])
        self.assertEqual(assessment.total_score, 20)
        self.assertEqual(assessment.status, "draft")
        self.assertEqual(assessment.edit_version, before_version + 1)
        self.assertEqual(assessment.scored_edit_version, assessment.edit_version)
        self.assertEqual(assessment.scoring_engine_version, scoring.ENGINE_VERSION)
        self.assertIsNotNone(assessment.scored_at)
        self.assertEqual(AuditEvent.objects.count(), 1)
        self.assertEqual(len(assessment.scoring_context["input"]["target_version"]["rings"]), 3)
        self.assertEqual(len(assessment.scoring_context["input"]["points"]), 4)
        self.assertEqual(assessment.scoring_context["input"]["assessment"]["edit_version"], before_version)
        digest = hashlib.sha256(json.dumps(assessment.scoring_context["input"], sort_keys=True,
            separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        self.assertEqual(assessment.scoring_input_sha256, digest)
        self.assertEqual(event.after_state["scoring_context"], assessment.scoring_context)
        self.assertIsNone(event.actor_participation_id)
        self.assertIsNone(event.activity_id)
        self.assertIsNone(event.authentication_context)

    def test_empty_draft_is_zero_and_all_excluded_points_remain_null(self):
        assessment = self.draft(points=False)
        commands.score_draft(**self.args(assessment))
        assessment.refresh_from_db()
        self.assertEqual(assessment.total_score, 0)
        self.command(services.add_shot_point, assessment=assessment,
            data={"x_px": 40, "y_px": 30, "is_included": False, "exclusion_reason": "Synthetic"})
        commands.score_draft(**self.args(assessment))
        assessment.refresh_from_db()
        self.assertEqual(assessment.total_score, 0)
        self.assertIsNone(assessment.shot_points.get().score)

    def test_caller_cannot_supply_partial_collections_scores_or_geometry(self):
        assessment = self.draft()
        before = self.database_state()
        for field, value in (("points", []), ("rings", []), ("total_score", 999), ("score", 999),
                             ("caliber_mm", 100), ("transform_matrix", []), ("actor_id", self.actor.pk), ("data", {})):
            with self.subTest(field=field), self.assertRaises(TypeError):
                commands.score_draft(**{**self.args(assessment), field: value})
        self.assertEqual(self.database_state(), before)

    def test_missing_global_permission_is_denied_despite_stale_cache(self):
        assessment = self.draft()
        self.assertTrue(self.actor.has_perm("assessments.change_assessment"))
        self.actor.user_permissions.clear()
        before = self.database_state()
        with self.assertRaises(PermissionDenied):
            commands.score_draft(**self.args(assessment))
        self.assertEqual(self.database_state(), before)

    def test_missing_scope_is_denied_even_for_staff_superuser(self):
        assessment = self.draft()
        self.actor.is_staff = self.actor.is_superuser = True
        self.actor.save()
        grant = ActivityPermissionGrant.objects.get(user=self.actor, permission__codename="change_assessment")
        grant.is_active = False
        grant.save()
        before = self.database_state()
        with self.assertRaises(PermissionDenied):
            commands.score_draft(**self.args(assessment))
        self.assertEqual(self.database_state(), before)

    def test_inactive_or_unassigned_user_is_denied(self):
        assessment = self.draft()
        other = self.grant(User.objects.create_user("synthetic-other-scorer@example.com"))
        before = self.database_state()
        with self.assertRaises(PermissionDenied):
            commands.score_draft(**self.args(assessment, actor=other))
        self.assertEqual(self.database_state(), before)
        User.objects.filter(pk=self.actor.pk).update(is_active=False)
        with self.assertRaises(PermissionDenied):
            commands.score_draft(**self.args(assessment))
        self.assertEqual(self.database_state(), before)

    def test_cross_activity_access_is_denied_even_with_session_assignment(self):
        assessment = self.draft()
        activity = Activity.objects.create(name="Synthetic other activity", training_date=self.session.participation.activity.training_date,
            status="active", created_by=self.actor)
        participation = ActivityParticipation.objects.create(activity=activity, participant=self.session.participation.participant,
            registration_status="approved", participant_identifier="other-activity", decided_by=self.actor,
            decided_at=self.session.participation.decided_at)
        session = TrainingSession.objects.create(participation=participation, session_number=1,
            assigned_assessor=self.actor, created_by=self.actor, weapon_type="pistol", shooting_position="berdiri",
            distance_m=Decimal("10"), caliber_mm=Decimal("9"))
        before = self.database_state()
        with self.assertRaises(PermissionDenied):
            commands.score_draft(**self.args(assessment, session_id=session.pk))
        self.assertEqual(self.database_state(), before)

    def test_authorization_is_rechecked_under_locks(self):
        assessment = self.draft()
        original = commands.lock_session_reference
        def revoke_then_lock(*args):
            User.objects.filter(pk=self.actor.pk).update(is_active=False)
            return original(*args)
        before = self.database_state()
        with patch.object(commands, "lock_session_reference", side_effect=revoke_then_lock), self.assertRaises(PermissionDenied):
            commands.score_draft(**self.args(assessment))
        self.assertEqual(self.database_state(), before)
        self.assertTrue(User.objects.get(pk=self.actor.pk).is_active)  # Entire transaction rolled back.

    def test_stale_assessment_version_rejects_without_scoring_or_writes(self):
        assessment = self.draft()
        values = self.args(assessment)
        self.command(services.update_draft, assessment=assessment, data={"notes": "Newer edit"})
        before = self.database_state()
        with patch.object(scoring, "score_target") as engine, self.assertRaises(ValidationError):
            commands.score_draft(**values)
        engine.assert_not_called()
        self.assertEqual(self.database_state(), before)

    def test_each_stale_evidence_version_or_identity_is_rejected(self):
        assessment = self.draft()
        values = self.args(assessment)
        before = self.database_state()
        for name in commands.EVIDENCE_NAMES:
            for key in ("id", "edit_version"):
                changed = copy.deepcopy(values["expected_evidence_versions"])
                changed[name][key] += 100
                with self.subTest(name=name, key=key), self.assertRaises(ValidationError):
                    commands.score_draft(**{**values, "expected_evidence_versions": changed})
                self.assertEqual(self.database_state(), before)

    def test_invalid_command_tokens_fail_closed(self):
        assessment = self.draft()
        before = self.database_state()
        for changes in ({"expected_edit_version": True}, {"expected_edit_version": 0}, {"operation_id": "invalid"},
                {"reason": " "}, {"expected_evidence_versions": {}}, {"expected_evidence_versions": None}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                commands.score_draft(**self.args(assessment, **changes))
        values = self.args(assessment)
        values["expected_evidence_versions"]["calibration"]["edit_version"] = True
        with self.assertRaises(ValidationError):
            commands.score_draft(**values)
        self.assertEqual(self.database_state(), before)

    def test_retirement_rejects_stale_expectation_but_explicit_current_reference_can_score(self):
        assessment = self.draft()
        values = self.args(assessment)
        self.target.retire()
        before = self.database_state()
        with self.assertRaises(ValidationError):
            commands.score_draft(**values)
        self.assertEqual(self.database_state(), before)
        commands.score_draft(**self.args(assessment))
        assessment.refresh_from_db()
        self.assertEqual(assessment.total_score, 20)

    def test_missing_evidence_and_mismatched_photo_calibration_are_rejected(self):
        assessment = self.draft()
        other = self.accept(self.make_calibration())
        self.raw(Assessment).filter(pk=assessment.pk).update(calibration_id=other.pk)
        before = self.database_state()
        with self.assertRaises(ValidationError):
            commands.score_draft(**self.args(assessment))
        self.assertEqual(self.database_state(), before)
        values = self.args(assessment)
        self.raw(Assessment).filter(pk=assessment.pk).update(calibration_id=None)
        before = self.database_state()
        with self.assertRaises(ValidationError):
            commands.score_draft(**values)
        self.assertEqual(self.database_state(), before)

    def test_sql_session_context_drift_is_rejected(self):
        assessment = self.draft()
        values = self.args(assessment)
        self.raw(TrainingSession).filter(pk=self.session.pk).update(distance_m=Decimal("20"))
        before = self.database_state()
        with self.assertRaises(ValidationError):
            commands.score_draft(**values)
        self.assertEqual(self.database_state(), before)

    def test_invalid_point_coordinate_or_manual_provenance_is_rejected(self):
        assessment = self.draft()
        point = assessment.shot_points.first()
        for changes in ({"x_px": Decimal("80")}, {"source": "ai", "origin_detection_id": "forged-origin"}):
            self.raw(ShotPoint).filter(pk=point.pk).update(**changes)
            before = self.database_state()
            with self.subTest(fields=list(changes)), self.assertRaises(ValidationError):
                commands.score_draft(**self.args(assessment))
            self.assertEqual(self.database_state(), before)
            self.raw(ShotPoint).filter(pk=point.pk).update(**{field: getattr(point, field) for field in changes})

    def test_projective_infinity_fails_atomically(self):
        photo = self.make_photo()
        calibration = self.accept(self.make_calibration(photo=photo,
            transform_matrix=[[1, 0, 0], [0, 1, 0], [1, 0, -40]]))
        assessment = self.make_assessment(photo=photo, calibration=calibration, scoring_rule_version=self.rule)
        self.make_point(assessment, x_px=Decimal("40"), y_px=Decimal("30"))
        before = self.database_state()
        with self.assertRaisesMessage(ValidationError, "projective infinity"):
            commands.score_draft(**self.args(assessment))
        self.assertEqual(self.database_state(), before)

    def test_corrupt_ring_geometry_is_rejected_without_scores(self):
        assessment = self.draft()
        self.raw(TargetRing).filter(target_version=self.target, ring_number=2).update(outer_radius_mm=Decimal("5"))
        before = self.database_state()
        with self.assertRaises(ValidationError):
            commands.score_draft(**self.args(assessment))
        self.assertEqual(self.database_state(), before)

    def test_ai_points_require_real_selected_detection_and_keep_provenance(self):
        run = self.finish_run(result=RESULT)
        assessment = self.make_assessment(photo=run.photo, detection_run=run,
            calibration=self.accept(self.make_calibration(photo=run.photo)), scoring_rule_version=self.rule)
        point = self.make_point(assessment, x_px=Decimal("40"), y_px=Decimal("30"), source="ai", origin_detection_id="synthetic-1")
        commands.score_draft(**self.args(assessment))
        point.refresh_from_db()
        self.assertEqual((point.score, point.source, point.origin_detection_id), (10, "ai", "synthetic-1"))
        self.raw(ShotPoint).filter(pk=point.pk).update(origin_detection_id="missing-detection")
        before = self.database_state()
        with self.assertRaises(ValidationError):
            commands.score_draft(**self.args(assessment))
        self.assertEqual(self.database_state(), before)

    def test_unsupported_rule_fails_without_writes(self):
        assessment = self.draft()
        self.raw(ScoringRuleVersion).filter(pk=self.rule.pk).update(algorithm_version="unsupported-synthetic-rule")
        before = self.database_state()
        with self.assertRaises(ValidationError):
            commands.score_draft(**self.args(assessment))
        self.assertEqual(self.database_state(), before)

    def test_engine_exception_rolls_back_without_partial_scores(self):
        assessment = self.draft()
        before = self.database_state()
        with patch.object(scoring, "score_target", side_effect=RuntimeError("Synthetic engine failure")):
            with self.assertRaises(RuntimeError):
                commands.score_draft(**self.args(assessment))
        self.assertEqual(self.database_state(), before)

    def test_audit_failure_rolls_back_scores_context_timestamps_and_version(self):
        assessment = self.draft()
        values = self.args(assessment)
        before = self.database_state()
        for failure in (ValidationError("Synthetic audit failure"), RuntimeError("Unexpected synthetic audit failure")):
            with patch.object(AuditEvent, "save", side_effect=failure), self.assertRaises(type(failure)):
                commands.score_draft(**values)
            self.assertEqual(self.database_state(), before)
        commands.score_draft(**values)  # Failed attempts did not reserve the UUID.

    def test_replay_returns_historical_receipt_without_recalculation_or_version_bump(self):
        assessment = self.draft()
        values = self.args(assessment)
        receipt = commands.score_draft(**values)
        self.command(services.update_draft, assessment=assessment, data={"notes": "Invalidate later"})
        self.target.retire()
        before = self.database_state()
        with patch.object(scoring, "score_target") as engine:
            self.assertEqual(commands.score_draft(**values), receipt)
        engine.assert_not_called()
        self.assertEqual(self.database_state(), before)

    def test_revoked_scope_or_permission_denies_replay(self):
        assessment = self.draft()
        values = self.args(assessment)
        commands.score_draft(**values)
        grant = ActivityPermissionGrant.objects.get(user=self.actor, permission__codename="change_assessment")
        grant.is_active = False
        grant.save()
        before = self.database_state()
        with self.assertRaises(PermissionDenied):
            commands.score_draft(**values)
        self.assertEqual(self.database_state(), before)
        grant.is_active = True
        grant.save()
        self.actor.user_permissions.clear()
        with self.assertRaises(PermissionDenied):
            commands.score_draft(**values)

    def test_uuid_collision_and_different_command_payload_are_conflicts(self):
        assessment = self.draft()
        values = self.args(assessment)
        receipt = commands.score_draft(**values)
        before = self.database_state()
        for changes in ({"reason": "Another reason"}, {"expected_edit_version": receipt.edit_version}):
            with self.subTest(changes=changes), self.assertRaises(services.OperationConflict):
                commands.score_draft(**{**values, **changes})
        self.assertEqual(self.database_state(), before)
        with self.assertRaises(services.OperationConflict):
            self.command(services.update_draft, assessment=assessment, operation_id=values["operation_id"], data={"notes": "UUID collision"})
        self.assertEqual(self.database_state(), before)

    def test_existing_uuid_from_other_action_is_rejected_before_mutation(self):
        assessment = self.draft()
        values = self.args(assessment)
        AuditEvent.objects.create(operation_id=values["operation_id"], actor_kind="user", actor_user=self.actor,
            session=self.session, assessment=assessment, action="synthetic.other", before_state={"schema_version": 1}, after_state={"schema_version": 1})
        before = self.database_state()
        with self.assertRaises(services.OperationConflict):
            commands.score_draft(**values)
        self.assertEqual(self.database_state(), before)

    def test_old_update_add_move_and_delete_commands_invalidate_every_scoring_field(self):
        assessment = self.draft()
        operations = ((services.update_draft, {"data": {"notes": "Edited"}}),
            (services.add_shot_point, {"data": {"x_px": 40, "y_px": 30}}),
            (services.update_shot_point, {"data": {"x_px": 41}}), (services.delete_shot_point, {}))
        for function, changes in operations:
            commands.score_draft(**self.args(assessment))
            assessment.refresh_from_db()
            scored_version = assessment.edit_version
            if function in (services.update_shot_point, services.delete_shot_point):
                changes = {**changes, "point": assessment.shot_points.order_by("point_number").first()}
            self.command(function, assessment=assessment, **changes)
            assessment.refresh_from_db()
            with self.subTest(command=function.__name__):
                self.assertEqual(assessment.edit_version, scored_version + 1)
                for field, empty in services.SCORING_FIELDS.items():
                    self.assertEqual(getattr(assessment, field), empty)
                self.assertFalse(assessment.shot_points.filter(score__isnull=False).exists())

    def test_old_command_audit_failure_preserves_preexisting_scored_cache(self):
        assessment = self.draft()
        commands.score_draft(**self.args(assessment))
        before = self.database_state()
        with patch.object(AuditEvent, "save", side_effect=ValidationError("Synthetic failure")), self.assertRaises(ValidationError):
            self.command(services.update_shot_point, assessment=assessment, point=assessment.shot_points.first(), data={"x_px": 42})
        self.assertEqual(self.database_state(), before)

    def test_json_contains_exact_ratios_no_floats_decimals_or_authentication_secrets(self):
        photo = self.make_photo()
        calibration = self.accept(self.make_calibration(photo=photo, transform_matrix=[[1, 0, 0], [0, 1, 0], [0.01, 0, 1]]))
        assessment = self.make_assessment(photo=photo, calibration=calibration, scoring_rule_version=self.rule)
        self.make_point(assessment, x_px=Decimal("1"), y_px=Decimal("2"))
        receipt = commands.score_draft(**self.args(assessment))
        assessment.refresh_from_db()
        self.assertEqual(assessment.scoring_context["results"][0]["x_mm"], {"numerator": "100", "denominator": "101"})
        self.assertEqual(assessment.scoring_context["results"][0]["y_mm"], {"numerator": "200", "denominator": "101"})
        def inspect(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    self.assertNotIn(key, {"password", "password_hash", "token", "cookie", "session_key", "storage_key", "parameters", "raw_result"})
                    inspect(item)
            elif isinstance(value, list):
                for item in value:
                    inspect(item)
            else:
                self.assertTrue(value is None or type(value) in (str, int, bool))
        event = AuditEvent.objects.get(pk=receipt.audit_event_id)
        inspect(event.before_state)
        inspect(event.after_state)
        json.dumps(assessment.scoring_context, allow_nan=False)
        with localcontext() as context:
            context.prec = 2
            self.assertEqual(commands._decimal_text(Decimal("123456.123456")), "123456.123456")
            self.assertEqual(commands._decimal_text(0.01), "0.01")

    def test_verified_and_locked_states_remain_immutable(self):
        assessment = self.privileged_state(status="verified")
        for status in ("verified", "locked"):
            if status == "locked":
                self.raw(Assessment).filter(pk=assessment.pk).update(status="locked", locked_by_id=self.actor.pk, locked_at=assessment.verified_at)
            before = self.database_state()
            with self.subTest(status=status), self.assertRaises(ValidationError):
                commands.score_draft(**self.args(assessment))
            self.assertEqual(self.database_state(), before)

    def test_direct_public_model_and_queryset_score_writes_still_reject(self):
        assessment = self.draft()
        commands.score_draft(**self.args(assessment))
        assessment.refresh_from_db()
        before = self.database_state()
        with self.assertRaises(ValidationError):
            assessment.save(expected_edit_version=assessment.edit_version)
        point = assessment.shot_points.filter(is_included=True).first()
        with self.assertRaises(ValidationError):
            point.save(expected_edit_version=assessment.edit_version)
        with self.assertRaises(ValidationError):
            ShotPoint.objects.filter(pk=point.pk).update(score=999)
        self.assertEqual(self.database_state(), before)

    def test_lock_order_preserves_domain_then_evidence_then_authorization_suffix(self):
        assessment = self.draft()
        with traced_locks() as trace:
            commands.score_draft(**self.args(assessment))
        first = []
        for name, ids, joined in trace:
            self.assertFalse(joined)
            if ids and name not in first:
                self.assertEqual(ids, sorted(ids))
                first.append(name)
        self.assertEqual(first, ["TrainingSession", "ActivityParticipation", "Assessment", "ShotPoint",
            "TargetPhoto", "Calibration", "TargetVersion", "TargetRing", "ScoringRuleVersion", "User", "ActivityPermissionGrant"])
