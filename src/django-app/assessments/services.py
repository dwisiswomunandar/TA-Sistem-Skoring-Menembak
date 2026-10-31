"""Atomic, permission-checked draft commands. No scoring or official transitions."""

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, router, transaction

from activities.access_rules import DRAFT_PERMISSIONS
from activities.authorization import authorize_draft
from activities.history import lock_participation_references, lock_session_reference

from .models import Assessment, AuditEvent, Calibration, DetectionRun, ScoringRuleVersion, ShotPoint, TargetPhoto
from .result_models import check_version, context_for
from .result_validators import same_json, validate_snapshot


class OperationConflict(ValidationError):
    """The operation UUID is already bound to a different command."""


@dataclass(frozen=True)
class DraftReceipt:
    operation_id: UUID
    audit_event_id: int
    assessment_id: int
    edit_version: int
    point_id: int | None


PERMISSIONS = DRAFT_PERMISSIONS
ASSESSMENT_FIELDS = frozenset(("notes", "photo_id", "detection_run_id", "calibration_id", "scoring_rule_version_id"))
POINT_FIELDS = frozenset(("x_px", "y_px", "is_included", "exclusion_reason"))
SCORING_FIELDS = {
    "total_score": None, "scoring_context": None, "scoring_engine_version": "",
    "scoring_input_sha256": "", "scored_at": None, "scored_edit_version": None,
}


def _identifier(value, name, *, nullable=False):
    if nullable and value is None:
        return None
    if type(value) is not int or not 0 < value <= 9223372036854775807:
        raise ValidationError({name: "Use a positive 64-bit integer identifier."})
    return value


def _operation(value):
    if not isinstance(value, (UUID, str)):
        raise ValidationError({"operation_id": "An explicit UUID is required."})
    try:
        return UUID(str(value))
    except ValueError as error:
        raise ValidationError({"operation_id": "An explicit UUID is required."}) from error


def _text(value, name, *, required=False):
    if not isinstance(value, str) or (required and not value.strip()):
        raise ValidationError({name: "Supply explicit nonblank text." if required else "Use text."})
    return value.strip()


def _payload(action, data):
    if type(data) is not dict or any(not isinstance(key, str) for key in data):
        raise ValidationError("Command data must be an explicit object with string keys.")
    allowed = ASSESSMENT_FIELDS if action.startswith("assessment.") else POINT_FIELDS
    if action == "shotpoint.deleted":
        allowed = frozenset()
    if set(data) - allowed:
        raise ValidationError("Command contains forbidden or unknown fields.")
    normalized = {}
    for key, value in data.items():
        if key.endswith("_id"):
            normalized[key] = _identifier(value, key, nullable=True)
        elif key in ("notes", "exclusion_reason"):
            normalized[key] = _text(value, key)
        elif key == "is_included":
            if type(value) is not bool:
                raise ValidationError({key: "Use a boolean."})
            normalized[key] = value
        else:
            if type(value) not in (int, float, str, Decimal):
                raise ValidationError({key: "Use a finite decimal coordinate."})
            try:
                coordinate = Decimal(str(value))
            except (InvalidOperation, ValueError) as error:
                raise ValidationError({key: "Use a finite decimal coordinate."}) from error
            # Validate precision before canonicalization; never silently round input.
            coordinate = ShotPoint._meta.get_field(key).clean(coordinate, None)
            rendered = format(coordinate, "f")
            if "." in rendered:
                rendered = rendered.rstrip("0").rstrip(".")
            normalized[key] = "0" if coordinate.is_zero() else rendered
    if action == "assessment.draft_created":
        normalized = {**dict.fromkeys(ASSESSMENT_FIELDS), "notes": "", **normalized}
    if action == "shotpoint.added":
        if not {"x_px", "y_px"} <= set(normalized):
            raise ValidationError("A new manual point requires explicit x_px and y_px.")
        normalized = {"is_included": True, "exclusion_reason": "", **normalized}
    return normalized


def _assessment_snapshot(assessment, points):
    row = None
    if assessment is not None:
        fields = (
            "id", "session_id", "revision_number", "status", "edit_version", "photo_id",
            "detection_run_id", "calibration_id", "scoring_rule_version_id", "notes", "total_score",
            "scoring_engine_version", "scoring_input_sha256", "scored_edit_version",
        )
        row = {name: getattr(assessment, name) for name in fields}
        row["status"] = str(assessment.status)
        row["scored_at"] = assessment.scored_at.isoformat() if assessment.scored_at else None
        row["has_scoring_context"] = assessment.scoring_context is not None
    snapshot = {"schema_version": 1, "assessment": row, "shot_points": [
        {"id": point.pk, "point_number": point.point_number, "x_px": str(point.x_px), "y_px": str(point.y_px),
         "source": point.source, "origin_detection_id": point.origin_detection_id,
         "is_included": point.is_included, "exclusion_reason": point.exclusion_reason, "score": point.score}
        for point in points
    ]}
    validate_snapshot(snapshot)
    return snapshot


