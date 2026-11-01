"""Pure concentric-circle scoring: no ORM, IO, AI, clock or global Decimal context.

Coordinates are analysis pixels -> target millimetres, with the ring centre at
(0, 0), as specified in database baseline v1.1 section 3.4. No photo midpoint,
target offset, line thickness or physical tolerance is inferred.
"""

from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from math import isfinite

ENGINE_VERSION = "inward-circle-v1"
BOUNDARY_POLICY = "touch-inclusive-v1"
NUMERIC_PRECISION = "exact-rational-decimal-v1"
COORDINATE_CONTRACT = "analysis-px-to-target-mm-center-origin-v1"
Number = int | float | Decimal


class ScoringError(ValueError):
    """Incomplete, invalid or unsupported scoring input; never a zero-score fallback."""


@dataclass(frozen=True)
class RingInput:
    ring_number: int
    outer_radius_mm: Number
    score: int


@dataclass(frozen=True)
class PointInput:
    point_number: int
    x_px: Number
    y_px: Number
    is_included: bool


@dataclass(frozen=True)
class RuleInput:
    method: str
    algorithm_version: str
    boundary_policy: str
    numeric_precision: str


@dataclass(frozen=True)
class ScoringInput:
    image_width_px: int
    image_height_px: int
    target_width_mm: Number
    target_height_mm: Number
    coordinate_contract: str
    transform_matrix: tuple[tuple[Number, ...], ...]
    caliber_mm: Number
    rings: tuple[RingInput, ...]
    points: tuple[PointInput, ...]
    rule: RuleInput


@dataclass(frozen=True)
class PointResult:
    point_number: int
    x_mm: Fraction
    y_mm: Fraction
    ring_number: int | None
    score: int | None


@dataclass(frozen=True)
class ScoringResult:
    engine_version: str
    points: tuple[PointResult, ...]
    total_score: int


def _integer(value, label):
    if type(value) is not int or not 0 < value <= 9223372036854775807:
        raise ScoringError(f"{label} must be a positive integer.")
    return value


def _number(value, label, *, positive=False):
    if type(value) is int:
        if value.bit_length() > 4096:
            raise ScoringError(f"{label} exceeds the numeric input limit.")
        result = Fraction(value)
    elif type(value) in (Decimal, float):
        if not (value.is_finite() if type(value) is Decimal else isfinite(value)):
            raise ScoringError(f"{label} must be finite.")
        # Interpret a JSON float's shortest round-trip decimal representation;
        # never use its binary approximation in geometric arithmetic.
        decimal = value if type(value) is Decimal else Decimal(str(value))
        parts = decimal.as_tuple()
        if len(parts.digits) > 1000 or abs(parts.exponent) > 1000:
            raise ScoringError(f"{label} exceeds the numeric input limit.")
        result = Fraction(decimal)
    else:
        raise ScoringError(f"{label} must be a number, not a boolean or numeric string.")
    if positive and result <= 0:
        raise ScoringError(f"{label} must be positive.")
    return result


def _matrix(value):
    if (type(value) is not tuple or len(value) != 3
            or any(type(row) is not tuple or len(row) != 3 for row in value)):
        raise ScoringError("Use an explicit 3x3 homogeneous transform.")
    a, b, c = matrix = tuple(tuple(_number(v, "matrix entry") for v in row) for row in value)
    determinant = (a[0] * (b[1] * c[2] - b[2] * c[1])
                   - a[1] * (b[0] * c[2] - b[2] * c[0])
                   + a[2] * (b[0] * c[1] - b[1] * c[0]))
    if determinant == 0:
        raise ScoringError("Calibration transform is singular.")
    return matrix


def score_target(data: ScoringInput) -> ScoringResult:
    """Return computed values only; excluded points remain NULL, included misses 0.

    An inward hit satisfies x_mm**2 + y_mm**2 <= (ring_radius + caliber/2)**2.
    Exact rational comparison avoids sqrt, rounding, epsilon and Decimal context.
    The physical field bounds ring geometry; it does not clip the bullet disk.
    """
    if not isinstance(data, ScoringInput) or not isinstance(data.rule, RuleInput):
        raise ScoringError("Use explicit scoring and rule inputs.")
    if data.rule != RuleInput("inward", ENGINE_VERSION, BOUNDARY_POLICY, NUMERIC_PRECISION):
        raise ScoringError("Unsupported scoring algorithm, boundary or numeric contract.")
    if data.coordinate_contract != COORDINATE_CONTRACT:
        raise ScoringError("An explicit supported centre-origin coordinate contract is required.")
    width = _integer(data.image_width_px, "image width")
    height = _integer(data.image_height_px, "image height")
    field_radius = min(_number(data.target_width_mm, "target width", positive=True),
                       _number(data.target_height_mm, "target height", positive=True)) / 2
    hole_radius = _number(data.caliber_mm, "caliber", positive=True) / 2
    matrix = _matrix(data.transform_matrix)
    if (type(data.rings) is not tuple or not data.rings
            or any(not isinstance(ring, RingInput) for ring in data.rings)):
        raise ScoringError("At least one configured ring is required.")
    for ring in data.rings:
        _integer(ring.ring_number, "ring number")
        _integer(ring.score, "ring score")
    rings = []
    for ring in sorted(data.rings, key=lambda item: item.ring_number):
        radius = _number(ring.outer_radius_mm, "ring radius", positive=True)
        if radius > field_radius or (rings and (ring.ring_number == rings[-1][0].ring_number
                or radius <= rings[-1][1] or ring.score > rings[-1][0].score)):
            raise ScoringError("Ring numbering, radii, score order or physical fit is invalid.")
        rings.append((ring, radius))
    if type(data.points) is not tuple or any(not isinstance(point, PointInput) for point in data.points):
        raise ScoringError("Use explicit point inputs.")
    seen = set()
    results = []
    for point in data.points:
        number = _integer(point.point_number, "point number")
        if number in seen or type(point.is_included) is not bool:
            raise ScoringError("Point numbers must be unique and inclusion must be boolean.")
        seen.add(number)
        x, y = _number(point.x_px, "x_px"), _number(point.y_px, "y_px")
        if not (0 <= x < width and 0 <= y < height):
            raise ScoringError("Point is outside the analysis image.")
        u, v, w = (row[0] * x + row[1] * y + row[2] for row in matrix)
        if w == 0:
            raise ScoringError("Point maps to projective infinity.")
        x_mm, y_mm = u / w, v / w
        matched = next((ring for ring, radius in rings
                        if x_mm * x_mm + y_mm * y_mm <= (radius + hole_radius) ** 2), None)
        score = (matched.score if matched else 0) if point.is_included else None
        results.append(PointResult(number, x_mm, y_mm,
                                   matched.ring_number if matched and point.is_included else None, score))
    return ScoringResult(ENGINE_VERSION, tuple(sorted(results, key=lambda item: item.point_number)),
                         sum(point.score for point in results if point.score is not None))
