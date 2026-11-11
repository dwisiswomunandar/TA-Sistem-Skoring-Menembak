"""Measured-reference calibration commands; field profiles are unconfigured by default."""

import hashlib
import json

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import models, router, transaction

from activities.authorization import authorize_draft
from activities.history import lock_participation_references, lock_session_reference
from . import calibration_math as geometry, scoring
from .models import Assessment, Calibration, DetectionRun, TargetPhoto, TargetRing, TargetVersion
from .photo_metadata import inspect_original
from .result_models import check_version, context_for
from .result_validators import same_json
from .services import _append_audit, _assessment_snapshot, _identifier, _invalidate, _operation, _receipt, _replay, _text
from .validators import validate_sha256, validate_transform_matrix

ACTION = "assessment.calibrated"
AUTHORIZATION_ACTION = "assessment.draft_updated"
PROFILE_KEYS = {"id", "target_version_id", "width_mm", "height_mm", "measurement_reference",
                "coordinate_contract", "fit_landmarks_mm", "check_landmarks_mm", "max_error_mm",
                "min_area_ratio", "min_denominator_ratio"}


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _label(value):
    if type(value) is not str or not value.strip() or value != value.strip() or len(value) > 128:
        raise ValueError("Use an explicit bounded landmark/profile identifier.")
    return value


def load_profile(profile_id):
    """Read/copy a protected measured reference and its fingerprint; no DB access.

    No real reference is supplied by this iteration. Deployment profiles must be
    independently measured/reviewed and versioned, not learned from client input.
    """
    try:
        _label(profile_id)
        rows = getattr(settings, "TARGET_CALIBRATION_PROFILES", ())
        if type(rows) not in (list, tuple) or any(type(row) is not dict for row in rows):
            raise ValueError
        ids = [_label(row["id"]) for row in rows]
        if len(set(ids)) != len(ids):
            raise ValueError
        raw = next(row for row in rows if row["id"] == profile_id)
        if set(raw) != PROFILE_KEYS or raw["coordinate_contract"] != scoring.COORDINATE_CONTRACT:
            raise ValueError
        profile = {"id": profile_id, "target_version_id": _identifier(raw["target_version_id"], "target_version_id"),
            "coordinate_contract": raw["coordinate_contract"],
            "measurement_reference": _text(raw["measurement_reference"], "measurement_reference", required=True)}
        for name in ("width_mm", "height_mm", "max_error_mm", "min_area_ratio", "min_denominator_ratio"):
            profile[name] = geometry.number_text(raw[name])
            if geometry.number(profile[name]) <= 0:
                raise ValueError
        if any(geometry.number(profile[name]) >= 1 for name in ("min_area_ratio", "min_denominator_ratio")):
            raise ValueError
        identifiers = []
        for name in ("fit_landmarks_mm", "check_landmarks_mm"):
            landmarks = raw[name]
            if type(landmarks) not in (list, tuple) or (len(landmarks) != 4 if name.startswith("fit") else not 1 <= len(landmarks) <= 16):
                raise ValueError
            profile[name] = []
            for row in landmarks:
                if type(row) is not dict or set(row) != {"id", "x_mm", "y_mm"}:
                    raise ValueError
                key = _label(row["id"])
                x, y = geometry.number_text(row["x_mm"]), geometry.number_text(row["y_mm"])
                if abs(geometry.number(x)) > geometry.number(profile["width_mm"]) / 2 or abs(geometry.number(y)) > geometry.number(profile["height_mm"]) / 2:
                    raise ValueError
                identifiers.append(key)
                profile[name].append({"id": key, "x_mm": x, "y_mm": y})
        if len(set(identifiers)) != len(identifiers):
            raise ValueError
        return profile, _digest(profile)
    except (KeyError, TypeError, ValueError, StopIteration, ValidationError):
        raise ImproperlyConfigured("Configure an approved measured target profile and explicit quality limits.") from None


