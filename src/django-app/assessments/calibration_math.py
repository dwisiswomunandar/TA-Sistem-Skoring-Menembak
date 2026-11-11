"""Deterministic planar landmark calibration; no ORM, IO or physical defaults."""

from decimal import Decimal, InvalidOperation
from fractions import Fraction
from functools import reduce
from itertools import combinations
from math import gcd, lcm

METHOD_VERSION = "measured-landmarks-projective-integer-v1"


class CalibrationError(ValueError):
    pass


def number_text(value):
    if type(value) not in (str, int, float, Decimal):
        raise CalibrationError("Use finite numeric coordinates, not booleans.")
    if len(str(value)) > 80:
        raise CalibrationError("Numeric input exceeds the technical size limit.")
    try:
        value = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise CalibrationError("Use finite numeric coordinates.") from None
    if not value.is_finite() or len(value.as_tuple().digits) > 32 or abs(value.as_tuple().exponent) > 32:
        raise CalibrationError("Numeric input is nonfinite or exceeds the technical precision limit.")
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if value.is_zero() else text


def number(value):
    return Fraction(number_text(value))


def ratio(value):
    return {"numerator": str(value.numerator), "denominator": str(value.denominator)}


def _cross(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _quad(points, minimum):
    turns = [_cross(points[i], points[(i + 1) % 4], points[(i + 2) % 4]) for i in range(4)]
    if not (all(value > 0 for value in turns) or all(value < 0 for value in turns)):
        raise CalibrationError("Fit landmarks must form an ordered strictly convex quadrilateral.")
    diameter_sq = max(sum((a[i] - b[i]) ** 2 for i in (0, 1)) for a, b in combinations(points, 2))
    separation = min(abs(_cross(a, b, c)) for a, b, c in combinations(points, 3)) / diameter_sq
    if separation < minimum:
        raise CalibrationError("Landmark geometry is too close to collinear under the approved policy.")
    return separation


def _inside(point, quad):
    sides = [_cross(quad[i], quad[(i + 1) % 4], point) for i in range(4)]
    return all(value >= 0 for value in sides) or all(value <= 0 for value in sides)


def _matrix(source, destination):
    rows = []
    for (x, y), (u, v) in zip(source, destination):
        rows += [[x, y, 1, 0, 0, 0, -u * x, -u * y, -u],
                 [0, 0, 0, x, y, 1, -v * x, -v * y, -v]]
    rows = [[Fraction(value) for value in row] for row in rows]
    pivots = []
    for column in range(9):
        pivot = next((index for index in range(len(pivots), 8) if rows[index][column]), None)
        if pivot is None:
            continue
        index = len(pivots)
        rows[index], rows[pivot] = rows[pivot], rows[index]
        scale = rows[index][column]
        rows[index] = [value / scale for value in rows[index]]
        for other in range(8):
            if other != index:
                scale = rows[other][column]
                rows[other] = [a - scale * b for a, b in zip(rows[other], rows[index])]
        pivots.append(column)
        if len(pivots) == 8:
            break
    if len(pivots) != 8:
        raise CalibrationError("Landmarks do not determine a unique projective transform.")
    free = next(column for column in range(9) if column not in pivots)
    solution = [Fraction(0) for _ in range(9)]
    solution[free] = Fraction(1)
    for row, column in enumerate(pivots):
        solution[column] = -rows[row][free]
    common = lcm(*(value.denominator for value in solution))
    integers = [int(value * common) for value in solution]
    divisor = reduce(gcd, integers)
    sign = 1 if next(value for value in integers if value) > 0 else -1
    integers = [value // divisor * sign for value in integers]
    if any(value.bit_length() > 4096 for value in integers):
        raise CalibrationError("Exact matrix exceeds the existing scoring numeric limit.")
    a, b, c = matrix = [integers[i:i + 3] for i in (0, 3, 6)]
    determinant = a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0]) + a[2] * (b[0] * c[1] - b[1] * c[0])
    if determinant == 0:
        raise CalibrationError("The fitted transform is singular.")
    return matrix


