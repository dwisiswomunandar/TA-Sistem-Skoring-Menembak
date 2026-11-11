"""Synthetic metrology/planar geometry only; no field-accuracy or AI claims."""

import copy
from decimal import Context, Decimal, localcontext
from fractions import Fraction
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.db import models
from django.test import SimpleTestCase, override_settings
from django.utils import timezone

from activities.models import ActivityPermissionGrant, TrainingSession
from activities.test_history import traced_locks
from assessments import calibration_math as geometry, calibration_services as commands
from assessments import detection_import_services, scoring, scoring_services, services
from assessments.models import Assessment, AuditEvent, Calibration, DetectionRun, ScoringRuleVersion, ShotPoint, TargetPhoto, TargetRing, TargetVersion
from assessments.querysets import ResultQuerySet
from assessments.test_services import ServiceTestCase

User = get_user_model()
FIT = [[10, 10, -10, 10], [50, 10, 10, 10], [50, 50, 10, -10], [10, 50, -10, -10]]
CHECKS = [[30, 30, 0, 0]]
OBSERVATIONS = {key: row[:2] for key, row in zip(("A", "B", "C", "D", "E"), FIT + CHECKS)}


class CalibrationGeometryTests(SimpleTestCase):
    def fit(self, **changes):
        values = dict(width_px=80, height_px=60, fit_pairs=copy.deepcopy(FIT), check_pairs=copy.deepcopy(CHECKS),
            max_error_mm="0.01", min_area_ratio="0.01", min_denominator_ratio="0.01")
        return geometry.fit_transform(**{**values, **changes})

    def project_with_existing_engine(self, matrix, x, y, width=80, height=60):
        data = scoring.ScoringInput(width, height, 200, 300, scoring.COORDINATE_CONTRACT,
            tuple(tuple(row) for row in matrix), 9, (scoring.RingInput(1, 10, 1),),
            (scoring.PointInput(1, x, y, True),), scoring.RuleInput("inward", scoring.ENGINE_VERSION,
                scoring.BOUNDARY_POLICY, scoring.NUMERIC_PRECISION))
        return scoring.score_target(data).points[0]

    def test_measured_ring_origin_is_not_inferred_from_photo_or_paper_centre(self):
        matrix, report = self.fit()
        origin = self.project_with_existing_engine(matrix, 30, 30)
        middle = self.project_with_existing_engine(matrix, 40, 30)
        self.assertEqual((origin.x_mm, origin.y_mm), (0, 0))
        self.assertEqual((middle.x_mm, middle.y_mm), (5, 0))
        self.assertEqual(report["max_check_error_squared_mm2"], {"numerator": "0", "denominator": "1"})
        self.assertTrue(all(type(value) is int for row in matrix for value in row))

    def test_integer_homography_preserves_exact_inward_boundary_for_existing_engine(self):
        matrix, _ = self.fit()
        self.assertEqual(self.project_with_existing_engine(matrix, 59, 30).score, 1)
        self.assertEqual(self.project_with_existing_engine(matrix, 60, 30).score, 0)

    def test_perspective_transform_recovers_known_coordinates(self):
        fit = [[0, 0, -100, 100], [100, 0, 0, 50], [100, 200, 0, -50], [0, 200, -100, -100]]
        matrix, _ = self.fit(width_px=400, height_px=300, fit_pairs=fit, check_pairs=[[100, 100, 0, 0]])
        self.assertNotEqual(matrix[2][0], 0)
        point = self.project_with_existing_engine(matrix, 300, 100, width=400, height=300)
        self.assertEqual((point.x_mm, point.y_mm), (50, 0))

    def test_exact_solution_is_independent_of_global_decimal_context(self):
        expected = self.fit()
        with localcontext(Context(prec=2)):
            self.assertEqual(self.fit(), expected)

    def test_duplicate_collinear_and_bow_tie_landmarks_are_rejected(self):
        for fit in ([FIT[0], FIT[0], FIT[2], FIT[3]], [FIT[1], FIT[0], FIT[2], FIT[3]],
                    [[10, 10, -10, 10], [20, 20, 10, 10], [30, 30, 10, -10], [40, 40, -10, -10]]):
            with self.subTest(fit=fit), self.assertRaises(geometry.CalibrationError):
                self.fit(fit_pairs=fit)
        collinear = [row[:2] + [index, index] for index, row in enumerate(FIT)]
        with self.assertRaises(geometry.CalibrationError):
            self.fit(fit_pairs=collinear)

    def test_near_collinear_geometry_fails_approved_dimensionless_limit(self):
        fit = [[10, 10, -10, 10], [50, 10, 10, 10], [50, 10.000001, 10, -10], [10, 10.000001, -10, -10]]
        with self.assertRaisesRegex(geometry.CalibrationError, "collinear"):
            self.fit(fit_pairs=fit, check_pairs=[[30, 10.0000005, 0, 0]])

    def test_independent_check_error_is_not_hidden_by_zero_fit_residual(self):
        with self.assertRaisesRegex(geometry.CalibrationError, "residual"):
            self.fit(check_pairs=[[31, 30, 0, 0]])
        with self.assertRaises(geometry.CalibrationError):
            self.fit(check_pairs=[])
        with self.assertRaises(geometry.CalibrationError):
            self.fit(check_pairs=[FIT[0]])
        with self.assertRaisesRegex(geometry.CalibrationError, "hull"):
            self.fit(check_pairs=[[60, 30, 0, 0]])

    def test_projective_horizon_crossing_photo_is_rejected(self):
        fit = [[10, 10, 0.2, 0.2], [20, 10, 0.5, 0.25], [20, 20, 0.5, 0.5], [10, 20, 0.2, 0.4]]
        with self.assertRaisesRegex(geometry.CalibrationError, "horizon"):
            self.fit(fit_pairs=fit, check_pairs=[[10, 15, 0.2, 0.3]])

    def test_near_horizon_transform_is_rejected_without_adding_a_scoring_epsilon(self):
        fit = [[x, y, x / (80.01 - x), y / (80.01 - x)] for x, y in ((10, 10), (20, 10), (20, 20), (10, 20))]
        with self.assertRaisesRegex(geometry.CalibrationError, "horizon"):
            self.fit(fit_pairs=fit, check_pairs=[[15, 15, 15 / 65.01, 15 / 65.01]])

    def test_invalid_dimensions_coordinates_and_quality_limits_are_rejected(self):
        for changes in ({"width_px": 0}, {"height_px": True}, {"max_error_mm": 0}, {"min_area_ratio": 0},
                        {"min_denominator_ratio": 1}, {"fit_pairs": FIT[:3]}):
            with self.subTest(changes=changes), self.assertRaises(geometry.CalibrationError):
                self.fit(**changes)
        for value in (True, "bad", "NaN", float("inf"), [], -1, 80):
            fit = copy.deepcopy(FIT)
            fit[0][0] = value
            with self.subTest(value=value), self.assertRaises(geometry.CalibrationError):
                self.fit(fit_pairs=fit)


