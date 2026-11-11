"""Audited draft -> verified -> locked, explicitly default-deny and no reopen."""

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models, router, transaction
from django.utils import timezone

from activities.authorization import authorize_draft
from activities.history import lock_participation_references, lock_session_reference
from activities.models import TrainingSession
from . import detection_import_services, scoring, scoring_services as calculator
from .models import Assessment, AuditEvent, ShotPoint
from .photo_metadata import inspect_original
from .result_models import check_version, context_for
from .result_validators import same_json
from .scoring_inputs import scoring_input_from_models
from .services import _append_audit, _assessment_snapshot, _identifier, _operation, _receipt, _replay, _text

VERIFY = "assessment.verified"
LOCK = "assessment.locked"
AUTHORIZATION_ACTION = "assessment.draft_updated"
DENIED = "Assessment finalization is not authorized."
POLICY_KEYS = {"activity_id", "verify_user_ids", "lock_user_ids", "allow_self_verification",
               "allow_verifier_to_lock", "allow_empty_results", "allow_manual_only_results", "approval_reference"}


def _policy(activity_id):
    try:
        rows = getattr(settings, "ASSESSMENT_FINALIZATION_POLICIES", ())
        if type(rows) not in (list, tuple) or any(type(row) is not dict or set(row) != POLICY_KEYS for row in rows):
            raise ValueError
        ids = [_identifier(row["activity_id"], "activity_id") for row in rows]
        if len(set(ids)) != len(ids):
            raise ValueError
        row = next(row for row in rows if row["activity_id"] == activity_id)
        policy = {"activity_id": activity_id, "approval_reference": _text(row["approval_reference"], "approval_reference", required=True)}
        for name in ("verify_user_ids", "lock_user_ids"):
            if type(row[name]) not in (list, tuple):
                raise ValueError
            users = [_identifier(value, name) for value in row[name]]
            if len(set(users)) != len(users):
                raise ValueError
            policy[name] = sorted(users)
        for name in ("allow_self_verification", "allow_verifier_to_lock", "allow_empty_results", "allow_manual_only_results"):
            if type(row[name]) is not bool:
                raise ValueError
            policy[name] = row[name]
        return policy
    except (KeyError, TypeError, ValueError, StopIteration, ValidationError):
        raise PermissionDenied(DENIED) from None


def _authorize(actor, session_id, action, using, *, lock=False, assessment=None):
    current = authorize_draft(actor=actor, action=AUTHORIZATION_ACTION, session_id=session_id, using=using, lock=lock)
    activity_id = TrainingSession.objects.using(using).filter(pk=session_id).values_list("participation__activity_id", flat=True).first()
    policy = _policy(activity_id)
    key = "verify_user_ids" if action == VERIFY else "lock_user_ids"
    if current.pk not in policy[key]:
        raise PermissionDenied(DENIED)
    # Existing authorization requires assigned_assessor: that is conservatively a
    # self-verifier. Independent reviewer assignment is NOT invented by this service.
    if action == VERIFY and not policy["allow_self_verification"]:
        raise PermissionDenied(DENIED)
    if action == LOCK and assessment is not None and assessment.verified_by_id == current.pk and not policy["allow_verifier_to_lock"]:
        raise PermissionDenied(DENIED)
    return current, policy


def _snapshot(assessment, points):
    state = _assessment_snapshot(assessment, points)
    state["identity"] = {"created_by_id": assessment.created_by_id, "created_at": assessment.created_at.isoformat(),
        "updated_at": assessment.updated_at.isoformat(), "revision_reason": assessment.revision_reason}
    state["lifecycle"] = {"verified_by_id": assessment.verified_by_id, "locked_by_id": assessment.locked_by_id,
        "verified_at": assessment.verified_at.isoformat() if assessment.verified_at else None,
        "locked_at": assessment.locked_at.isoformat() if assessment.locked_at else None}
    state["historical_context_sha256"] = calculator._digest(assessment.historical_context)
    state["scoring_context_sha256"] = calculator._digest(assessment.scoring_context)
    return state