def _project(matrix, point):
    x, y, w = [row[0] * point[0] + row[1] * point[1] + row[2] for row in matrix]
    if w == 0:
        raise CalibrationError("A landmark maps to projective infinity.")
    return x / w, y / w


def fit_transform(*, width_px, height_px, fit_pairs, check_pairs, max_error_mm,
                  min_area_ratio, min_denominator_ratio):
    """Fit four measured correspondences and verify independent check landmarks.

    Each pair is [x_px, y_px, x_mm, y_mm]. Physical coordinates must already be
    relative to the measured ring origin. This function never infers paper centre.
    Quality thresholds are explicit approved inputs, never physical defaults.
    """
    if any(type(value) is not int or value <= 0 for value in (width_px, height_px)):
        raise CalibrationError("Explicit positive image dimensions are required.")
    if type(fit_pairs) not in (list, tuple) or len(fit_pairs) != 4:
        raise CalibrationError("Exactly four fit correspondences are required.")
    if type(check_pairs) not in (list, tuple) or not 1 <= len(check_pairs) <= 16:
        raise CalibrationError("Supply 1-16 independent check landmarks.")
    rows = []
    for pair in (*fit_pairs, *check_pairs):
        if type(pair) not in (list, tuple) or len(pair) != 4:
            raise CalibrationError("Each correspondence requires pixel and measured millimetre coordinates.")
        row = tuple(number(value) for value in pair)
        if not (0 <= row[0] < width_px and 0 <= row[1] < height_px):
            raise CalibrationError("Landmarks must lie inside the analysis raster.")
        rows.append(row)
    source, physical = [row[:2] for row in rows], [row[2:] for row in rows]
    if len(set(source)) != len(rows) or len(set(physical)) != len(rows):
        raise CalibrationError("Fit and check landmarks must be distinct independent correspondences.")
    error, area, denominator_ratio = map(number, (max_error_mm, min_area_ratio, min_denominator_ratio))
    if error <= 0 or not 0 < area < 1 or not 0 < denominator_ratio < 1:
        raise CalibrationError("Explicit positive error and dimensionless quality ratios are required.")
    source_ratio, physical_ratio = _quad(source[:4], area), _quad(physical[:4], area)
    if any(not _inside(p, source[:4]) for p in source[4:]) or any(not _inside(p, physical[:4]) for p in physical[4:]):
        raise CalibrationError("Independent checks must lie within the fit landmark hull.")
    matrix = _matrix(source[:4], physical[:4])
    denominators = [matrix[2][0] * x + matrix[2][1] * y + matrix[2][2]
                    for x, y in ((0, 0), (width_px, 0), (width_px, height_px), (0, height_px))]
    if not (all(value > 0 for value in denominators) or all(value < 0 for value in denominators)):
        raise CalibrationError("The projective horizon intersects the analysis raster.")
    margin = Fraction(min(map(abs, denominators)), max(map(abs, denominators)))
    if margin < denominator_ratio:
        raise CalibrationError("The transform is too close to its projective horizon.")
    errors = []
    for point, expected in zip(source, physical):
        actual = _project(matrix, point)
        squared = sum((actual[i] - expected[i]) ** 2 for i in (0, 1))
        if squared > error ** 2:
            raise CalibrationError("Independent check or fit residual exceeds the approved millimetre limit.")
        errors.append(squared)
    return matrix, {"schema_version": 1, "max_fit_error_squared_mm2": ratio(max(errors[:4])),
        "max_check_error_squared_mm2": ratio(max(errors[4:])),
        "check_errors_squared_mm2": [ratio(value) for value in errors[4:]],
        "source_min_triangle_ratio": ratio(source_ratio), "physical_min_triangle_ratio": ratio(physical_ratio),
        "denominator_margin_ratio": ratio(margin)}
