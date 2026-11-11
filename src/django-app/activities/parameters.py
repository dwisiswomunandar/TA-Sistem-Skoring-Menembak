"""PROVISIONAL DEC-2026-11-02-01; application policy, not a field standard.

No ORM or writes. Omitted derived values are resolved by the server; supplied
conflicting values are rejected. Model/form field validation still owns storage
precision. Never apply this resolver to rewrite unchanged historical records.
"""

from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError


_CALIBERS = {"laras_panjang": Decimal("5.56"), "pistol": Decimal("9.00")}
POSITIONS = ("berdiri", "duduk", "tiarap")
PISTOL_DISTANCES = (Decimal("15.00"), Decimal("20.00"), Decimal("25.00"))
PARAMETER_FIELDS = ("weapon_type", "shooting_position", "distance_m", "caliber_mm")


def caliber_for_weapon(weapon_type):
    return _CALIBERS.get(weapon_type) if isinstance(weapon_type, str) else None


def _number(value, field):
    if value is None or (isinstance(value, str) and value == ""):
        return None
    if type(value) not in (str, int, float, Decimal):
        raise ValidationError({field: "Supply a finite numeric parameter."})
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValidationError({field: "Supply a finite numeric parameter."}) from None
    if not number.is_finite():
        raise ValidationError({field: "Supply a finite numeric parameter."})
    return number


def resolve_parameters(*, weapon_type=None, shooting_position=None, distance_m=None, caliber_mm=None):
    """Resolve all four fields from the selected weapon and its required input.

    None/empty means absent; only derived values may be absent. Explicit values
    must agree with the policy. No implicit weapon, long-gun stance or pistol
    distance is selected. Returned Decimals use the server's provisional values.
    """
    caliber = caliber_for_weapon(weapon_type)
    if caliber is None:
        raise ValidationError({"weapon_type": "Select laras_panjang or pistol."})
    distance = _number(distance_m, "distance_m")
    supplied_caliber = _number(caliber_mm, "caliber_mm")
    errors = {}
    if supplied_caliber is not None and supplied_caliber != caliber:
        errors["caliber_mm"] = "Caliber conflicts with the provisional weapon policy."
    if weapon_type == "laras_panjang":
        if shooting_position not in POSITIONS:
            errors["shooting_position"] = "Select berdiri, duduk or tiarap for laras panjang."
        if distance is not None and distance != Decimal("100.00"):
            errors["distance_m"] = "Laras panjang requires the server distance of 100 m."
        distance = Decimal("100.00")
        position = shooting_position
    else:
        if shooting_position not in (None, "", "berdiri"):
            errors["shooting_position"] = "Pistol requires the server position berdiri."
        if distance not in PISTOL_DISTANCES:
            errors["distance_m"] = "Select 15, 20 or 25 m for pistol."
        position = "berdiri"
    if errors:
        raise ValidationError(errors)
    return {"weapon_type": weapon_type, "shooting_position": position,
            "distance_m": distance, "caliber_mm": caliber}
