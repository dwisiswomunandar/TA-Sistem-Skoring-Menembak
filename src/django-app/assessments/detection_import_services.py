"""Audited append-only import into a selected draft; no inference or scoring."""

import hashlib
import json
from decimal import Decimal
from fractions import Fraction

from django.core.exceptions import ValidationError
from django.db import models, router, transaction

from activities.authorization import authorize_draft
from activities.history import lock_participation_references, lock_session_reference
from .models import Assessment, AuditEvent, DetectionRun, ShotPoint, TargetPhoto
from .result_models import check_version, context_for
from .result_validators import same_json
from .services import (
    _append_audit, _assessment_snapshot, _identifier, _invalidate,
    _operation, _receipt, _replay, _text,
)
from .validators import validate_detection_result, validate_strict_json

ACTION = "assessment.detections_imported"
# Both change_assessment and add_shotpoint, their scope grants and assignment.
AUTHORIZATION_ACTION = "shotpoint.added"
CONVERSION_VERSION = "analysis-bbox-midpoint-half-even-6dp-v1"


def _digest(value):
    validate_strict_json(value)
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _midpoint(first, second):
    # Exact decimal interpretation and half-even rounding, independent of the
    # process Decimal context. Six decimals match ShotPoint storage precision.
    scaled = round((Fraction(str(first)) + Fraction(str(second))) * 500_000)
    return Decimal(f"{scaled // 1_000_000}.{scaled % 1_000_000:06d}")


def _validate_source(run, photo):
    validate_detection_result(run.raw_result, width=photo.analysis_width_px, height=photo.analysis_height_px)
    # Schema-1 legacy adapter results may contain only schema/detections. When
    # the full FastAPI envelope is present, every supplied identity must agree.
    expected = {"status": "succeeded", "analysis_width_px": photo.analysis_width_px,
        "analysis_height_px": photo.analysis_height_px, "preprocessing_version": photo.preprocessing_version,
        "input_sha256": photo.sha256, "model_name": run.model_name, "weights_sha256": run.weights_sha256,
        "runtime_version": run.runtime_version, "adapter_version": run.adapter_version, "parameters": run.parameters}
    for name, value in expected.items():
        if name in run.raw_result and not same_json({"value": run.raw_result[name]}, {"value": value}):
            raise ValidationError("Detection envelope does not match the persisted photo/run identity.")


