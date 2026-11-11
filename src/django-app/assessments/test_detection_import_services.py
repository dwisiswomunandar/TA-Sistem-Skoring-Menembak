"""Synthetic persisted detector outputs; no HTTP, weights, GPU or real DBMS."""

import copy
import json
from decimal import Context, Decimal, localcontext
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models
from django.utils import timezone

from activities.models import ActivityPermissionGrant, TrainingSession
from activities.test_history import traced_locks
from assessments import detection_import_services as commands, scoring, scoring_services, services
from assessments.models import Assessment, AuditEvent, DetectionRun, ScoringRuleVersion, ShotPoint, TargetPhoto
from assessments.querysets import ResultQuerySet
from assessments.test_services import ServiceTestCase

User = get_user_model()
RESULT = {"schema_version": 1, "detections": [
    {"id": "d000001", "class_id": 0, "confidence": 0.9, "bbox_xyxy": [38, 28, 42, 32]},
    {"id": "d000002", "class_id": 0, "confidence": 0.8, "bbox_xyxy": [45.25, 28, 49.25, 32]},
]}


class DetectionImportTests(ServiceTestCase):
    def ready(self, *, result=None, session=None, scoreable=False):
        photo = self.make_photo(session=session or self.session)
        run = self.make_run(photo=photo)
        run.status = "running"
        run.save()
        run.status, run.raw_result = "succeeded", copy.deepcopy(RESULT if result is None else result)
        run.save()
        values = dict(session=session or self.session, photo=photo, detection_run=run)
        if scoreable:
            rule = ScoringRuleVersion.objects.create(code=uuid4().hex, version=1, created_by=self.actor,
                algorithm_version=scoring.ENGINE_VERSION, boundary_policy=scoring.BOUNDARY_POLICY,
                numeric_precision=scoring.NUMERIC_PRECISION).publish()
            values.update(calibration=self.accept(self.make_calibration(photo=photo)), scoring_rule_version=rule)
        return self.make_assessment(**values), run

    def args(self, assessment, run, **changes):
        assessment.refresh_from_db()
        return {**dict(actor=self.actor, session_id=assessment.session_id, assessment_id=assessment.pk,
            detection_run_id=run.pk, expected_edit_version=assessment.edit_version,
            operation_id=uuid4(), reason="Synthetic detection import"), **changes}

    def state(self):
        return {model: list(model.objects.order_by("pk").values()) for model in
                (Assessment, ShotPoint, AuditEvent, DetectionRun, TargetPhoto, TrainingSession)}

    def calculate(self, assessment):
        assessment.refresh_from_db()
        calibration = assessment.calibration
        return scoring_services.score_draft(actor=self.actor, session_id=assessment.session_id,
            assessment_id=assessment.pk, expected_edit_version=assessment.edit_version,
            expected_evidence_versions={key: {"id": value.pk, "edit_version": value.edit_version}
                for key, value in (("calibration", calibration), ("target_version", calibration.target_version),
                                   ("scoring_rule_version", assessment.scoring_rule_version))},
            operation_id=uuid4(), reason="Synthetic post-import scoring")

    def test_imports_centers_and_provenance_with_one_version_and_one_audit(self):
        assessment, run = self.ready()
        original_run = DetectionRun.objects.values().get(pk=run.pk)
        receipt = commands.import_detection_points(**self.args(assessment, run))
        assessment.refresh_from_db()
        points = list(assessment.shot_points.order_by("point_number"))
        self.assertEqual([(p.point_number, p.x_px, p.y_px) for p in points],
            [(1, Decimal("40"), Decimal("30")), (2, Decimal("47.25"), Decimal("30"))])
        self.assertEqual([(p.source, p.origin_detection_id, p.score, p.is_included) for p in points],
            [("ai", "d000001", None, True), ("ai", "d000002", None, True)])
        self.assertEqual((assessment.status, assessment.edit_version, assessment.total_score), ("draft", 2, None))
        event = self.assert_audited(receipt, action=commands.ACTION, previous_version=1)
        self.assertIsNone(receipt.point_id)
        self.assertEqual(AuditEvent.objects.count(), 1)
        source = event.after_state["detection_import"]
        self.assertEqual(source["point_ids"], [p.pk for p in points])
        self.assertEqual((source["run_id"], source["imported_count"]), (run.pk, 2))
        self.assertEqual(source["weights_sha256"], run.weights_sha256)
        self.assertEqual(source["raw_result_sha256"], commands._digest(run.raw_result))
        self.assertEqual(DetectionRun.objects.values().get(pk=run.pk), original_run)

    def test_empty_success_creates_no_fictitious_shots_or_scores_and_is_audited(self):
        assessment, run = self.ready(result={"schema_version": 1, "detections": []})
        args = self.args(assessment, run)
        first = commands.import_detection_points(**args)
        assessment.refresh_from_db()
        self.assertFalse(assessment.shot_points.exists())
        self.assertIsNone(assessment.total_score)
        self.assertEqual((run.status, assessment.edit_version), ("succeeded", 2))
        self.assertEqual(AuditEvent.objects.get().after_state["detection_import"]["imported_count"], 0)
        before = self.state()
        self.assertEqual(commands.import_detection_points(**args), first)
        self.assertEqual(self.state(), before)
        with self.assertRaises(ValidationError):
            commands.import_detection_points(**self.args(assessment, run))
        self.assertEqual(self.state(), before)

    def test_appends_after_existing_number_without_replacing_manual_points(self):
        assessment, run = self.ready()
        manual = self.make_point(assessment, point_number=8)
        original = ShotPoint.objects.values().get(pk=manual.pk)
        commands.import_detection_points(**self.args(assessment, run))
        self.assertEqual(ShotPoint.objects.values().get(pk=manual.pk), original)
        self.assertEqual(list(assessment.shot_points.order_by("point_number").values_list("point_number", flat=True)), [8, 9, 10])
        assessment.refresh_from_db()
        self.assertEqual(assessment.edit_version, 3)

    def test_rounding_is_explicit_half_even_and_independent_of_decimal_context(self):
        result = copy.deepcopy(RESULT)
        result["detections"][0]["bbox_xyxy"] = [1.0000002, 2.0000002, 1.0000008, 2.0000028]
        assessment, run = self.ready(result=result)
        with localcontext(Context(prec=2)):
            commands.import_detection_points(**self.args(assessment, run))
        point = assessment.shot_points.get(origin_detection_id="d000001")
        self.assertEqual((point.x_px, point.y_px), (Decimal("1.000000"), Decimal("2.000002")))
        self.assertEqual(AuditEvent.objects.get().after_state["detection_import"]["conversion_version"], commands.CONVERSION_VERSION)

    def test_rounding_outside_raster_is_rejected_without_clipping_or_partial_writes(self):
        result = copy.deepcopy(RESULT)
        result["detections"][1]["bbox_xyxy"] = [79.9999999, 1, 80, 2]
        assessment, run = self.ready(result=result)
        before = self.state()
        with self.assertRaises(ValidationError):
            commands.import_detection_points(**self.args(assessment, run))
        self.assertEqual(self.state(), before)

    def test_client_cannot_supply_coordinates_sources_scores_or_raw_results(self):
        assessment, run = self.ready()
        before = self.state()
        for field in ("points", "x_px", "source", "score", "raw_result", "weights_sha256", "actor_id"):
            with self.subTest(field=field), self.assertRaises(TypeError):
                commands.import_detection_points(**self.args(assessment, run), **{field: 1})
        self.assertEqual(self.state(), before)

    def test_invalid_identifiers_versions_operations_and_reasons_are_rejected(self):
        assessment, run = self.ready()
        before = self.state()
        for changes in ({"assessment_id": True}, {"detection_run_id": "1"}, {"detection_run_id": 0},
                        {"expected_edit_version": True}, {"expected_edit_version": 0},
                        {"operation_id": "invalid"}, {"reason": " "}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                commands.import_detection_points(**self.args(assessment, run, **changes))
        self.assertEqual(self.state(), before)

    def test_pending_running_and_failed_runs_are_not_imported(self):
        assessment, run = self.ready()
        for status in ("pending", "running", "failed"):
            other = self.make_run(photo=run.photo)
            if status == "running":
                other.status = status
                other.save()
            elif status == "failed":
                other.status, other.failure_code = status, "synthetic-failure"
                other.save()
            self.raw(Assessment).filter(pk=assessment.pk).update(detection_run_id=other.pk)
            before = self.state()
            with self.subTest(status=status), self.assertRaises(ValidationError):
                commands.import_detection_points(**self.args(assessment, other))
            self.assertEqual(self.state(), before)

    def test_run_must_already_be_selected_and_match_photo_and_session(self):
        assessment, run = self.ready()
        other = self.finish_run(result=RESULT)
        before = self.state()
        with self.assertRaises(ValidationError):
            commands.import_detection_points(**self.args(assessment, other))
        self.assertEqual(self.state(), before)
        self.raw(Assessment).filter(pk=assessment.pk).update(detection_run_id=None)
        before = self.state()
        with self.assertRaises(ValidationError):
            commands.import_detection_points(**self.args(assessment, run))
        self.assertEqual(self.state(), before)

    def test_foreign_assessment_and_photo_context_are_rejected(self):
        assessment, run = self.ready()
        other_session = TrainingSession.objects.create(participation=self.session.participation,
            session_number=2, assigned_assessor=self.actor, created_by=self.actor,
            weapon_type="pistol", distance_m=20)
        other, foreign = self.ready(session=other_session)
        before = self.state()
        with self.assertRaises(ValidationError):
            commands.import_detection_points(**self.args(other, foreign, session_id=self.session.pk))
        self.assertEqual(self.state(), before)
        self.raw(Assessment).filter(pk=assessment.pk).update(photo_id=foreign.photo_id, detection_run_id=foreign.pk)
        before = self.state()
        with self.assertRaises(ValidationError):
            commands.import_detection_points(**self.args(assessment, foreign))
        self.assertEqual(self.state(), before)

    def test_invalid_persisted_contract_and_duplicate_detection_ids_fail_without_writes(self):
        assessment, run = self.ready()
        mutations = [dict(schema_version=True), dict(schema_version=2), dict(detections={}),
            dict(detections=[RESULT["detections"][0], RESULT["detections"][0]])]
        for field, value in (("confidence", True), ("class_id", -1), ("id", " "),
                             ("bbox_xyxy", [0, 0, 81, 2]), ("bbox_xyxy", [2, 0, 1, 2]),
                             ("bbox_xyxy", ["NaN", 0, 1, 2]), ("bbox_xyxy", [True, 0, 1, 2])):
            row = copy.deepcopy(RESULT["detections"][0])
            row[field] = value
            mutations.append(dict(detections=[row]))
        for changes in mutations:
            self.raw(DetectionRun).filter(pk=run.pk).update(raw_result={**copy.deepcopy(RESULT), **changes})
            before = self.state()
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                commands.import_detection_points(**self.args(assessment, run))
            self.assertEqual(self.state(), before)

    def test_duplicate_existing_origin_rejects_entire_batch(self):
        assessment, run = self.ready()
        self.make_point(assessment, source="ai", origin_detection_id="d000002")
        before = self.state()
        with self.assertRaises(ValidationError):
            commands.import_detection_points(**self.args(assessment, run))
        self.assertEqual(self.state(), before)

    def test_new_uuid_cannot_reimport_even_after_all_imported_points_are_deleted(self):
        assessment, run = self.ready()
        commands.import_detection_points(**self.args(assessment, run))
        for point in list(assessment.shot_points.all()):
            self.command(services.delete_shot_point, assessment=assessment, point=point)
        before = self.state()
        with self.assertRaises(ValidationError):
            commands.import_detection_points(**self.args(assessment, run))
        self.assertEqual(self.state(), before)

    def test_stale_version_fails_and_identical_replay_keeps_human_corrections(self):
        assessment, run = self.ready()
        args = self.args(assessment, run)
        receipt = commands.import_detection_points(**args)
        point = assessment.shot_points.get(origin_detection_id="d000001")
        self.command(services.update_shot_point, assessment=assessment, point=point, data={"x_px": 41})
        before = self.state()
        with self.assertRaises(ValidationError):
            commands.import_detection_points(**{**args, "operation_id": uuid4()})
        self.assertEqual(self.state(), before)
        self.assertEqual(commands.import_detection_points(**args), receipt)
        self.assertEqual(self.state(), before)
        point.refresh_from_db()
        self.assertEqual((point.x_px, point.source, point.origin_detection_id), (Decimal("41"), "ai", "d000001"))

    def test_uuid_conflicts_with_changed_payload_or_other_command(self):
        assessment, run = self.ready()
        args = self.args(assessment, run)
        commands.import_detection_points(**args)
        before = self.state()
        for changes in ({"reason": "Different"}, {"expected_edit_version": 2}, {"detection_run_id": run.pk + 100}):
            with self.subTest(changes=changes), self.assertRaises(services.OperationConflict):
                commands.import_detection_points(**{**args, **changes})
        with self.assertRaises(services.OperationConflict):
            self.command(services.update_draft, assessment=assessment, operation_id=args["operation_id"], data={"notes": "Different command"})
        self.assertEqual(self.state(), before)

    def test_missing_global_permission_scope_assignment_and_invalid_actor_are_denied(self):
        assessment, run = self.ready()
        for actor in (AnonymousUser(), self.actor.pk, User(email="unsaved@example.com")):
            with self.subTest(actor=actor), self.assertRaises(PermissionDenied):
                commands.import_detection_points(**self.args(assessment, run, actor=actor))
        other = User.objects.create_user("other-importer@example.com")
        self.grant(other)
        with self.assertRaises(PermissionDenied):
            commands.import_detection_points(**self.args(assessment, run, actor=other))
        for model, field, value in ((ActivityPermissionGrant, "is_active", False), (User, "is_active", False)):
            query = self.raw(model).filter(user=self.actor) if model is ActivityPermissionGrant else model.objects.filter(pk=self.actor.pk)
            query.update(**{field: value})
            before = self.state()
            with self.subTest(model=model), self.assertRaises(PermissionDenied):
                commands.import_detection_points(**self.args(assessment, run))
            self.assertEqual(self.state(), before)
            query.update(**{field: True})
        permission = self.actor.user_permissions.get(codename="add_shotpoint")
        self.actor.user_permissions.remove(permission)
        before = self.state()
        with self.assertRaises(PermissionDenied):
            commands.import_detection_points(**self.args(assessment, run))
        self.assertEqual(self.state(), before)

    def test_revocation_blocks_replay_and_authorization_is_rechecked_under_locks(self):
        assessment, run = self.ready()
        args = self.args(assessment, run)
        with patch.object(commands, "authorize_draft", wraps=commands.authorize_draft) as authorize:
            commands.import_detection_points(**args)
        self.assertEqual([call.kwargs.get("lock", False) for call in authorize.call_args_list], [False, True])
        self.raw(ActivityPermissionGrant).filter(user=self.actor, permission__codename="add_shotpoint").update(is_active=False)
        before = self.state()
        with self.assertRaises(PermissionDenied):
            commands.import_detection_points(**args)
        self.assertEqual(self.state(), before)

    def test_latest_revision_and_session_snapshot_are_rechecked(self):
        assessment, run = self.ready()
        before = self.state()
        with patch.object(ResultQuerySet, "latest_working", return_value=SimpleNamespace(pk=assessment.pk + 1)):
            with self.assertRaises(ValidationError):
                commands.import_detection_points(**self.args(assessment, run))
        self.assertEqual(self.state(), before)
        self.raw(TrainingSession).filter(pk=self.session.pk).update(distance_m=Decimal("20"))
        before = self.state()
        with self.assertRaises(ValidationError):
            commands.import_detection_points(**self.args(assessment, run))
        self.assertEqual(self.state(), before)

    def test_verified_and_locked_assessments_remain_immutable(self):
        assessment, run = self.ready(scoreable=True)
        self.calculate(assessment)
        now = timezone.now()
        for status in ("verified", "locked"):
            values = dict(status=status, verified_by_id=self.actor.pk, verified_at=now)
            if status == "locked":
                values.update(locked_by_id=self.actor.pk, locked_at=now)
            self.raw(Assessment).filter(pk=assessment.pk).update(**values)  # Synthetic official-state fixture only.
            before = self.state()
            with self.subTest(status=status), self.assertRaises(ValidationError):
                commands.import_detection_points(**self.args(assessment, run))
            self.assertEqual(self.state(), before)

    def test_existing_score_cache_is_invalidated_once_and_server_can_score_imported_points(self):
        assessment, run = self.ready(scoreable=True)
        manual = self.make_point(assessment, x_px=Decimal("40"), y_px=Decimal("30"))
        self.calculate(assessment)
        assessment.refresh_from_db()
        self.assertEqual(assessment.total_score, 1)
        prior = assessment.edit_version
        historical = copy.deepcopy(assessment.historical_context)
        import_args = self.args(assessment, run)
        receipt = commands.import_detection_points(**import_args)
        assessment.refresh_from_db()
        self.assertEqual(assessment.edit_version, prior + 1)
        self.assertIsNone(assessment.total_score)
        self.assertIsNone(assessment.scoring_context)
        self.assertEqual(assessment.scoring_input_sha256, "")
        self.assertIsNone(assessment.scored_at)
        self.assertIsNone(assessment.scored_edit_version)
        self.assertTrue(all(p.score is None for p in assessment.shot_points.all()))
        self.assertEqual(assessment.historical_context, historical)
        self.calculate(assessment)
        assessment.refresh_from_db()
        self.assertEqual(assessment.total_score, 3)
        manual.refresh_from_db()
        self.assertEqual((manual.source, manual.score), ("manual", 1))

        before_replay = self.state()
        self.assertEqual(commands.import_detection_points(**import_args), receipt)
        self.assertEqual(self.state(), before_replay)

    def test_audit_failure_rolls_back_batch_version_and_preexisting_score_cache(self):
        assessment, run = self.ready(scoreable=True)
        self.make_point(assessment, x_px=Decimal("40"), y_px=Decimal("30"))
        self.calculate(assessment)
        for error in (ValidationError("Synthetic audit failure"), RuntimeError("Unexpected audit failure")):
            before = self.state()
            with self.subTest(error=error), patch.object(AuditEvent, "save", side_effect=error) as audit:
                with self.assertRaises(type(error)):
                    commands.import_detection_points(**self.args(assessment, run))
                audit.assert_called_once()
            self.assertEqual(self.state(), before)

    def test_second_point_insert_failure_rolls_back_first_point_and_cache(self):
        assessment, run = self.ready(scoreable=True)
        self.calculate(assessment)
        before = self.state()
        original = models.Model.save
        inserted = []
        def save(instance, *args, **kwargs):
            if isinstance(instance, ShotPoint):
                inserted.append(instance.origin_detection_id)
                if len(inserted) == 2:
                    raise RuntimeError("Synthetic second insert failure")
            return original(instance, *args, **kwargs)
        with patch.object(models.Model, "save", new=save), self.assertRaises(RuntimeError):
            commands.import_detection_points(**self.args(assessment, run))
        self.assertEqual(inserted, ["d000001", "d000002"])
        self.assertEqual(self.state(), before)

    def test_uuid_insert_collision_rolls_back_other_session_import(self):
        assessment, run = self.ready()
        args = self.args(assessment, run)
        commands.import_detection_points(**args)
        session = TrainingSession.objects.create(participation=self.session.participation, session_number=2,
            assigned_assessor=self.actor, created_by=self.actor, weapon_type="pistol", distance_m=25)
        other, other_run = self.ready(session=session)
        before = self.state()
        # Simulates UUID visibility only at INSERT, not a real SQLite race.
        with patch.object(commands, "_replay", return_value=None):
            with self.assertRaises(services.OperationConflict):
                commands.import_detection_points(**self.args(other, other_run, operation_id=args["operation_id"]))
        self.assertEqual(self.state(), before)

    def test_audit_excludes_private_paths_raw_results_and_inference_secrets(self):
        assessment, run = self.ready()
        self.raw(DetectionRun).filter(pk=run.pk).update(parameters={"token": "synthetic-sensitive-value"},
            raw_result={**run.raw_result, "private_extra": {"password": "synthetic-sensitive-value"}})
        commands.import_detection_points(**self.args(assessment, run))
        event = AuditEvent.objects.get(action=commands.ACTION)
        serialized = json.dumps(event.after_state)
        for value in ("synthetic-sensitive-value", run.photo.storage_key, "private_extra", '"parameters":', '"raw_result":'):
            self.assertNotIn(value, serialized)

    def test_lock_order_is_domain_then_points_photo_run_then_actor_and_grants(self):
        assessment, run = self.ready()
        self.make_point(assessment)
        with traced_locks() as trace:
            commands.import_detection_points(**self.args(assessment, run))
        names = [item[0] for item in trace]
        self.assertEqual(names[:8], ["TrainingSession", "ActivityParticipation", "Assessment", "ShotPoint",
                                     "TargetPhoto", "DetectionRun", "User", "ActivityPermissionGrant"])
        self.assertTrue(all(not item[2] for item in trace))

    def test_import_does_not_dispatch_inference_or_compute_scores(self):
        from assessments import detection_client
        assessment, run = self.ready()
        with patch.object(detection_client, "infer", side_effect=AssertionError("No HTTP")) as infer:
            with patch.object(scoring, "score_target", side_effect=AssertionError("No scoring")) as score:
                commands.import_detection_points(**self.args(assessment, run))
        infer.assert_not_called()
        score.assert_not_called()

    def test_matching_full_response_envelope_uses_photo_analysis_coordinates(self):
        assessment, run = self.ready()
        envelope = {**run.raw_result, "status": "succeeded", "analysis_width_px": 80,
            "analysis_height_px": 60, "preprocessing_version": run.photo.preprocessing_version,
            "input_sha256": run.photo.sha256, "model_name": run.model_name,
            "weights_sha256": run.weights_sha256, "runtime_version": run.runtime_version,
            "adapter_version": run.adapter_version, "parameters": run.parameters}
        self.raw(DetectionRun).filter(pk=run.pk).update(raw_result=envelope)
        commands.import_detection_points(**self.args(assessment, run))
        self.assertEqual(assessment.shot_points.get(origin_detection_id="d000001").x_px, Decimal("40"))

    def test_conflicting_response_metadata_is_rejected_before_any_write(self):
        assessment, run = self.ready()
        for field, value in (("status", "failed"), ("analysis_width_px", 81), ("analysis_height_px", True),
                             ("preprocessing_version", "different-space"), ("input_sha256", "b" * 64),
                             ("weights_sha256", "b" * 64), ("model_name", "other-model"),
                             ("runtime_version", "other-runtime"), ("adapter_version", "other-adapter"),
                             ("parameters", {"different": True})):
            self.raw(DetectionRun).filter(pk=run.pk).update(raw_result={**run.raw_result, field: value})
            before = self.state()
            with self.subTest(field=field), self.assertRaises(ValidationError):
                commands.import_detection_points(**self.args(assessment, run))
            self.assertEqual(self.state(), before)