class CalibrationServiceTests(ServiceTestCase):
    def setUp(self):
        super().setUp()
        # Explicit synthetic measured geometry; these are not operational defaults.
        self.profile = {"id": "synthetic-measured-v1", "target_version_id": self.target.pk,
            "width_mm": "200", "height_mm": "300", "measurement_reference": "Synthetic fixture; no field measurement",
            "coordinate_contract": scoring.COORDINATE_CONTRACT,
            "fit_landmarks_mm": [{"id": key, "x_mm": row[2], "y_mm": row[3]} for key, row in zip(("A", "B", "C", "D"), FIT)],
            "check_landmarks_mm": [{"id": "E", "x_mm": 0, "y_mm": 0}],
            "max_error_mm": "0.01", "min_area_ratio": "0.01", "min_denominator_ratio": "0.01"}
        self.enterContext(override_settings(TARGET_CALIBRATION_PROFILES=(self.profile,)))

    def draft(self):
        return self.make_assessment(photo=self.make_photo(), scoring_rule_version=self.rule)

    def args(self, assessment, **changes):
        assessment.refresh_from_db()
        self.target.refresh_from_db()
        _, fingerprint = commands.load_profile(self.profile["id"])
        return {**dict(actor=self.actor, session_id=assessment.session_id, assessment_id=assessment.pk,
            photo_id=assessment.photo_id, target_version_id=self.target.pk,
            expected_edit_version=assessment.edit_version, expected_target_edit_version=self.target.edit_version,
            profile_id=self.profile["id"], expected_profile_sha256=fingerprint,
            observations=copy.deepcopy(OBSERVATIONS), operation_id=uuid4(), reason="Synthetic measured calibration"), **changes}

    def state(self):
        return {model: list(model.objects.order_by("pk").values()) for model in
            (TrainingSession, Assessment, ShotPoint, TargetPhoto, DetectionRun, Calibration,
             TargetVersion, TargetRing, ScoringRuleVersion, AuditEvent)}

    def test_creates_accepted_calibration_and_one_audited_assessment_step(self):
        assessment = self.draft()
        history = copy.deepcopy(assessment.historical_context)
        receipt = commands.calibrate_draft(**self.args(assessment))
        assessment.refresh_from_db()
        calibration = assessment.calibration
        self.assertEqual((calibration.status, calibration.edit_version), ("accepted", 2))
        self.assertEqual((calibration.created_by_id, calibration.accepted_by_id), (self.actor.pk, self.actor.pk))
        self.assertEqual((calibration.photo_id, calibration.target_version_id), (assessment.photo_id, self.target.pk))
        self.assertEqual(calibration.method_version, geometry.METHOD_VERSION)
        self.assertEqual((assessment.edit_version, assessment.status, assessment.total_score), (2, "draft", None))
        self.assertEqual(assessment.historical_context, history)
        event = self.assert_audited(receipt, action=commands.ACTION, previous_version=1)
        self.assertEqual(AuditEvent.objects.count(), 1)
        details = event.after_state["calibration_result"]
        self.assertEqual(details["id"], calibration.pk)
        self.assertEqual(details["measured_profile"]["measurement_reference"], self.profile["measurement_reference"])
        self.assertEqual(details["transform_matrix"], calibration.transform_matrix)
        self.assertEqual(details["photo_sha256"], assessment.photo.sha256)
        with self.assertRaises(ValidationError):
            calibration.save()

    def test_missing_unmeasured_or_invalid_server_profile_fails_closed(self):
        assessment = self.draft()
        args = self.args(assessment)
        before = self.state()
        for profiles in ((), ({**self.profile, "measurement_reference": ""},),
                         ({**self.profile, "check_landmarks_mm": []},),
                         ({**self.profile, "max_error_mm": "NaN"},),
                         ({**self.profile, "coordinate_contract": "paper-centre"},)):
            with self.subTest(profiles=profiles), override_settings(TARGET_CALIBRATION_PROFILES=profiles):
                with self.assertRaises(ImproperlyConfigured):
                    commands.calibrate_draft(**args)
            self.assertEqual(self.state(), before)

    def test_stale_target_profile_and_assessment_versions_fail_without_writes(self):
        assessment = self.draft()
        args = self.args(assessment)
        for changes in ({"expected_edit_version": 99}, {"expected_target_edit_version": 99},
                        {"expected_profile_sha256": "f" * 64}, {"expected_profile_sha256": None}):
            before = self.state()
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                commands.calibrate_draft(**{**args, **changes})
            self.assertEqual(self.state(), before)

    def test_profile_dimensions_and_landmarks_must_match_published_target(self):
        assessment = self.draft()
        for changes in ({"width_mm": "201"}, {"height_mm": "301"}, {"target_version_id": self.target.pk + 1}):
            with override_settings(TARGET_CALIBRATION_PROFILES=({**self.profile, **changes},)):
                args = self.args(assessment)
                before = self.state()
                with self.assertRaises(ValidationError):
                    commands.calibrate_draft(**args)
                self.assertEqual(self.state(), before)
        bad = {**self.profile, "fit_landmarks_mm": [{"id": "A", "x_mm": 1000, "y_mm": 0}, *self.profile["fit_landmarks_mm"][1:]]}
        with override_settings(TARGET_CALIBRATION_PROFILES=(bad,)), self.assertRaises(ImproperlyConfigured):
            commands.load_profile(self.profile["id"])

    def test_retired_or_draft_target_cannot_be_used_for_new_acceptance(self):
        assessment = self.draft()
        published_at = self.target.published_at
        for status in ("draft", "retired"):
            self.raw(TargetVersion).filter(pk=self.target.pk).update(status=status,
                published_at=None if status == "draft" else published_at)
            before = self.state()
            with self.subTest(status=status), self.assertRaises(ValidationError):
                commands.calibrate_draft(**self.args(assessment))
            self.assertEqual(self.state(), before)

    def test_bad_observations_geometry_and_independent_error_do_not_create_calibration(self):
        assessment = self.draft()
        base = self.args(assessment)
        cases = [None, {}, {**OBSERVATIONS, "extra": [1, 1]}, {**OBSERVATIONS, "E": [31, 30]},
                 {**OBSERVATIONS, "E": [80, 30]}, {**OBSERVATIONS, "A": [True, 10]},
                 {**OBSERVATIONS, "A": ["NaN", 10]}, {**OBSERVATIONS, "B": OBSERVATIONS["A"]}]
        for observations in cases:
            before = self.state()
            with self.subTest(observations=observations), self.assertRaises(ValidationError):
                commands.calibrate_draft(**{**base, "observations": observations})
            self.assertEqual(self.state(), before)

    def test_photo_session_and_selected_draft_integrity_are_required(self):
        assessment = self.draft()
        other_photo = self.make_photo()
        before = self.state()
        with self.assertRaises(ValidationError):
            commands.calibrate_draft(**self.args(assessment, photo_id=other_photo.pk))
        self.assertEqual(self.state(), before)
        other_session = TrainingSession.objects.create(participation=self.session.participation, session_number=2,
            assigned_assessor=self.actor, created_by=self.actor, weapon_type="pistol", distance_m=20)
        other_photo = self.make_photo(session=other_session)
        before = self.state()
        with self.assertRaises(ValidationError):
            commands.calibrate_draft(**self.args(assessment, photo_id=other_photo.pk))
        self.assertEqual(self.state(), before)

    def test_latest_revision_and_historical_session_drift_are_rejected(self):
        assessment = self.draft()
        before = self.state()
        with patch.object(ResultQuerySet, "latest_working", return_value=SimpleNamespace(pk=assessment.pk + 1)):
            with self.assertRaises(ValidationError):
                commands.calibrate_draft(**self.args(assessment))
        self.assertEqual(self.state(), before)
        self.raw(TrainingSession).filter(pk=self.session.pk).update(distance_m=Decimal("20"))
        before = self.state()
        with self.assertRaises(ValidationError):
            commands.calibrate_draft(**self.args(assessment))
        self.assertEqual(self.state(), before)

    def test_authorization_requires_global_permission_scope_and_assignment(self):
        assessment = self.draft()
        other = User.objects.create_user("other-calibrator@example.com")
        self.grant(other)
        before = self.state()
        for actor in (other, self.actor.pk, User(email="unsaved-calibrator@example.com")):
            with self.subTest(actor=actor), self.assertRaises(PermissionDenied):
                commands.calibrate_draft(**self.args(assessment, actor=actor))
        self.assertEqual(self.state(), before)
        grants = self.raw(ActivityPermissionGrant).filter(user=self.actor, permission__codename="change_assessment")
        grants.update(is_active=False)
        with self.assertRaises(PermissionDenied):
            commands.calibrate_draft(**self.args(assessment))
        grants.update(is_active=True)
        permission = self.actor.user_permissions.get(codename="change_assessment")
        self.actor.user_permissions.remove(permission)
        with self.assertRaises(PermissionDenied):
            commands.calibrate_draft(**self.args(assessment))
        self.assertEqual(self.state(), before)

    def test_replay_survives_profile_disable_without_restoring_later_state(self):
        assessment = self.draft()
        args = self.args(assessment)
        receipt = commands.calibrate_draft(**args)
        self.command(services.update_draft, assessment=assessment, data={"notes": "Later review"})
        before = self.state()
        with override_settings(TARGET_CALIBRATION_PROFILES=()):
            self.assertEqual(commands.calibrate_draft(**args), receipt)
        self.assertEqual(self.state(), before)
        self.raw(ActivityPermissionGrant).filter(user=self.actor, permission__codename="change_assessment").update(is_active=False)
        with self.assertRaises(PermissionDenied):
            commands.calibrate_draft(**args)
        self.assertEqual(self.state(), before)

    def test_uuid_payload_conflict_and_stale_repeat_do_not_create_more_calibrations(self):
        assessment = self.draft()
        args = self.args(assessment)
        commands.calibrate_draft(**args)
        before = self.state()
        with self.assertRaises(services.OperationConflict):
            commands.calibrate_draft(**{**args, "reason": "Different request"})
        with self.assertRaises(ValidationError):
            commands.calibrate_draft(**{**args, "operation_id": uuid4()})
        with self.assertRaises(services.OperationConflict):
            self.command(services.update_draft, assessment=assessment, operation_id=args["operation_id"], data={"notes": "Different command"})
        self.assertEqual(self.state(), before)

    def test_client_cannot_supply_matrix_millimetres_or_quality_overrides(self):
        assessment = self.draft()
        before = self.state()
        for key in ("transform_matrix", "landmarks_mm", "width_mm", "max_error_mm", "actor_id", "total_score"):
            with self.subTest(key=key), self.assertRaises(TypeError):
                commands.calibrate_draft(**self.args(assessment), **{key: 1})
        self.assertEqual(self.state(), before)

    def score(self, assessment):
        assessment.refresh_from_db()
        calibration = assessment.calibration
        return scoring_services.score_draft(actor=self.actor, session_id=self.session.pk,
            assessment_id=assessment.pk, expected_edit_version=assessment.edit_version,
            expected_evidence_versions={name: {"id": row.pk, "edit_version": row.edit_version}
                for name, row in (("calibration", calibration), ("target_version", calibration.target_version),
                                  ("scoring_rule_version", assessment.scoring_rule_version))},
            operation_id=uuid4(), reason="Synthetic post-calibration score")

    def detected_draft(self):
        result = {"schema_version": 1, "detections": [
            {"id": key, "class_id": 0, "confidence": 0.9, "bbox_xyxy": [x - 1, 29, x + 1, 31]}
            for key, x in (("centre", 30), ("touch", 59), ("miss", 60))]}
        run = self.finish_run(result=result)
        rule = ScoringRuleVersion.objects.create(code=uuid4().hex, version=1, created_by=self.actor,
            algorithm_version=scoring.ENGINE_VERSION, boundary_policy=scoring.BOUNDARY_POLICY,
            numeric_precision=scoring.NUMERIC_PRECISION).publish()
        assessment = self.make_assessment(photo=run.photo, detection_run=run, scoring_rule_version=rule)
        detection_import_services.import_detection_points(actor=self.actor, session_id=self.session.pk,
            assessment_id=assessment.pk, detection_run_id=run.pk, expected_edit_version=1,
            operation_id=uuid4(), reason="Synthetic import before calibration")
        assessment.refresh_from_db()
        return assessment, run

    def test_detection_import_calibration_and_existing_scoring_form_one_valid_pipeline(self):
        assessment, run = self.detected_draft()
        point_values = list(assessment.shot_points.order_by("pk").values("id", "x_px", "y_px", "source", "origin_detection_id"))
        history = copy.deepcopy(assessment.historical_context)
        commands.calibrate_draft(**self.args(assessment))
        self.score(assessment)
        assessment.refresh_from_db()
        self.assertEqual(assessment.total_score, 2)
        self.assertEqual(list(assessment.shot_points.order_by("point_number").values_list("score", flat=True)), [1, 1, 0])
        self.assertEqual(list(assessment.shot_points.order_by("pk").values("id", "x_px", "y_px", "source", "origin_detection_id")), point_values)
        self.assertEqual(assessment.historical_context, history)
        self.assertEqual((assessment.status, assessment.edit_version, assessment.detection_run_id), ("draft", 4, run.pk))
        self.assertEqual(list(AuditEvent.objects.order_by("id").values_list("action", flat=True)),
            [detection_import_services.ACTION, commands.ACTION, scoring_services.ACTION])
        result = assessment.scoring_context["results"][0]
        self.assertEqual(result["x_mm"], {"numerator": "0", "denominator": "1"})
        self.assertEqual(result["y_mm"], {"numerator": "0", "denominator": "1"})

    def test_recalibration_preserves_old_calibration_points_and_configs_and_invalidates_scores(self):
        assessment, _ = self.detected_draft()
        commands.calibrate_draft(**self.args(assessment))
        self.score(assessment)
        assessment.refresh_from_db()
        previous = Calibration.objects.values().get(pk=assessment.calibration_id)
        configurations = {model: list(model.objects.values()) for model in (TargetVersion, TargetRing, ScoringRuleVersion)}
        point_values = list(assessment.shot_points.order_by("pk").values("id", "x_px", "y_px", "source", "origin_detection_id"))
        args = self.args(assessment)
        receipt = commands.calibrate_draft(**args)
        assessment.refresh_from_db()
        self.assertNotEqual(assessment.calibration_id, previous["id"])
        self.assertEqual(Calibration.objects.values().get(pk=previous["id"]), previous)
        self.assertEqual({model: list(model.objects.values()) for model in configurations}, configurations)
        self.assertEqual(list(assessment.shot_points.order_by("pk").values("id", "x_px", "y_px", "source", "origin_detection_id")), point_values)
        self.assertTrue(all(point.score is None for point in assessment.shot_points.all()))
        self.assertIsNone(assessment.total_score)
        self.assertIsNone(assessment.scoring_context)
        self.assertEqual(assessment.scoring_engine_version, "")
        self.assertEqual(assessment.scoring_input_sha256, "")
        self.assertIsNone(assessment.scored_at)
        self.assertIsNone(assessment.scored_edit_version)
        self.score(assessment)
        before = self.state()
        self.assertEqual(commands.calibrate_draft(**args), receipt)
        self.assertEqual(self.state(), before)

    def test_audit_failure_rolls_back_acceptance_binding_version_and_cached_scores(self):
        assessment, _ = self.detected_draft()
        commands.calibrate_draft(**self.args(assessment))
        self.score(assessment)
        for error in (ValidationError("Synthetic audit failure"), RuntimeError("Unexpected audit failure")):
            before = self.state()
            with self.subTest(error=error), patch.object(AuditEvent, "save", side_effect=error) as audit:
                with self.assertRaises(type(error)):
                    commands.calibrate_draft(**self.args(assessment))
                audit.assert_called_once()
            self.assertEqual(self.state(), before)

    def test_acceptance_failure_rolls_back_new_draft_calibration_without_orphans(self):
        assessment = self.draft()
        original = Calibration.save
        seen = []
        def save(instance, *args, **kwargs):
            seen.append(str(instance.status))
            if instance.status == "accepted":
                raise ValidationError("Synthetic acceptance failure")
            return original(instance, *args, **kwargs)
        before = self.state()
        with patch.object(Calibration, "save", new=save), self.assertRaises(ValidationError):
            commands.calibrate_draft(**self.args(assessment))
        self.assertEqual(seen, ["draft", "accepted"])
        self.assertEqual(self.state(), before)

    def test_uuid_insert_collision_rolls_back_calibration_and_binding(self):
        assessment = self.draft()
        args = self.args(assessment)
        commands.calibrate_draft(**args)
        before = self.state()
        with patch.object(commands, "_replay", return_value=None), self.assertRaises(services.OperationConflict):
            commands.calibrate_draft(**self.args(assessment, operation_id=args["operation_id"]))
        self.assertEqual(self.state(), before)

    def test_verified_and_locked_results_cannot_be_recalibrated(self):
        assessment = self.privileged_state(status="verified")
        for status in ("verified", "locked"):
            if status == "locked":
                self.raw(Assessment).filter(pk=assessment.pk).update(status="locked", locked_by_id=self.actor.pk, locked_at=timezone.now())
            before = self.state()
            with self.subTest(status=status), self.assertRaises(ValidationError):
                commands.calibrate_draft(**self.args(assessment))
            self.assertEqual(self.state(), before)

    def test_public_evidence_switch_guard_remains_closed_when_points_exist(self):
        assessment, _ = self.detected_draft()
        commands.calibrate_draft(**self.args(assessment))
        assessment.refresh_from_db()
        other = self.accept(self.make_calibration(photo=assessment.photo))
        assessment.calibration = other
        before = self.state()
        with self.assertRaises(ValidationError):
            assessment.save(expected_edit_version=assessment.edit_version)
        self.assertEqual(self.state(), before)

    def test_existing_lock_order_and_authenticated_recheck_are_preserved(self):
        assessment = self.draft()
        args = self.args(assessment)
        with traced_locks() as trace, patch.object(commands, "authorize_draft", wraps=commands.authorize_draft) as authorize:
            commands.calibrate_draft(**args)
        self.assertEqual([call.kwargs.get("lock", False) for call in authorize.call_args_list], [False, True])
        self.assertEqual([item[0] for item in trace][:9], ["TrainingSession", "ActivityParticipation", "Assessment",
            "ShotPoint", "TargetPhoto", "TargetVersion", "TargetRing", "User", "ActivityPermissionGrant"])
        self.assertTrue(all(not item[2] for item in trace))

    def test_audit_records_measured_reference_without_private_photo_path(self):
        assessment = self.draft()
        commands.calibrate_draft(**self.args(assessment))
        event = AuditEvent.objects.get()
        import json
        serialized = json.dumps(event.after_state)
        self.assertNotIn(assessment.photo.storage_key, serialized)
        self.assertEqual(event.after_state["calibration_result"]["coordinate_contract"], scoring.COORDINATE_CONTRACT)

    def test_changed_or_missing_original_is_rejected_without_calibration_writes(self):
        from pathlib import Path
        assessment = self.draft()
        path = Path(self.storage.path(assessment.photo.storage_key))
        original = path.read_bytes()
        # Privileged synthetic fixture bypass; normal storage forbids these writes.
        path.write_bytes(original + b"synthetic tamper")
        before = self.state()
        with self.assertRaises(ValidationError):
            commands.calibrate_draft(**self.args(assessment))
        self.assertEqual(self.state(), before)
        path.unlink()
        with self.assertRaises(ValidationError):
            commands.calibrate_draft(**self.args(assessment))
        self.assertEqual(self.state(), before)

    def test_scoring_failure_preserves_accepted_calibration_and_unscored_draft(self):
        assessment, _ = self.detected_draft()
        commands.calibrate_draft(**self.args(assessment))
        before = self.state()
        with patch.object(scoring, "score_target", side_effect=RuntimeError("Synthetic scoring failure")):
            with self.assertRaises(RuntimeError):
                self.score(assessment)
        self.assertEqual(self.state(), before)
        assessment.refresh_from_db()
        self.assertEqual(assessment.calibration.status, "accepted")
        self.assertIsNone(assessment.total_score)