def _observations(value):
    if type(value) is not dict or not 5 <= len(value) <= 20:
        raise ValidationError("Supply all four fit and independent check observations.")
    result = {}
    try:
        for key, pair in value.items():
            _label(key)
            if type(pair) not in (list, tuple) or len(pair) != 2:
                raise ValueError
            result[key] = [geometry.number_text(number) for number in pair]
    except (ValueError, TypeError):
        raise ValidationError("Observations must contain only labelled finite analysis-pixel pairs.") from None
    return result


def calibrate_draft(*, actor, session_id, assessment_id, photo_id, target_version_id,
                    expected_edit_version, expected_target_edit_version, profile_id,
                    expected_profile_sha256, observations, operation_id, reason, using=None):
    """Create/accept a new calibration and bind it to the current draft atomically.

    Existing points stay in pixels. Replacing calibration invalidates scores only;
    use score_draft separately with the returned/current evidence versions.
    """
    using = using or router.db_for_write(Assessment)
    authorize_draft(actor=actor, action=AUTHORIZATION_ACTION, session_id=session_id, using=using)
    values = {name: _identifier(value, name) for name, value in (
        ("session_id", session_id), ("assessment_id", assessment_id), ("photo_id", photo_id),
        ("target_version_id", target_version_id), ("expected_edit_version", expected_edit_version),
        ("expected_target_edit_version", expected_target_edit_version))}
    profile_id = _text(profile_id, "profile_id", required=True)
    if type(expected_profile_sha256) is not str or not expected_profile_sha256:
        raise ValidationError("An explicit measured-profile fingerprint is required.")
    validate_sha256(expected_profile_sha256)
    observations = _observations(observations)
    operation_id, reason = _operation(operation_id), _text(reason, "reason", required=True)
    request = {"schema_version": 1, "action": ACTION, "actor_user_id": actor.pk, **values,
        "profile_id": profile_id, "expected_profile_sha256": expected_profile_sha256,
        "observations": observations, "reason": reason}
    digest = _digest(request)
    with transaction.atomic(using=using):
        session = lock_session_reference(session_id, using)
        lock_participation_references([session.participation_id], using)
        assessment = Assessment.objects.using(using).select_for_update().filter(pk=assessment_id, session_id=session.pk).first()
        if assessment is None:
            raise ValidationError("Draft is unavailable in this session.")
        points = list(assessment.shot_points.using(using).select_for_update().order_by("pk"))
        photo = TargetPhoto.objects.using(using).select_for_update().filter(pk=photo_id, session_id=session.pk).first()
        run = DetectionRun.objects.using(using).select_for_update().filter(pk=assessment.detection_run_id,
            photo_id=photo_id).first() if assessment.detection_run_id is not None else None
        previous = Calibration.objects.using(using).select_for_update().filter(pk=assessment.calibration_id,
            photo_id=photo_id).first() if assessment.calibration_id is not None else None
        target = TargetVersion.objects.using(using).select_for_update().filter(pk=target_version_id).first()
        rings = list(TargetRing.objects.using(using).select_for_update().filter(target_version_id=target_version_id).order_by("pk"))
        current = authorize_draft(actor=actor, action=AUTHORIZATION_ACTION, session_id=session.pk, using=using, lock=True)
        replay = _replay(operation_id, digest, ACTION, current, session.pk, using)
        if replay is not None:
            return replay
        if assessment.status != "draft" or Assessment.objects.using(using).latest_working(session_id=session.pk).pk != assessment.pk:
            raise ValidationError("Only the latest draft can be calibrated.")
        check_version(assessment, expected_edit_version)
        if not same_json(context_for(session, using), assessment.historical_context):
            raise ValidationError("Historical session context has drifted.")
        if photo is None or assessment.photo_id != photo.pk or (assessment.detection_run_id is not None and run is None) or (assessment.calibration_id is not None and previous is None):
            raise ValidationError("Photo, existing evidence and draft must belong to the same session.")
        if target is None or target.status != "published" or target.edit_version != expected_target_edit_version:
            raise ValidationError("A current published target version is required.")
        assessment.validate_evidence(using)
        for instance in (session, assessment, photo, target, *rings, *points):
            instance.full_clean()
        if run is not None:
            run.full_clean()
        if previous is not None:
            previous.full_clean()
        profile, profile_hash = load_profile(profile_id)
        if profile_hash != expected_profile_sha256 or profile["target_version_id"] != target.pk:
            raise ValidationError("Measured reference is changed or belongs to another target.")
        if any(geometry.number(profile[name]) != geometry.number(getattr(target, name)) for name in ("width_mm", "height_mm")):
            raise ValidationError("Measured reference dimensions do not match the target version.")
        landmarks = profile["fit_landmarks_mm"] + profile["check_landmarks_mm"]
        if set(observations) != {row["id"] for row in landmarks}:
            raise ValidationError("Supply exactly the approved fit and independent check landmark IDs.")
        pairs = [[*observations[row["id"]], row["x_mm"], row["y_mm"]] for row in landmarks]
        try:
            matrix, quality = geometry.fit_transform(width_px=photo.analysis_width_px, height_px=photo.analysis_height_px,
                fit_pairs=pairs[:4], check_pairs=pairs[4:], max_error_mm=profile["max_error_mm"],
                min_area_ratio=profile["min_area_ratio"], min_denominator_ratio=profile["min_denominator_ratio"])
        except geometry.CalibrationError as error:
            raise ValidationError({"calibration": str(error)}) from error
        validate_transform_matrix(matrix)
        verified = inspect_original(photo.storage_key)
        if any(getattr(photo, name) != value for name, value in verified.items()):
            raise ValidationError("Original photo bytes no longer match their stored identity.")
        before = _assessment_snapshot(assessment, points)
        version_before = assessment.edit_version
        calibration = Calibration(photo=photo, target_version=target, transform_matrix=matrix,
            method_version=geometry.METHOD_VERSION, created_by=current)
        calibration.save(using=using)
        calibration.status, calibration.accepted_by = "accepted", current
        calibration.save(using=using, expected_edit_version=1)
        _invalidate(assessment, points, using)
        assessment.calibration = calibration
        assessment.edit_version += 1
        assessment.full_clean()
        # Bounded audited replacement in the SAME pixel frame, preserving photo,
        # run, rule and point identities. Public evidence-switch guards stay closed.
        models.Model.save(assessment, using=using, update_fields=("calibration", "edit_version", "total_score",
            "scoring_context", "scoring_engine_version", "scoring_input_sha256", "scored_at", "scored_edit_version", "updated_at"))
        assessment.refresh_from_db(using=using)
        after = _assessment_snapshot(assessment, list(assessment.shot_points.using(using).order_by("pk")))
        after.update(command_sha256=digest, command_result={"assessment_id": assessment.pk,
            "edit_version": assessment.edit_version, "point_id": None})
        after["calibration_result"] = {"id": calibration.pk, "edit_version": calibration.edit_version,
            "previous_calibration_id": previous.pk if previous else None, "method_version": geometry.METHOD_VERSION,
            "target_version_id": target.pk, "target_edit_version": target.edit_version,
            "photo_id": photo.pk, "photo_sha256": photo.sha256, "preprocessing_version": photo.preprocessing_version,
            "analysis_width_px": photo.analysis_width_px, "analysis_height_px": photo.analysis_height_px,
            "coordinate_contract": scoring.COORDINATE_CONTRACT, "profile_sha256": profile_hash,
            "measured_profile": profile, "observations_px": observations, "transform_matrix": matrix,
            "quality": quality, "lifecycle": ["draft", "accepted"]}
        event = _append_audit(using=using, operation_id=operation_id, actor_kind="user", actor_user=current,
            session=session, assessment=assessment, action=ACTION, reason=reason,
            before_state=before, after_state=after, version_before=version_before, version_after=assessment.edit_version)
        return _receipt(event)
