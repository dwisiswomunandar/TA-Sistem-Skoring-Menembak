"""Atomic audited draft-score cache. No verification, locking or public model bypass."""

import hashlib
import json
from decimal import Decimal
from fractions import Fraction

from django.core.exceptions import ValidationError
from django.db import models, router, transaction
from django.utils import timezone

from activities.authorization import authorize_draft
from activities.history import lock_participation_references, lock_session_reference
from . import scoring
from .models import Assessment, Calibration, DetectionRun, ScoringRuleVersion, ShotPoint, TargetPhoto, TargetRing, TargetVersion
from .result_models import check_version, context_for
from .result_validators import same_json, validate_snapshot
from .scoring_inputs import scoring_input_from_models
from .services import _append_audit, _assessment_snapshot, _identifier, _operation, _receipt, _replay, _text

ACTION = "assessment.draft_scored"
# Existing edit permission + activity grant + assigned_assessor; no new capability.
AUTHORIZATION_ACTION = "assessment.draft_updated"
EVIDENCE_NAMES = ("calibration", "target_version", "scoring_rule_version")


def _expected_evidence(value):
    if type(value) is not dict or set(value) != set(EVIDENCE_NAMES):
        raise ValidationError("Supply explicit calibration, target_version and scoring_rule_version expectations.")
    result = {}
    for name in EVIDENCE_NAMES:
        item = value[name]
        if type(item) is not dict or set(item) != {"id", "edit_version"}:
            raise ValidationError("Each evidence expectation requires only id and edit_version.")
        result[name] = {key: _identifier(item[key], f"{name}.{key}") for key in ("id", "edit_version")}
    return result


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _decimal_text(value):
    # Same decimal interpretation as MVP-01, independent of Decimal context.
    decimal = value if isinstance(value, Decimal) else Decimal(str(value))
    rendered = format(decimal, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return "0" if decimal.is_zero() else rendered


def _ratio(value: Fraction):
    return {"numerator": str(value.numerator), "denominator": str(value.denominator)}


def _lock_evidence(assessment, using):
    def row(model, identifier):
        return model.objects.using(using).select_for_update().filter(pk=identifier).first() if identifier is not None else None
    # Calibration -> Target matches calibration acceptance; Target -> rings matches
    # aggregate ring writes. No earlier domain locks are acquired after this suffix.
    photo = row(TargetPhoto, assessment.photo_id)
    run = row(DetectionRun, assessment.detection_run_id)
    calibration = row(Calibration, assessment.calibration_id)
    target = row(TargetVersion, calibration.target_version_id) if calibration else None
    rings = list(TargetRing.objects.using(using).select_for_update().filter(
        target_version_id=target.pk).order_by("pk")) if target else []
    rule = row(ScoringRuleVersion, assessment.scoring_rule_version_id)
    return photo, run, calibration, target, rings, rule


def _input_snapshot(assessment, session, photo, run, calibration, target, rings, rule, points):
    return {"schema_version": 1, "coordinate_contract": scoring.COORDINATE_CONTRACT,
        "assessment": {"id": assessment.pk, "revision_number": assessment.revision_number,
                       "edit_version": assessment.edit_version},
        "session": context_for(session, assessment._state.db),
        "photo": {"id": photo.pk, "sha256": photo.sha256, "preprocessing_version": photo.preprocessing_version,
                  "analysis_width_px": photo.analysis_width_px, "analysis_height_px": photo.analysis_height_px},
        "detection_run": None if run is None else {"id": run.pk, "photo_id": run.photo_id,
            "model_name": run.model_name, "weights_sha256": run.weights_sha256,
            "runtime_version": run.runtime_version, "adapter_version": run.adapter_version},
        "calibration": {"id": calibration.pk, "edit_version": calibration.edit_version,
            "photo_id": calibration.photo_id, "target_version_id": calibration.target_version_id,
            "method_version": calibration.method_version,
            "transform_matrix": [[_decimal_text(v) for v in row] for row in calibration.transform_matrix]},
        "target_version": {"id": target.pk, "code": target.code, "version": target.version,
            "edit_version": target.edit_version, "width_mm": _decimal_text(target.width_mm),
            "height_mm": _decimal_text(target.height_mm), "rings": [
                {"id": ring.pk, "edit_version": ring.edit_version, "ring_number": ring.ring_number,
                 "outer_radius_mm": _decimal_text(ring.outer_radius_mm), "score": ring.score}
                for ring in sorted(rings, key=lambda item: item.ring_number)]},
        "scoring_rule_version": {"id": rule.pk, "code": rule.code, "version": rule.version,
            "edit_version": rule.edit_version, "method": rule.method, "algorithm_version": rule.algorithm_version,
            "boundary_policy": rule.boundary_policy, "numeric_precision": rule.numeric_precision},
        "points": [{"id": point.pk, "point_number": point.point_number, "x_px": _decimal_text(point.x_px),
            "y_px": _decimal_text(point.y_px), "source": point.source,
            "origin_detection_id": point.origin_detection_id, "is_included": point.is_included,
            "exclusion_reason": point.exclusion_reason} for point in sorted(points, key=lambda item: item.point_number)]}


def score_draft(*, actor, session_id, assessment_id, expected_edit_version,
                expected_evidence_versions, operation_id, reason, using=None):
    """Persist a draft calculation and one audit, or roll back everything.

    Caller supplies a trusted authenticated User, IDs and expected versions only;
    no caller-supplied scores, collections, matrices, caliber or scope assertions.
    """
    using = using or router.db_for_write(Assessment)
    authorize_draft(actor=actor, action=AUTHORIZATION_ACTION, session_id=session_id, using=using)
    session_id = _identifier(session_id, "session_id")
    assessment_id = _identifier(assessment_id, "assessment_id")
    expected_edit_version = _identifier(expected_edit_version, "expected_edit_version")
    expected = _expected_evidence(expected_evidence_versions)
    operation_id = _operation(operation_id)
    reason = _text(reason, "reason", required=True)
    request = {"schema_version": 1, "action": ACTION, "actor_user_id": actor.pk,
        "session_id": session_id, "assessment_id": assessment_id, "expected_edit_version": expected_edit_version,
        "expected_evidence_versions": expected, "reason": reason}
    command_digest = _digest(request)
    with transaction.atomic(using=using):
        session = lock_session_reference(session_id, using)
        lock_participation_references([session.participation_id], using)
        assessment = Assessment.objects.using(using).select_for_update().filter(
            pk=assessment_id, session_id=session.pk).first()
        if assessment is None:
            raise ValidationError("Draft is unavailable in this session.")
        points = list(ShotPoint.objects.using(using).select_for_update().filter(assessment_id=assessment.pk).order_by("pk"))
        photo, run, calibration, target, rings, rule = _lock_evidence(assessment, using)
        current_actor = authorize_draft(actor=actor, action=AUTHORIZATION_ACTION, session_id=session.pk, using=using, lock=True)
        replay = _replay(operation_id, command_digest, ACTION, current_actor, session.pk, using)
        if replay is not None:
            return replay
        if assessment.status != Assessment.Status.DRAFT:
            raise ValidationError("Only the latest draft can be scored; verified/locked remain immutable.")
        check_version(assessment, expected_edit_version)
        if Assessment.objects.using(using).latest_working(session_id=session.pk).pk != assessment.pk:
            raise ValidationError("Only the latest draft can be scored.")
        if not same_json(context_for(session, using), assessment.historical_context):
            raise ValidationError("Historical session context has drifted.")
        for name, evidence in zip(EVIDENCE_NAMES, (calibration, target, rule)):
            if evidence is None or {"id": evidence.pk, "edit_version": evidence.edit_version} != expected[name]:
                raise ValidationError({"expected_evidence_versions": "Evidence is missing, changed or stale."})
        if photo is None or (assessment.detection_run_id is not None and run is None):
            raise ValidationError("Complete matching evidence is required.")
        assessment.validate_evidence(using)
        # Revalidate provenance, geometry, field constraints and AI detection IDs
        # from persisted rows, even for excluded points. No filesystem/AI is run.
        for instance in (session, assessment, photo, calibration, target, rule, *rings, *points):
            instance.full_clean()
        if run is not None:
            run.full_clean()
        try:
            data = scoring_input_from_models(assessment=assessment, session=session, photo=photo, target=target,
                rings=rings, rule=rule, calibration=calibration, points=points,
                coordinate_contract=scoring.COORDINATE_CONTRACT)
            result = scoring.score_target(data)
        except scoring.ScoringError as error:
            raise ValidationError({"scoring": str(error)}) from error
        before = _assessment_snapshot(assessment, points)
        input_state = _input_snapshot(assessment, session, photo, run, calibration, target, rings, rule, points)
        input_digest = _digest(input_state)
        result_by_number = {point.point_number: point for point in result.points}
        context = {"schema_version": 1, "engine_version": result.engine_version,
            "input": input_state, "input_sha256": input_digest, "total_score": result.total_score,
            "results": [{"point_id": point.pk, "point_number": point.point_number,
                "x_mm": _ratio(result_by_number[point.point_number].x_mm),
                "y_mm": _ratio(result_by_number[point.point_number].y_mm),
                "ring_number": result_by_number[point.point_number].ring_number,
                "score": result_by_number[point.point_number].score}
                for point in sorted(points, key=lambda item: item.point_number)]}
        validate_snapshot(context)
        version_before = assessment.edit_version
        for point in points:
            point.score = result_by_number[point.point_number].score
            point.full_clean()
            # Private bounded write: locked draft, server result, no public flag
            # or ORM manager escape hatch is added. Aggregate version bumps once.
            models.Model.save(point, using=using, update_fields=("score", "updated_at"))
        assessment.edit_version += 1
        assessment.total_score = result.total_score
        assessment.scoring_context = context
        assessment.scoring_engine_version = result.engine_version
        assessment.scoring_input_sha256 = input_digest
        assessment.scored_edit_version = assessment.edit_version
        assessment.scored_at = timezone.now()
        assessment.full_clean()
        models.Model.save(assessment, using=using, update_fields=(
            "edit_version", "total_score", "scoring_context", "scoring_engine_version",
            "scoring_input_sha256", "scored_edit_version", "scored_at", "updated_at"))
        after = _assessment_snapshot(assessment, points)
        after.update(command_sha256=command_digest, scoring_context=context,
                     command_result={"assessment_id": assessment.pk, "edit_version": assessment.edit_version, "point_id": None})
        event = _append_audit(using=using, operation_id=operation_id, actor_kind="user", actor_user=current_actor,
            session=session, assessment=assessment, action=ACTION, reason=reason,
            before_state=before, after_state=after, version_before=version_before, version_after=assessment.edit_version)
        return _receipt(event)