def _invalidate(assessment, points, using):
    for point in points:
        if point.score is not None:
            point.score = None
            point.full_clean()
            # Bounded internal invalidation: only NULL scores, draft parent already locked.
            # Do not call point.save(): cache invalidation must not increment the version per row.
            models.Model.save(point, using=using)
    for field, value in SCORING_FIELDS.items():
        setattr(assessment, field, value)


def _apply_evidence(assessment, data, using):
    photo_id = data.get("photo_id", assessment.photo_id)
    if photo_id is not None and not TargetPhoto.objects.using(using).filter(pk=photo_id, session_id=assessment.session_id).exists():
        raise ValidationError("Selected photo is unavailable in this session.")
    for field, model, condition in (
        ("detection_run_id", DetectionRun, {"photo_id": photo_id, "status": "succeeded"}),
        ("calibration_id", Calibration, {"photo_id": photo_id, "status": "accepted"}),
    ):
        identifier = data.get(field, getattr(assessment, field))
        if identifier is not None and (photo_id is None or not model.objects.using(using).filter(pk=identifier, **condition).exists()):
            raise ValidationError("Selected evidence is unavailable for this photo.")
    if "scoring_rule_version_id" in data and data["scoring_rule_version_id"] is not None:
        identifier = data["scoring_rule_version_id"]
        states = ("published", "retired") if identifier == assessment.scoring_rule_version_id else ("published",)
        if not ScoringRuleVersion.objects.using(using).filter(pk=identifier, status__in=states).exists():
            raise ValidationError("Select a published scoring rule for a new reference.")
    for field, value in data.items():
        setattr(assessment, field, value)


def _receipt(event):
    result = event.after_state["command_result"]
    return DraftReceipt(event.operation_id, event.pk, result["assessment_id"], result["edit_version"], result["point_id"])


def _replay(operation_id, digest, action, actor, session_id, using):
    event = AuditEvent.objects.using(using).filter(operation_id=operation_id).first()
    if event is None:
        return None
    if (event.actor_kind != "user" or event.actor_user_id != actor.pk or event.session_id != session_id
            or event.action != action or not isinstance(event.after_state, dict)
            or event.after_state.get("command_sha256") != digest or "command_result" not in event.after_state):
        raise OperationConflict("Operation UUID is already bound to a different command.")
    return _receipt(event)


def _append_audit(*, using, **values):
    try:
        with transaction.atomic(using=using):
            event = AuditEvent(**values)
            event.save(using=using)
            return event
    except (IntegrityError, ValidationError) as error:
        # A UUID collision on another session must roll back the whole outer command.
        if AuditEvent.objects.using(using).filter(operation_id=values["operation_id"]).exists():
            raise OperationConflict("Operation UUID is already bound to a different command.") from error
        raise


