"""Synthetic finalization approvals and evidence; not organizational/field policy."""

import copy
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import override_settings
from django.utils import timezone

from activities.models import ActivityPermissionGrant, TrainingSession
from activities.test_history import traced_locks
from assessments import detection_import_services, finalization_services as commands
from assessments import scoring, scoring_services as calculator, services
from assessments.models import Assessment, AuditEvent, Calibration, DetectionRun, ScoringRuleVersion, ShotPoint, TargetPhoto, TargetRing, TargetVersion
from assessments.querysets import ResultQuerySet
from assessments.test_services import ServiceTestCase

User = get_user_model()


class FinalizationTests(ServiceTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.rule = ScoringRuleVersion.objects.create(code="synthetic-finalization", version=1, created_by=cls.actor,
            algorithm_version=scoring.ENGINE_VERSION, boundary_policy=scoring.BOUNDARY_POLICY,
            numeric_precision=scoring.NUMERIC_PRECISION).publish()

    def setUp(self):
        super().setUp()
        self.policy = {"activity_id": self.session.participation.activity_id,
            "verify_user_ids": [self.actor.pk], "lock_user_ids": [self.actor.pk],
            "allow_self_verification": True, "allow_verifier_to_lock": True,
            "allow_empty_results": True, "allow_manual_only_results": True,
            "approval_reference": "Synthetic test approval; not an organizational decision"}
        self.enterContext(override_settings(ASSESSMENT_FINALIZATION_POLICIES=(self.policy,)))

    def ready(self, *, ai=False, empty=False, calculate=True, excluded=False):
        run = None
        if ai:
            run = self.finish_run(result={"schema_version": 1, "detections": [
                {"id": "origin-1", "class_id": 0, "confidence": 0.9, "bbox_xyxy": [39, 29, 41, 31]}]})
            photo = run.photo
        else:
            photo = self.make_photo()
        assessment = self.make_assessment(photo=photo, detection_run=run,
            calibration=self.accept(self.make_calibration(photo=photo)), scoring_rule_version=self.rule)
        if ai:
            detection_import_services.import_detection_points(actor=self.actor, session_id=self.session.pk,
                assessment_id=assessment.pk, detection_run_id=run.pk, expected_edit_version=1,
                operation_id=uuid4(), reason="Synthetic AI import")
        elif not empty:
            self.command(services.add_shot_point, assessment=assessment, data={"x_px": 40, "y_px": 30})
        if excluded:
            self.command(services.add_shot_point, assessment=assessment,
                data={"x_px": 5, "y_px": 5, "is_included": False, "exclusion_reason": "Synthetic exclusion"})
        if calculate:
            self.calculate(assessment)
        assessment.refresh_from_db()
        return assessment

    def args(self, assessment, **changes):
        assessment.refresh_from_db()
        calibration, rule = assessment.calibration, assessment.scoring_rule_version
        target = calibration.target_version if calibration else None
        expected = {name: {"id": row.pk, "edit_version": row.edit_version}
                    for name, row in (("calibration", calibration), ("target_version", target), ("scoring_rule_version", rule)) if row is not None}
        return {**dict(actor=self.actor, session_id=assessment.session_id, assessment_id=assessment.pk,
            expected_edit_version=assessment.edit_version, expected_evidence_versions=expected,
            operation_id=uuid4(), reason="Synthetic finalization"), **changes}

    def calculate(self, assessment):
        return calculator.score_draft(**self.args(assessment))

    def state(self):
        return {model: list(model.objects.order_by("pk").values()) for model in
            (TrainingSession, Assessment, ShotPoint, TargetPhoto, DetectionRun, Calibration,
             TargetVersion, TargetRing, ScoringRuleVersion, AuditEvent)}

    def verify(self, assessment):
        return commands.verify_assessment(**self.args(assessment))

    def lock(self, assessment):
        return commands.lock_assessment(**self.args(assessment))

    def test_draft_verified_locked_preserves_scores_input_and_history_with_audited_lineage(self):
        assessment = self.ready(excluded=True)
        original = Assessment.objects.values().get(pk=assessment.pk)
        points = list(assessment.shot_points.order_by("pk").values())
        score_event = AuditEvent.objects.get(action=calculator.ACTION)
        verified = self.verify(assessment)
        assessment.refresh_from_db()
        self.assertEqual((assessment.status, assessment.verified_by_id, assessment.locked_by_id), ("verified", self.actor.pk, None))
        self.assertEqual((assessment.edit_version, assessment.scored_edit_version), (original["edit_version"] + 1,) * 2)
        verify_event = AuditEvent.objects.get(pk=verified.audit_event_id)
        self.assertEqual(verify_event.caused_by_event_id, score_event.pk)
        verified_at = assessment.verified_at
        locked = self.lock(assessment)
        assessment.refresh_from_db()
        self.assertEqual((assessment.status, assessment.locked_by_id), ("locked", self.actor.pk))
        self.assertGreaterEqual(assessment.locked_at, verified_at)
        self.assertEqual(assessment.verified_at, verified_at)
        self.assertEqual((assessment.edit_version, assessment.scored_edit_version), (original["edit_version"] + 2,) * 2)
        for name in ("total_score", "scoring_context", "scoring_input_sha256", "scored_at", "scoring_engine_version", "historical_context"):
            self.assertEqual(getattr(assessment, name), original[name])
        self.assertEqual(list(assessment.shot_points.order_by("pk").values()), points)
        self.assertEqual(AuditEvent.objects.get(pk=locked.audit_event_id).caused_by_event_id, verify_event.pk)
        self.assertEqual(Assessment.objects.latest_official(session_id=self.session.pk).pk, assessment.pk)
        self.assertEqual((verify_event.version_before, verify_event.version_after), (original["edit_version"], original["edit_version"] + 1))
        self.assertEqual(verify_event.after_state["transition"]["source_scored_version"], score_event.version_after)

    def test_empty_missing_or_malformed_policy_is_default_deny(self):
        assessment = self.ready()
        args = self.args(assessment)
        policies = [(), ({},), ({**self.policy, "allow_self_verification": 1},),
            ({**self.policy, "approval_reference": " "},), (self.policy, self.policy),
            ({**self.policy, "verify_user_ids": [True]},)]
        before = self.state()
        for policy in policies:
            with self.subTest(policy=policy), override_settings(ASSESSMENT_FINALIZATION_POLICIES=policy):
                with self.assertRaises(PermissionDenied):
                    commands.verify_assessment(**args)
            self.assertEqual(self.state(), before)

    def test_action_allowlists_and_explicit_self_verification_approval_are_required(self):
        assessment = self.ready()
        before = self.state()
        for changes in ({"verify_user_ids": []}, {"allow_self_verification": False}):
            with override_settings(ASSESSMENT_FINALIZATION_POLICIES=({**self.policy, **changes},)):
                with self.assertRaises(PermissionDenied):
                    self.verify(assessment)
            self.assertEqual(self.state(), before)
        self.verify(assessment)
        before = self.state()
        for changes in ({"lock_user_ids": []}, {"allow_verifier_to_lock": False}):
            with override_settings(ASSESSMENT_FINALIZATION_POLICIES=({**self.policy, **changes},)):
                with self.assertRaises(PermissionDenied):
                    self.lock(assessment)
            self.assertEqual(self.state(), before)

    def test_global_permission_activity_grant_assignment_and_active_user_are_all_required(self):
        assessment = self.ready()
        before = self.state()
        other = User.objects.create_user("other-finalizer@example.com")
        self.grant(other)
        with override_settings(ASSESSMENT_FINALIZATION_POLICIES=({**self.policy, "verify_user_ids": [other.pk]},)):
            with self.assertRaises(PermissionDenied):
                commands.verify_assessment(**self.args(assessment, actor=other))
        for actor in (AnonymousUser(), self.actor.pk, User(email="unsaved-finalizer@example.com")):
            with self.subTest(actor=actor), self.assertRaises(PermissionDenied):
                commands.verify_assessment(**self.args(assessment, actor=actor))
        grants = self.raw(ActivityPermissionGrant).filter(user=self.actor, permission__codename="change_assessment")
        grants.update(is_active=False)
        with self.assertRaises(PermissionDenied):
            self.verify(assessment)
        grants.update(is_active=True)
        User.objects.filter(pk=self.actor.pk).update(is_active=False)
        with self.assertRaises(PermissionDenied):
            self.verify(assessment)
        User.objects.filter(pk=self.actor.pk).update(is_active=True)
        permission = self.actor.user_permissions.get(codename="change_assessment")
        self.actor.user_permissions.remove(permission)
        with self.assertRaises(PermissionDenied):
            self.verify(assessment)
        self.assertEqual(self.state(), before)

    def test_staff_and_superuser_do_not_bypass_scope_or_explicit_policy(self):
        assessment = self.ready()
        User.objects.filter(pk=self.actor.pk).update(is_staff=True, is_superuser=True)
        self.raw(ActivityPermissionGrant).filter(user=self.actor).update(is_active=False)
        with self.assertRaises(PermissionDenied):
            self.verify(assessment)
        self.raw(ActivityPermissionGrant).filter(user=self.actor).update(is_active=True)
        with override_settings(ASSESSMENT_FINALIZATION_POLICIES=()), self.assertRaises(PermissionDenied):
            self.verify(assessment)

    def test_manual_only_and_empty_results_need_separate_explicit_approval(self):
        assessment = self.ready()
        before = self.state()
        with override_settings(ASSESSMENT_FINALIZATION_POLICIES=({**self.policy, "allow_manual_only_results": False},)):
            with self.assertRaises(PermissionDenied):
                self.verify(assessment)
        self.assertEqual(self.state(), before)
        # A different session permits an independent empty-result fixture.
        session = TrainingSession.objects.create(participation=self.session.participation, session_number=2,
            assigned_assessor=self.actor, created_by=self.actor, weapon_type="pistol", distance_m=20)
        photo = self.make_photo(session=session)
        empty = self.make_assessment(session=session, photo=photo,
            calibration=self.accept(self.make_calibration(photo=photo)), scoring_rule_version=self.rule)
        self.calculate(empty)
        before = self.state()
        with override_settings(ASSESSMENT_FINALIZATION_POLICIES=({**self.policy, "allow_empty_results": False},)):
            with self.assertRaises(PermissionDenied):
                self.verify(empty)
        self.assertEqual(self.state(), before)
        self.verify(empty)
        self.lock(empty)
        empty.refresh_from_db()
        self.assertEqual((empty.status, empty.total_score, empty.shot_points.count()), ("locked", 0, 0))

    def test_ai_pipeline_preserves_audited_import_provenance(self):
        assessment = self.ready(ai=True)
        with override_settings(ASSESSMENT_FINALIZATION_POLICIES=({**self.policy, "allow_manual_only_results": False},)):
            self.verify(assessment)
            self.lock(assessment)
        point = assessment.shot_points.get()
        self.assertEqual((point.source, point.origin_detection_id, point.score), ("ai", "origin-1", 1))
        self.assertEqual(assessment.detection_run.status, "succeeded")

    def test_unscored_incomplete_and_invalidated_draft_cannot_be_verified(self):
        assessment = self.ready(calculate=False)
        before = self.state()
        with self.assertRaises(ValidationError):
            self.verify(assessment)
        self.assertEqual(self.state(), before)
        self.calculate(assessment)
        self.command(services.update_draft, assessment=assessment, data={"notes": "Invalidates cache"})
        before = self.state()
        with self.assertRaises(ValidationError):
            self.verify(assessment)
        self.assertEqual(self.state(), before)

    def test_missing_photo_calibration_or_rule_cannot_be_finalized(self):
        assessment = self.ready()
        original = {field: getattr(assessment, field) for field in
                    ("photo_id", "calibration_id", "detection_run_id", "scoring_rule_version_id")}
        for field in ("photo_id", "calibration_id", "scoring_rule_version_id"):
            # Deliberate incomplete SQL fixture that still respects DB constraints.
            changes = {field: None}
            if field == "photo_id":
                changes.update(calibration_id=None, detection_run_id=None)
            self.raw(Assessment).filter(pk=assessment.pk).update(**changes)
            before = self.state()
            with self.subTest(field=field), self.assertRaises(ValidationError):
                commands.verify_assessment(**self.args(assessment, expected_evidence_versions={
                    "calibration": {"id": 1, "edit_version": 1},
                    "target_version": {"id": 1, "edit_version": 1},
                    "scoring_rule_version": {"id": 1, "edit_version": 1}}))
            self.assertEqual(self.state(), before)
            self.raw(Assessment).filter(pk=assessment.pk).update(**original)
            assessment.refresh_from_db()
        assessment.refresh_from_db()

    def test_stale_request_evidence_and_invalid_command_inputs_are_rejected(self):
        assessment = self.ready()
        args = self.args(assessment)
        before = self.state()
        cases = [{"expected_edit_version": 1}, {"expected_edit_version": True}, {"assessment_id": True},
            {"reason": " "}, {"operation_id": "bad"}, {"expected_evidence_versions": {}},
            {"expected_evidence_versions": {**args["expected_evidence_versions"], "calibration": {"id": 999, "edit_version": 1}}}]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                commands.verify_assessment(**{**args, **changes})
        for field in ("status", "total_score", "verified_at", "verified_by", "locked_at", "trusted"):
            with self.subTest(field=field), self.assertRaises(TypeError):
                commands.verify_assessment(**args, **{field: 1})
        self.assertEqual(self.state(), before)

    def test_draft_cannot_lock_and_repeated_new_operations_cannot_skip_transitions(self):
        assessment = self.ready()
        before = self.state()
        with self.assertRaises(ValidationError):
            self.lock(assessment)
        self.assertEqual(self.state(), before)
        args = self.args(assessment)
        self.verify(assessment)
        before = self.state()
        with self.assertRaises(ValidationError):
            commands.verify_assessment(**{**args, "operation_id": uuid4()})
        with self.assertRaises(ValidationError):
            self.verify(assessment)
        self.assertEqual(self.state(), before)
        self.lock(assessment)
        before = self.state()
        with self.assertRaises(ValidationError):
            self.lock(assessment)
        with self.assertRaises(ValidationError):
            self.verify(assessment)
        self.assertEqual(self.state(), before)

    def test_replay_returns_historical_receipts_after_lock_without_mutation(self):
        assessment = self.ready()
        verify_args = self.args(assessment)
        verified = commands.verify_assessment(**verify_args)
        lock_args = self.args(assessment)
        locked = commands.lock_assessment(**lock_args)
        before = self.state()
        self.assertEqual(commands.verify_assessment(**verify_args), verified)
        self.assertEqual(commands.lock_assessment(**lock_args), locked)
        self.assertEqual(self.state(), before)
        self.assertEqual(AuditEvent.objects.filter(action=commands.VERIFY).count(), 1)
        self.assertEqual(AuditEvent.objects.filter(action=commands.LOCK).count(), 1)

    def test_current_policy_and_scope_revocation_deny_receipt_replay(self):
        assessment = self.ready()
        args = self.args(assessment)
        commands.verify_assessment(**args)
        before = self.state()
        for policies in ((), ({**self.policy, "verify_user_ids": []},), ({**self.policy, "allow_self_verification": False},)):
            with override_settings(ASSESSMENT_FINALIZATION_POLICIES=policies), self.assertRaises(PermissionDenied):
                commands.verify_assessment(**args)
            self.assertEqual(self.state(), before)
        self.raw(ActivityPermissionGrant).filter(user=self.actor, permission__codename="change_assessment").update(is_active=False)
        with self.assertRaises(PermissionDenied):
            commands.verify_assessment(**args)
        self.assertEqual(self.state(), before)

    def test_uuid_reuse_with_different_payload_or_action_conflicts(self):
        assessment = self.ready()
        args = self.args(assessment)
        commands.verify_assessment(**args)
        before = self.state()
        for changes in ({"reason": "Different"}, {"expected_edit_version": 999}):
            with self.subTest(changes=changes), self.assertRaises(services.OperationConflict):
                commands.verify_assessment(**{**args, **changes})
        with self.assertRaises(services.OperationConflict):
            commands.lock_assessment(**self.args(assessment, operation_id=args["operation_id"]))
        self.assertEqual(self.state(), before)

    def test_session_snapshot_and_latest_revision_are_checked(self):
        assessment = self.ready()
        before = self.state()
        with patch.object(ResultQuerySet, "latest_working", return_value=SimpleNamespace(pk=assessment.pk + 1)):
            with self.assertRaises(ValidationError):
                self.verify(assessment)
        self.assertEqual(self.state(), before)
        self.raw(TrainingSession).filter(pk=self.session.pk).update(distance_m=20)
        before = self.state()
        with self.assertRaises(ValidationError):
            self.verify(assessment)
        self.assertEqual(self.state(), before)

    def test_point_drift_without_version_bump_invalidates_fingerprint(self):
        assessment = self.ready()
        self.raw(ShotPoint).filter(assessment=assessment).update(x_px=41)
        before = self.state()
        with self.assertRaises(ValidationError):
            self.verify(assessment)
        self.assertEqual(self.state(), before)

    def test_score_and_total_tampering_cannot_become_verified(self):
        assessment = self.ready()
        self.raw(ShotPoint).filter(assessment=assessment).update(score=0)
        self.raw(Assessment).filter(pk=assessment.pk).update(total_score=0)
        before = self.state()
        with self.assertRaises(ValidationError):
            self.verify(assessment)
        self.assertEqual(self.state(), before)

    def test_forged_cache_without_a_scoring_audit_is_rejected(self):
        assessment = self.ready(calculate=False)
        self.raw(ShotPoint).filter(assessment=assessment).update(score=1)
        self.raw(Assessment).filter(pk=assessment.pk).update(total_score=1, scored_at=timezone.now(),
            scored_edit_version=assessment.edit_version, scoring_engine_version=scoring.ENGINE_VERSION,
            scoring_input_sha256="f" * 64, scoring_context={"schema_version": 1, "synthetic_forgery": True})
        before = self.state()
        with self.assertRaises(ValidationError):
            self.verify(assessment)
        self.assertEqual(self.state(), before)

    def test_modified_or_ambiguous_scoring_context_and_audit_are_rejected(self):
        assessment = self.ready()
        original = copy.deepcopy(assessment.scoring_context)
        invalid = copy.deepcopy(original)
        invalid["results"][0]["x_mm"]["numerator"] = "999"
        self.raw(Assessment).filter(pk=assessment.pk).update(scoring_context=invalid)
        before = self.state()
        with self.assertRaises(ValidationError):
            self.verify(assessment)
        self.assertEqual(self.state(), before)
        self.raw(Assessment).filter(pk=assessment.pk).update(scoring_context=original)
        event = AuditEvent.objects.get(action=calculator.ACTION)
        AuditEvent.objects.create(actor_kind="user", actor_user=self.actor, session=self.session, assessment=assessment,
            action=calculator.ACTION, reason="Synthetic ambiguous ledger", before_state=event.before_state,
            after_state=event.after_state, version_before=event.version_before, version_after=event.version_after)
        before = self.state()
        with self.assertRaises(ValidationError):
            self.verify(assessment)
        self.assertEqual(self.state(), before)

    def test_ai_import_provenance_must_exist_and_match_immutable_run(self):
        assessment = self.ready(ai=True)
        run = assessment.detection_run
        original = copy.deepcopy(run.raw_result)
        invalid = copy.deepcopy(original)
        invalid["detections"][0]["bbox_xyxy"] = [38, 29, 42, 31]
        self.raw(DetectionRun).filter(pk=run.pk).update(raw_result=invalid)
        before = self.state()
        with self.assertRaises(ValidationError):
            self.verify(assessment)
        self.assertEqual(self.state(), before)
        self.raw(DetectionRun).filter(pk=run.pk).update(raw_result=original)
        self.raw(AuditEvent).filter(action=detection_import_services.ACTION).delete()  # Trusted fixture only.
        before = self.state()
        with self.assertRaises(ValidationError):
            self.verify(assessment)
        self.assertEqual(self.state(), before)

    def test_missing_or_modified_photo_bytes_prevent_verify_and_lock(self):
        assessment = self.ready()
        path = Path(self.storage.path(assessment.photo.storage_key))
        original = path.read_bytes()
        path.write_bytes(original + b"synthetic corruption")  # Privileged fixture, not public storage API.
        before = self.state()
        with self.assertRaises(ValidationError):
            self.verify(assessment)
        self.assertEqual(self.state(), before)
        path.write_bytes(original)
        self.verify(assessment)
        before = self.state()
        path.unlink()
        with self.assertRaises(ValidationError):
            self.lock(assessment)
        self.assertEqual(self.state(), before)

    def test_verified_record_cannot_drift_before_lock(self):
        assessment = self.ready()
        self.verify(assessment)
        self.raw(Assessment).filter(pk=assessment.pk).update(notes="Synthetic SQL drift after verification")
        before = self.state()
        with self.assertRaises(ValidationError):
            self.lock(assessment)
        self.assertEqual(self.state(), before)

    def test_synthetic_verified_state_without_verification_audit_cannot_lock(self):
        assessment = self.ready()
        self.raw(Assessment).filter(pk=assessment.pk).update(status="verified", verified_by_id=self.actor.pk,
            verified_at=timezone.now(), edit_version=assessment.edit_version + 1, scored_edit_version=assessment.edit_version + 1)
        before = self.state()
        with self.assertRaises(ValidationError):
            self.lock(assessment)
        self.assertEqual(self.state(), before)

    def test_retired_evidence_token_changes_after_verify_require_review_not_silent_lock(self):
        assessment = self.ready()
        self.verify(assessment)
        self.target.retire()
        before = self.state()
        with self.assertRaises(ValidationError):
            self.lock(assessment)
        self.assertEqual(self.state(), before)

    def test_existing_retired_references_scored_with_current_versions_remain_supported(self):
        assessment = self.ready(calculate=False)
        self.target.retire()
        self.calculate(assessment)
        self.verify(assessment)
        self.lock(assessment)
        assessment.refresh_from_db()
        self.assertEqual(assessment.status, "locked")

    def test_audit_failure_rolls_back_verify_and_lock_tokens_and_metadata(self):
        assessment = self.ready()
        for command in (commands.verify_assessment, commands.lock_assessment):
            if command is commands.lock_assessment:
                self.verify(assessment)
            for error in (ValidationError("Synthetic audit failure"), RuntimeError("Unexpected audit failure")):
                before = self.state()
                with self.subTest(command=command.__name__, error=error), patch.object(AuditEvent, "save", side_effect=error) as audit:
                    with self.assertRaises(type(error)):
                        command(**self.args(assessment))
                    audit.assert_called_once()
                self.assertEqual(self.state(), before)

    def test_persistence_failure_rolls_back_without_audit_or_partial_status(self):
        assessment = self.ready()
        before = self.state()
        with patch("assessments.finalization_services.models.Model.save", side_effect=RuntimeError("Synthetic write failure")):
            with self.assertRaises(RuntimeError):
                self.verify(assessment)
        self.assertEqual(self.state(), before)

    def test_uuid_insert_collision_rolls_back_status_and_cache_validity_token(self):
        assessment = self.ready()
        args = self.args(assessment)
        commands.verify_assessment(**args)
        before = self.state()
        # Simulate a UUID observed only at INSERT, not actual SQLite concurrency.
        with patch.object(commands, "_replay", return_value=None), self.assertRaises(services.OperationConflict):
            commands.lock_assessment(**self.args(assessment, operation_id=args["operation_id"]))
        self.assertEqual(self.state(), before)

    def test_read_only_engine_failure_rolls_back_and_does_not_rewrite_cache(self):
        assessment = self.ready()
        before = self.state()
        with patch.object(scoring, "score_target", side_effect=RuntimeError("Synthetic validation engine failure")):
            with self.assertRaises(RuntimeError):
                self.verify(assessment)
        self.assertEqual(self.state(), before)

    def test_locked_result_point_and_public_status_guards_remain_closed(self):
        assessment = self.ready()
        self.verify(assessment)
        self.lock(assessment)
        before = self.state()
        assessment.refresh_from_db()
        assessment.notes = "Forbidden"
        with self.assertRaises(ValidationError):
            assessment.save(expected_edit_version=assessment.edit_version)
        point = assessment.shot_points.get()
        point.score, point.x_px = None, 41
        with self.assertRaises(ValidationError):
            point.save(expected_edit_version=assessment.edit_version)
        with self.assertRaises(ValidationError):
            point.delete(expected_edit_version=assessment.edit_version)
        with self.assertRaises(ValidationError):
            self.command(services.update_draft, assessment=assessment, data={"notes": "Forbidden"})
        with self.assertRaises(ValidationError):
            self.command(services.add_shot_point, assessment=assessment, data={"x_px": 1, "y_px": 1})
        with self.assertRaises(ValidationError):
            self.calculate(assessment)
        self.assertEqual(self.state(), before)

    def test_backward_server_time_is_rejected_without_inventing_a_timestamp(self):
        assessment = self.ready()
        before = self.state()
        with patch.object(commands.timezone, "now", return_value=assessment.created_at - timedelta(seconds=1)):
            with self.assertRaises(ValidationError):
                self.verify(assessment)
        self.assertEqual(self.state(), before)

    def test_foreign_scope_assessment_and_missing_target_are_not_accessible(self):
        assessment = self.ready()
        other_session = TrainingSession.objects.create(participation=self.session.participation, session_number=2,
            assigned_assessor=self.actor, created_by=self.actor, weapon_type="pistol", distance_m=20)
        before = self.state()
        with self.assertRaises(ValidationError):
            commands.verify_assessment(**self.args(assessment, session_id=other_session.pk))
        with self.assertRaises(ValidationError):
            commands.verify_assessment(**self.args(assessment, assessment_id=999999))
        self.assertEqual(self.state(), before)

    def test_lock_order_and_authorization_recheck_follow_existing_scoring_order(self):
        assessment = self.ready(ai=True)
        args = self.args(assessment)
        with traced_locks() as trace, patch.object(commands, "_authorize", wraps=commands._authorize) as authorize:
            commands.verify_assessment(**args)
        names = [item[0] for item in trace]
        self.assertEqual(names[:11], ["TrainingSession", "ActivityParticipation", "Assessment", "ShotPoint",
            "TargetPhoto", "DetectionRun", "Calibration", "TargetVersion", "TargetRing", "ScoringRuleVersion", "User"])
        self.assertEqual(names[11], "ActivityPermissionGrant")
        self.assertTrue(all(not item[2] for item in trace))
        self.assertEqual([call.kwargs.get("lock", False) for call in authorize.call_args_list], [False, True])

    def test_full_import_measured_calibration_scoring_verify_lock_pipeline(self):
        from assessments import calibration_services
        profile = {"id": "synthetic-finalization-profile", "target_version_id": self.target.pk,
            "width_mm": "200", "height_mm": "300", "measurement_reference": "Synthetic only; no field metrology",
            "coordinate_contract": scoring.COORDINATE_CONTRACT,
            "fit_landmarks_mm": [{"id": key, "x_mm": x, "y_mm": y} for key, x, y in
                                  (("A", -10, 10), ("B", 10, 10), ("C", 10, -10), ("D", -10, -10))],
            "check_landmarks_mm": [{"id": "E", "x_mm": 0, "y_mm": 0}],
            "max_error_mm": "0.01", "min_area_ratio": "0.01", "min_denominator_ratio": "0.01"}
        run = self.finish_run(result={"schema_version": 1, "detections": [
            {"id": "e2e-origin", "class_id": 0, "confidence": 0.9, "bbox_xyxy": [29, 29, 31, 31]}]})
        assessment = self.make_assessment(photo=run.photo, detection_run=run, scoring_rule_version=self.rule)
        detection_import_services.import_detection_points(actor=self.actor, session_id=self.session.pk,
            assessment_id=assessment.pk, detection_run_id=run.pk, expected_edit_version=1,
            operation_id=uuid4(), reason="Synthetic end-to-end import")
        with override_settings(TARGET_CALIBRATION_PROFILES=(profile,)):
            _, digest = calibration_services.load_profile(profile["id"])
            calibration_services.calibrate_draft(actor=self.actor, session_id=self.session.pk, assessment_id=assessment.pk,
                photo_id=run.photo_id, target_version_id=self.target.pk, expected_edit_version=2,
                expected_target_edit_version=self.target.edit_version, profile_id=profile["id"], expected_profile_sha256=digest,
                observations={"A": [10, 10], "B": [50, 10], "C": [50, 50], "D": [10, 50], "E": [30, 30]},
                operation_id=uuid4(), reason="Synthetic end-to-end calibration")
        self.calculate(assessment)
        self.verify(assessment)
        self.lock(assessment)
        assessment.refresh_from_db()
        self.assertEqual((assessment.status, assessment.total_score, assessment.edit_version), ("locked", 1, 6))
        self.assertEqual(assessment.shot_points.get().origin_detection_id, "e2e-origin")
        self.assertEqual(list(AuditEvent.objects.order_by("id").values_list("action", flat=True)),
            [detection_import_services.ACTION, calibration_services.ACTION, calculator.ACTION, commands.VERIFY, commands.LOCK])
