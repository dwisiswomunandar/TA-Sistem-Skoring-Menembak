"""Iteration 04A storage contracts; neither authentication nor scoring is executed."""

from decimal import Decimal, InvalidOperation
from uuid import UUID

from django.core.exceptions import ValidationError
from django.utils.dateparse import parse_datetime
from django.utils.timezone import is_aware

from .validators import validate_strict_json


def validate_snapshot(value):
    validate_strict_json(value)
    if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise ValidationError("Use an explicit JSON object with schema_version 1.")
    # Secrets have no place in audit or historical snapshots, including nested payloads.
    def inspect(item):
        if isinstance(item, dict):
            for key, child in item.items():
                normalized = key.lower().replace("-", "_")
                if normalized in {
                    "password", "password_hash", "token", "access_token", "refresh_token",
                    "pin", "secret", "secret_hash", "cookie", "authorization", "session_key",
                }:
                    raise ValidationError("Snapshots must not contain authentication secrets.")
                inspect(child)
        elif isinstance(item, list):
            for child in item:
                inspect(child)
    inspect(value)


def validate_historical_context(value):
    validate_snapshot(value)
    keys = {
        "schema_version", "session_id", "participation_id", "activity_id", "participant_id",
        "session_number", "session_edit_version", "weapon_type", "shooting_position",
        "distance_m", "caliber_mm",
    }
    if set(value) != keys:
        raise ValidationError("Historical context must contain the complete server session snapshot.")
    for key in (
        "session_id", "participation_id", "activity_id", "participant_id", "session_number",
        "session_edit_version",
    ):
        if type(value[key]) is not int or value[key] < 1:
            raise ValidationError("Historical identifiers and versions must be positive integers.")
    for key in ("weapon_type", "shooting_position"):
        if not isinstance(value[key], str) or not value[key].strip():
            raise ValidationError("Historical weapon and position must be explicit.")
    for key in ("distance_m", "caliber_mm"):
        try:
            number = Decimal(value[key]) if isinstance(value[key], str) else None
        except (InvalidOperation, ValueError) as error:
            raise ValidationError("Historical measurements must be finite positive decimal strings.") from error
        if number is None or not number.is_finite() or number <= 0:
            raise ValidationError("Historical measurements must be finite positive decimal strings.")


def same_json(first, second):
    import json
    validate_strict_json(first)
    validate_strict_json(second)
    return json.dumps(first, sort_keys=True, allow_nan=False) == json.dumps(second, sort_keys=True, allow_nan=False)


def validate_authentication_context(value):
    """Validate shape only. No claimed authentication context is trusted by save()."""
    validate_snapshot(value)
    required = {"schema_version", "participation_id", "method", "authenticated_at", "authentication_id"}
    if not required <= set(value) or set(value) - required - {"credential_generation"}:
        raise ValidationError("Supply the explicit nonsecret participant authentication context.")
    if type(value["participation_id"]) is not int or value["participation_id"] < 1:
        raise ValidationError("Principal participation must be a positive integer.")
    if not isinstance(value["method"], str) or not value["method"].strip():
        raise ValidationError("An explicit authentication method is required.")
    if "credential_generation" in value and (type(value["credential_generation"]) is not int or value["credential_generation"] < 1):
        raise ValidationError("Credential generation must be positive.")
    try:
        timestamp = parse_datetime(value["authenticated_at"]) if isinstance(value["authenticated_at"], str) else None
        UUID(value["authentication_id"])
    except (TypeError, ValueError, AttributeError) as error:
        raise ValidationError("Use a nonsecret authentication UUID and an aware timestamp.") from error
    if timestamp is None or not is_aware(timestamp):
        raise ValidationError("Authentication timestamps must include a timezone.")