def _event(action, assessment, version, using):
    rows = list(AuditEvent.objects.using(using).filter(action=action, assessment_id=assessment.pk,
        session_id=assessment.session_id, version_after=version).order_by("pk")[:2])
    if (len(rows) != 1 or rows[0].actor_kind != "user" or type(rows[0].version_before) is not int
            or rows[0].version_before < 1 or rows[0].version_after != rows[0].version_before + 1):
        raise ValidationError("A unique matching audited scoring/verification decision is required.")
    return rows[0]


def _score_source(action, assessment, points, using):
    if action == VERIFY:
        event = _event(calculator.ACTION, assessment, assessment.edit_version, using)
        return event, event
    verified = _event(VERIFY, assessment, assessment.edit_version, using)
    snapshot = _snapshot(assessment, points)
    if (verified.actor_user_id != assessment.verified_by_id or not isinstance(verified.after_state, dict)
            or not same_json(snapshot, {key: verified.after_state.get(key) for key in snapshot})):
        raise ValidationError("Verified state no longer matches its committed audit.")
    source = _event(calculator.ACTION, assessment, verified.version_before, using)
    metadata = verified.after_state.get("transition", {})
    if (verified.caused_by_event_id != source.pk or not isinstance(metadata, dict)
            or metadata.get("source_scoring_event_id") != source.pk
            or metadata.get("source_scored_version") != source.version_after):
        raise ValidationError("Verification has no matching audited scoring lineage.")
    return source, verified


def _validate_ai_provenance(assessment, points, photo, run, using):
    ai_points = [point for point in points if point.source == "ai"]
    if run is not None:
        detection_import_services._validate_source(run, photo)
    if not ai_points:
        return
    events = list(AuditEvent.objects.using(using).filter(action=detection_import_services.ACTION,
        assessment_id=assessment.pk, session_id=assessment.session_id,
        after_state__detection_import__run_id=run.pk).order_by("pk")[:2])
    if len(events) != 1 or events[0].actor_kind != "user":
        raise ValidationError("AI points require their matching audited import provenance.")
    after = events[0].after_state
    metadata = after.get("detection_import", {})
    expected = {"photo_id": photo.pk, "photo_sha256": photo.sha256,
        "preprocessing_version": photo.preprocessing_version, "analysis_width_px": photo.analysis_width_px,
        "analysis_height_px": photo.analysis_height_px, "model_name": run.model_name,
        "weights_sha256": run.weights_sha256, "runtime_version": run.runtime_version, "adapter_version": run.adapter_version,
        "raw_result_sha256": calculator._digest(run.raw_result), "parameters_sha256": calculator._digest(run.parameters)}
    if not isinstance(metadata, dict) or any(metadata.get(key) != value for key, value in expected.items()):
        raise ValidationError("Detection provenance changed after import.")
    ids, imported = metadata.get("point_ids"), after.get("shot_points")
    if not isinstance(ids, list) or not isinstance(imported, list):
        raise ValidationError("AI import provenance is incomplete.")
    original = {row.get("id"): row for row in imported if isinstance(row, dict) and type(row.get("id")) is int and row["id"] in ids}
    for point in ai_points:
        row = original.get(point.pk, {})
        if row.get("source") != "ai" or row.get("origin_detection_id") != point.origin_detection_id or row.get("point_number") != point.point_number:
            raise ValidationError("AI point identity no longer matches its original detection import.")