def import_detection_points(*, actor, session_id, assessment_id, detection_run_id,
                            expected_edit_version, operation_id, reason, using=None):
    """Append every validated detection once; return an existing DraftReceipt.

    Photo/run must already be selected on the latest draft. Empty success imports
    zero points and invalidates cached scoring; it never invents shots or scores.
    """
    using = using or router.db_for_write(Assessment)
    authorize_draft(actor=actor, action=AUTHORIZATION_ACTION, session_id=session_id, using=using)
    session_id = _identifier(session_id, "session_id")
    assessment_id = _identifier(assessment_id, "assessment_id")
    detection_run_id = _identifier(detection_run_id, "detection_run_id")
    expected_edit_version = _identifier(expected_edit_version, "expected_edit_version")
    operation_id = _operation(operation_id)
    reason = _text(reason, "reason", required=True)
    request = {"schema_version": 1, "action": ACTION, "actor_user_id": actor.pk,
               "session_id": session_id, "assessment_id": assessment_id,
               "detection_run_id": detection_run_id,
               "expected_edit_version": expected_edit_version, "reason": reason}
    digest = _digest(request)
    with transaction.atomic(using=using):
        session = lock_session_reference(session_id, using)
        lock_participation_references([session.participation_id], using)
        assessment = Assessment.objects.using(using).select_for_update().filter(
            pk=assessment_id, session_id=session.pk).first()
        if assessment is None:
            raise ValidationError("Draft is unavailable in this session.")
        points = list(ShotPoint.objects.using(using).select_for_update().filter(
            assessment_id=assessment.pk).order_by("pk"))
        photo = TargetPhoto.objects.using(using).select_for_update().filter(
            pk=assessment.photo_id, session_id=session.pk).first()
        # Never lock foreign evidence selected by an arbitrary client identifier.
        run = DetectionRun.objects.using(using).select_for_update().filter(
            pk=detection_run_id, photo_id=photo.pk).first() if photo is not None else None
        current = authorize_draft(actor=actor, action=AUTHORIZATION_ACTION,
            session_id=session.pk, using=using, lock=True)
        replay = _replay(operation_id, digest, ACTION, current, session.pk, using)
        if replay is not None:
            return replay  # Historical receipt, never restoration of old state.
        if assessment.status != Assessment.Status.DRAFT:
            raise ValidationError("Only the latest draft can import detections.")
        check_version(assessment, expected_edit_version)
        if Assessment.objects.using(using).latest_working(session_id=session.pk).pk != assessment.pk:
            raise ValidationError("Only the latest draft revision can import detections.")
        if not same_json(context_for(session, using), assessment.historical_context):
            raise ValidationError("Historical session context has drifted.")
        if (photo is None or run is None or assessment.detection_run_id != run.pk
                or run.status != DetectionRun.Status.SUCCEEDED):
            raise ValidationError("Select a succeeded run of this draft's session photo first.")
        assessment.validate_evidence(using)
        for instance in (session, assessment, photo, run, *points):
            instance.full_clean()
        _validate_source(run, photo)
        detections = run.raw_result["detections"]
        existing_ids = {point.origin_detection_id for point in points if point.source == ShotPoint.Source.AI}
        if any(row["id"] in existing_ids for row in detections):
            raise ValidationError("This draft already contains points from the selected detections.")
        # Assessment lock serializes compliant importers, including empty imports.
        # The completion marker survives later point correction/deletion.
        if AuditEvent.objects.using(using).filter(assessment_id=assessment.pk, action=ACTION,
                after_state__detection_import__run_id=run.pk).exists():
            raise ValidationError("This run was already imported into this assessment.")
        number = max((point.point_number for point in points), default=0)
        prepared = []
        for index, row in enumerate(detections, 1):
            x1, y1, x2, y2 = row["bbox_xyxy"]
            point = ShotPoint(assessment=assessment, point_number=number + index,
                source=ShotPoint.Source.AI, origin_detection_id=row["id"],
                x_px=_midpoint(x1, x2), y_px=_midpoint(y1, y2),
                is_included=True, exclusion_reason="", score=None)
            point.full_clean()  # Reject rounded points outside the raster; no clip.
            prepared.append(point)
        before = _assessment_snapshot(assessment, points)
        version_before = assessment.edit_version
        _invalidate(assessment, points, using)
        for point in prepared:
            # Bounded internal INSERT after validation under aggregate locks.
            # Public save bumps the parent per point; this batch bumps it once.
            # No public bypass flag or weakened model/queryset guard is introduced.
            models.Model.save(point, using=using, force_insert=True)
        assessment.save(using=using, expected_edit_version=version_before)
        assessment.refresh_from_db(using=using)
        after_points = list(ShotPoint.objects.using(using).filter(assessment_id=assessment.pk).order_by("pk"))
        after = _assessment_snapshot(assessment, after_points)
        after["command_sha256"] = digest
        after["command_result"] = {"assessment_id": assessment.pk,
                                  "edit_version": assessment.edit_version, "point_id": None}
        after["detection_import"] = {"run_id": run.pk, "photo_id": photo.pk,
            "conversion_version": CONVERSION_VERSION, "imported_count": len(prepared),
            "point_ids": [point.pk for point in prepared],
            "raw_result_sha256": _digest(run.raw_result), "parameters_sha256": _digest(run.parameters),
            "model_name": run.model_name, "weights_sha256": run.weights_sha256,
            "runtime_version": run.runtime_version, "adapter_version": run.adapter_version,
            "photo_sha256": photo.sha256, "preprocessing_version": photo.preprocessing_version,
            "analysis_width_px": photo.analysis_width_px, "analysis_height_px": photo.analysis_height_px}
        event = _append_audit(using=using, operation_id=operation_id, actor_kind="user", actor_user=current,
            session=session, assessment=assessment, action=ACTION, reason=reason,
            before_state=before, after_state=after,
            version_before=version_before, version_after=assessment.edit_version)
        return _receipt(event)
