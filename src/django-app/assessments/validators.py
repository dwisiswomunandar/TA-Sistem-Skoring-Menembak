"""Storage contracts only; these validators do not run AI or calibration."""

import json
import math
import re
from fractions import Fraction

from django.core.exceptions import ValidationError


def validate_sha256(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValidationError("Use a lowercase, 64-character SHA-256 hexadecimal digest.")


def validate_private_key(value):
    if (
        not isinstance(value, str)
        or re.fullmatch(r"originals/[A-Za-z0-9._/-]+", value) is None
        or any(part in ("", ".", "..") for part in value.split("/"))
    ):
        raise ValidationError("Use a relative originals/ key without URLs or traversal segments.")


def validate_strict_json(value):
    """Reject non-JSON Python values and nonfinite numbers, including nested ones."""
    def inspect(item):
        if item is None or type(item) in (str, bool, int):
            return
        if type(item) is float and math.isfinite(item):
            return
        if isinstance(item, list):
            for child in item:
                inspect(child)
            return
        if isinstance(item, dict) and all(isinstance(key, str) for key in item):
            for child in item.values():
                inspect(child)
            return
        raise ValueError

    try:
        inspect(value)
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError, OverflowError, RecursionError) as error:
        raise ValidationError("Use finite, standard JSON values with string object keys.") from error


def validate_parameters(value):
    validate_strict_json(value)
    if not isinstance(value, dict):
        raise ValidationError("Inference parameters must be an explicit JSON object.")


def finite_number(value):
    return type(value) is int or (type(value) is float and math.isfinite(value))


def validate_transform_matrix(value):
    """3x3 homogeneous analysis-pixel -> target-mm transform, no epsilon policy."""
    if (
        not isinstance(value, list) or len(value) != 3
        or any(not isinstance(row, list) or len(row) != 3 for row in value)
        or any(not finite_number(number) for row in value for number in row)
    ):
        raise ValidationError("Use a 3x3 JSON array of finite numbers (booleans are invalid).")
    a, b, c = ([Fraction(number) for number in row] for row in value)
    determinant = (
        a[0] * (b[1] * c[2] - b[2] * c[1])
        - a[1] * (b[0] * c[2] - b[2] * c[0])
        + a[2] * (b[0] * c[1] - b[1] * c[0])
    )
    if determinant == 0:
        raise ValidationError("The transform matrix must be invertible.")


def validate_detection_result(value, *, width, height):
    validate_strict_json(value)
    if (
        not isinstance(value, dict) or type(value.get("schema_version")) is not int
        or value["schema_version"] != 1 or not isinstance(value.get("detections"), list)
    ):
        raise ValidationError("Use schema_version 1 and an explicit detections array.")
    identifiers = set()
    for detection in value["detections"]:
        if not isinstance(detection, dict):
            raise ValidationError("Each detection must be a JSON object.")
        identifier = detection.get("id")
        class_id = detection.get("class_id")
        confidence = detection.get("confidence")
        box = detection.get("bbox_xyxy")
        if not isinstance(identifier, str) or not identifier.strip() or identifier in identifiers:
            raise ValidationError("Detection IDs must be nonblank unique strings within a run.")
        identifiers.add(identifier)
        if type(class_id) is not int or class_id < 0:
            raise ValidationError("class_id must be a nonnegative integer.")
        if not finite_number(confidence) or not 0 <= confidence <= 1:
            raise ValidationError("confidence must be finite and between zero and one.")
        if (
            not isinstance(box, list) or len(box) != 4
            or any(not finite_number(number) for number in box)
        ):
            raise ValidationError("bbox_xyxy must contain four finite numbers.")
        x1, y1, x2, y2 = box
        if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
            raise ValidationError("Bounding boxes must have positive area within the analysis image.")