def _validate_score(action, assessment, session, points, evidence, using):
    photo, run, calibration, target, rings, rule = evidence
    if (any(row is None for row in (photo, calibration, target, rule))
            or (assessment.detection_run_id is not None and run is None)
            or assessment.total_score is None or assessment.scored_at is None
            or assessment.scored_edit_version != assessment.edit_version
            or not isinstance(assessment.scoring_context, dict)):
        raise ValidationError("Complete matching evidence and a current audited score are required.")
    assessment.validate_evidence(using)
    for row in (session, assessment, photo, calibration, target, rule, *rings, *points):
        row.full_clean()
    if run is not None:
        run.full_clean()
    source, cause = _score_source(action, assessment, points, using)
    if not isinstance(source.after_state, dict):
        raise ValidationError("Audited scoring snapshot is missing.")
    original = _assessment_snapshot(assessment, points)
    original["assessment"].update(status="draft", edit_version=source.version_after, scored_edit_version=source.version_after)
    if not same_json(original, {key: source.after_state.get(key) for key in original}):
        raise ValidationError("Current result differs from its audited scoring state.")
    input_state = calculator._input_snapshot(assessment, session, photo, run, calibration, target, rings, rule, points)
    # The original scoring input records the pre-calculation aggregate token.
    # Lifecycle transitions carry cache validity forward without rewriting it.
    input_state["assessment"]["edit_version"] = source.version_before
    digest = calculator._digest(input_state)
    try:
        result = scoring.score_target(scoring_input_from_models(assessment=assessment, session=session,
            photo=photo, target=target, rings=rings, rule=rule, calibration=calibration, points=points,
            coordinate_contract=scoring.COORDINATE_CONTRACT))
    except scoring.ScoringError as error:
        raise ValidationError({"scoring": str(error)}) from error
    by_number = {point.point_number: point for point in result.points}
    expected = {"schema_version": 1, "engine_version": result.engine_version, "input": input_state,
        "input_sha256": digest, "total_score": result.total_score, "results": [
            {"point_id": point.pk, "point_number": point.point_number,
             "x_mm": calculator._ratio(by_number[point.point_number].x_mm),
             "y_mm": calculator._ratio(by_number[point.point_number].y_mm),
             "ring_number": by_number[point.point_number].ring_number,
             "score": by_number[point.point_number].score}
            for point in sorted(points, key=lambda point: point.point_number)]}
    if (assessment.scoring_input_sha256 != digest or assessment.scoring_engine_version != result.engine_version
            or assessment.total_score != result.total_score
            or not same_json(expected, assessment.scoring_context)
            or not same_json(expected, source.after_state.get("scoring_context"))
            or not same_json({"scores": [point.score for point in points]},
                             {"scores": [by_number[point.point_number].score for point in points]})):
        raise ValidationError("Scoring inputs, provenance, context or values are no longer valid.")
    _validate_ai_provenance(assessment, points, photo, run, using)
    verified = inspect_original(photo.storage_key)
    if any(getattr(photo, key) != value for key, value in verified.items()):
        raise ValidationError("Original photo no longer matches its stored identity.")
    if assessment.scored_at < assessment.created_at or assessment.scored_at > source.created_at:
        raise ValidationError("Scoring timestamps are inconsistent with their audit.")
    return source, cause