def _execute(action, *, actor, session_id, operation_id, expected_edit_version, data,
             reason, assessment_id=None, point_id=None, using=None):
    using = using or router.db_for_write(Assessment)
    authorize_draft(actor=actor, action=action, session_id=session_id, using=using)
    session_id = _identifier(session_id, "session_id")
    if assessment_id is not None:
        assessment_id = _identifier(assessment_id, "assessment_id")
    if point_id is not None:
        point_id = _identifier(point_id, "point_id")
    operation_id = _operation(operation_id)
    reason = _text(reason, "reason", required=True)
    data = _payload(action, data)
    creating = action == "assessment.draft_created"
    if type(expected_edit_version) is not int or not 0 <= expected_edit_version <= 9223372036854775807:
        raise ValidationError({"expected_edit_version": "Use the explicit current integer version (0 for initial creation)."})
    if creating and expected_edit_version != 0:
        raise ValidationError({"expected_edit_version": "An initial draft requires expected version 0."})
    if not creating and expected_edit_version < 1:
        raise ValidationError({"expected_edit_version": "An existing draft requires a positive version."})
    request = {"schema_version": 1, "action": action, "actor_user_id": actor.pk, "session_id": session_id,
               "assessment_id": assessment_id, "point_id": point_id, "expected_edit_version": expected_edit_version,
               "data": data, "reason": reason}
    digest = hashlib.sha256(json.dumps(request, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    with transaction.atomic(using=using):
        session = lock_session_reference(session_id, using)
        lock_participation_references([session.participation_id], using)
        assessment = None
        points = []
        if not creating:
            assessment = Assessment.objects.using(using).select_for_update().filter(pk=assessment_id, session_id=session.pk).first()
            if assessment is None:
                raise ValidationError("Draft is unavailable in this session.")
            points = list(ShotPoint.objects.using(using).select_for_update().filter(assessment_id=assessment.pk).order_by("pk"))
        # User locks come after domain locks; existing User writes take no domain locks.
        current_actor = authorize_draft(actor=actor, action=action, session_id=session.pk, using=using, lock=True)
        replay = _replay(operation_id, digest, action, current_actor, session.pk, using)
        if replay is not None:
            return replay  # Historical command receipt, not current aggregate state.
        if creating:
            if Assessment.objects.using(using).filter(session_id=session.pk).exists():
                raise ValidationError("The session already has an assessment; official revisions are not supported here.")
            before = _assessment_snapshot(None, [])
            assessment = Assessment(session=session, revision_number=1, created_by=current_actor)
            _apply_evidence(assessment, data, using)
            assessment.save(using=using)
            version_before = None
        else:
            if assessment.status != Assessment.Status.DRAFT:
                raise ValidationError("Only draft assessments can be changed.")
            check_version(assessment, expected_edit_version)
            latest = Assessment.objects.using(using).latest_working(session_id=session.pk)
            if latest.pk != assessment.pk:
                raise ValidationError("Only the latest draft revision can be changed.")
            if not same_json(context_for(session, using), assessment.historical_context):
                raise ValidationError("Historical session context has drifted.")
            assessment.validate_evidence(using)
            target = next((point for point in points if point.pk == point_id), None)
            if action in ("shotpoint.updated", "shotpoint.deleted") and target is None:
                raise ValidationError("Point is unavailable in this assessment.")
            before = _assessment_snapshot(assessment, points)
            version_before = assessment.edit_version
            _invalidate(assessment, points, using)
            if action == "assessment.draft_updated":
                _apply_evidence(assessment, data, using)
                assessment.save(using=using, expected_edit_version=version_before)
            elif action == "shotpoint.added":
                number = max((point.point_number for point in points), default=0) + 1
                target = ShotPoint(assessment=assessment, point_number=number, source=ShotPoint.Source.MANUAL, **data)
                target.save(using=using, expected_edit_version=version_before)
                point_id = target.pk
            elif action == "shotpoint.updated":
                for field, value in data.items():
                    setattr(target, field, value)
                target.save(using=using, expected_edit_version=version_before)
            else:
                target.delete(using=using, expected_edit_version=version_before)
        # Snapshot the persisted representation, including enum/decimal normalization.
        assessment.refresh_from_db(using=using)
        after_points = list(ShotPoint.objects.using(using).filter(assessment_id=assessment.pk).order_by("pk"))
        after = _assessment_snapshot(assessment, after_points)
        after["command_sha256"] = digest
        after["command_result"] = {"assessment_id": assessment.pk, "edit_version": assessment.edit_version, "point_id": point_id}
        event = _append_audit(
            using=using, operation_id=operation_id, actor_kind=AuditEvent.ActorKind.USER, actor_user=current_actor,
            session=session, assessment=assessment, action=action, reason=reason,
            before_state=before, after_state=after, version_before=version_before, version_after=assessment.edit_version,
        )
        return _receipt(event)


def create_initial_draft(*, actor, session_id, operation_id, expected_edit_version, data, reason, using=None):
    return _execute("assessment.draft_created", actor=actor, session_id=session_id, operation_id=operation_id,
                    expected_edit_version=expected_edit_version, data=data, reason=reason, using=using)


def update_draft(*, actor, session_id, assessment_id, operation_id, expected_edit_version, data, reason, using=None):
    return _execute("assessment.draft_updated", actor=actor, session_id=session_id, assessment_id=assessment_id,
                    operation_id=operation_id, expected_edit_version=expected_edit_version, data=data, reason=reason, using=using)


def add_shot_point(*, actor, session_id, assessment_id, operation_id, expected_edit_version, data, reason, using=None):
    return _execute("shotpoint.added", actor=actor, session_id=session_id, assessment_id=assessment_id,
                    operation_id=operation_id, expected_edit_version=expected_edit_version, data=data, reason=reason, using=using)


def update_shot_point(*, actor, session_id, assessment_id, point_id, operation_id, expected_edit_version, data, reason, using=None):
    return _execute("shotpoint.updated", actor=actor, session_id=session_id, assessment_id=assessment_id, point_id=point_id,
                    operation_id=operation_id, expected_edit_version=expected_edit_version, data=data, reason=reason, using=using)


def delete_shot_point(*, actor, session_id, assessment_id, point_id, operation_id, expected_edit_version, reason, using=None):
    return _execute("shotpoint.deleted", actor=actor, session_id=session_id, assessment_id=assessment_id, point_id=point_id,
                    operation_id=operation_id, expected_edit_version=expected_edit_version, data={}, reason=reason, using=using)