def _execute(action, *, actor, session_id, assessment_id, expected_edit_version,
             expected_evidence_versions, operation_id, reason, using=None):
    if action not in (VERIFY, LOCK):
        raise PermissionDenied(DENIED)
    using = using or router.db_for_write(Assessment)
    _authorize(actor, session_id, action, using)
    session_id = _identifier(session_id, "session_id")
    assessment_id = _identifier(assessment_id, "assessment_id")
    expected_edit_version = _identifier(expected_edit_version, "expected_edit_version")
    expected = calculator._expected_evidence(expected_evidence_versions)
    operation_id, reason = _operation(operation_id), _text(reason, "reason", required=True)
    request = {"schema_version": 1, "action": action, "actor_user_id": actor.pk, "session_id": session_id,
        "assessment_id": assessment_id, "expected_edit_version": expected_edit_version,
        "expected_evidence_versions": expected, "reason": reason}
    digest = calculator._digest(request)
    with transaction.atomic(using=using):
        session = lock_session_reference(session_id, using)
        lock_participation_references([session.participation_id], using)
        assessment = Assessment.objects.using(using).select_for_update().filter(pk=assessment_id, session_id=session.pk).first()
        if assessment is None:
            raise ValidationError("Assessment is unavailable in this session.")
        points = list(ShotPoint.objects.using(using).select_for_update().filter(assessment_id=assessment.pk).order_by("pk"))
        evidence = calculator._lock_evidence(assessment, using)
        current, policy = _authorize(actor, session.pk, action, using, lock=True, assessment=assessment)
        replay = _replay(operation_id, digest, action, current, session.pk, using)
        if replay is not None:
            return replay
        source_status, target_status = ("draft", "verified") if action == VERIFY else ("verified", "locked")
        if assessment.status != source_status:
            raise ValidationError("Only draft -> verified and verified -> locked transitions are supported.")
        check_version(assessment, expected_edit_version)
        if Assessment.objects.using(using).latest_working(session_id=session.pk).pk != assessment.pk:
            raise ValidationError("Only the latest assessment revision can be finalized.")
        if not same_json(context_for(session, using), assessment.historical_context):
            raise ValidationError("Historical session context has drifted.")
        for name, row in zip(calculator.EVIDENCE_NAMES, (evidence[2], evidence[3], evidence[5])):
            if row is None or {"id": row.pk, "edit_version": row.edit_version} != expected[name]:
                raise ValidationError("Expected evidence is missing or stale.")
        included = [point for point in points if point.is_included]
        if (not included and not policy["allow_empty_results"]) or (included and not any(point.source == "ai" for point in included) and not policy["allow_manual_only_results"]):
            raise PermissionDenied(DENIED)
        scoring_event, cause = _validate_score(action, assessment, session, points, evidence, using)
        now = timezone.now()
        if now < max(assessment.created_at, assessment.scored_at, assessment.verified_at or assessment.created_at):
            raise ValidationError("Server time precedes the existing result history.")
        before = _snapshot(assessment, points)
        version_before = assessment.edit_version
        assessment.status = target_status
        if action == VERIFY:
            assessment.verified_by, assessment.verified_at = current, now
        else:
            assessment.locked_by, assessment.locked_at = current, now
        assessment.edit_version += 1
        # DB invariant requires cache-validity token == aggregate token. Preserve
        # original score time/input hash/context and trace their original audit.
        assessment.scored_edit_version = assessment.edit_version
        assessment.full_clean()
        # Bounded lifecycle write only. Public model/queryset guards remain closed;
        # no caller flag can enable general status, score, evidence or point writes.
        models.Model.save(assessment, using=using, update_fields=("status", "verified_by", "verified_at",
            "locked_by", "locked_at", "edit_version", "scored_edit_version", "updated_at"))
        assessment.refresh_from_db(using=using)
        after = _snapshot(assessment, points)
        after.update(command_sha256=digest, command_result={"assessment_id": assessment.pk,
            "edit_version": assessment.edit_version, "point_id": None})
        after["transition"] = {"from": source_status, "to": target_status,
            "source_scoring_event_id": scoring_event.pk, "source_scored_version": scoring_event.version_after,
            "authorization_policy": policy, "authorization_policy_sha256": calculator._digest(policy)}
        event = _append_audit(using=using, operation_id=operation_id, actor_kind="user", actor_user=current,
            session=session, assessment=assessment, action=action, reason=reason, caused_by_event=cause,
            before_state=before, after_state=after, version_before=version_before, version_after=assessment.edit_version)
        return _receipt(event)


def verify_assessment(*, actor, session_id, assessment_id, expected_edit_version,
                      expected_evidence_versions, operation_id, reason, using=None):
    return _execute(VERIFY, actor=actor, session_id=session_id, assessment_id=assessment_id,
        expected_edit_version=expected_edit_version, expected_evidence_versions=expected_evidence_versions,
        operation_id=operation_id, reason=reason, using=using)


def lock_assessment(*, actor, session_id, assessment_id, expected_edit_version,
                    expected_evidence_versions, operation_id, reason, using=None):
    return _execute(LOCK, actor=actor, session_id=session_id, assessment_id=assessment_id,
        expected_edit_version=expected_edit_version, expected_evidence_versions=expected_evidence_versions,
        operation_id=operation_id, reason=reason, using=using)
