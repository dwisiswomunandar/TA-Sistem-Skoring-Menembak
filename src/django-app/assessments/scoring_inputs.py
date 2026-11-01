"""Read-only adapter for fully loaded model snapshots; no queries or writes.

This is not authentication, freshness validation or an official scoring workflow.
A future audited writer must load/lock the current aggregate and recheck versions.
"""

from activities.models import TrainingSession
from .models import Assessment, Calibration, ScoringRuleVersion, ShotPoint, TargetPhoto, TargetRing, TargetVersion
from .scoring import PointInput, RingInput, RuleInput, ScoringError, ScoringInput


def _loaded(instance, model):
    if (not isinstance(instance, model) or instance._state.adding or instance._state.db is None
            or type(instance.pk) is not int or instance.pk < 1 or instance.get_deferred_fields()):
        raise ScoringError(f"A complete persisted {model.__name__} snapshot is required.")


def scoring_input_from_models(*, assessment, session, photo, target, rings, rule, calibration,
                              points, coordinate_contract):
    """Explicitly acknowledge baseline v1.1's target-centred mm contract.

    The accepted calibration supplies every translation/scale/orientation; photo
    dimensions never imply an origin. Caliber comes from the supplied session.
    Existing score fields are ignored. Passing loaded objects does not prove that
    they are still current or that the caller may access them.
    """
    if type(rings) not in (list, tuple) or type(points) not in (list, tuple):
        raise ScoringError("Supply materialized ring and point snapshots, not lazy querysets.")
    rings, points = tuple(rings), tuple(points)
    for instance, model in ((assessment, Assessment), (session, TrainingSession), (photo, TargetPhoto),
            (target, TargetVersion), (rule, ScoringRuleVersion), (calibration, Calibration),
            *((ring, TargetRing) for ring in rings), *((point, ShotPoint) for point in points)):
        _loaded(instance, model)
        if instance._state.db != assessment._state.db:
            raise ScoringError("All snapshots must belong to the same database.")
    if (assessment.session_id != session.pk or photo.session_id != session.pk
            or assessment.photo_id != photo.pk or assessment.calibration_id != calibration.pk
            or assessment.scoring_rule_version_id != rule.pk or calibration.photo_id != photo.pk
            or calibration.target_version_id != target.pk
            or any(ring.target_version_id != target.pk for ring in rings)
            or any(point.assessment_id != assessment.pk for point in points)):
        raise ScoringError("Scoring evidence and point contexts do not match.")
    if (calibration.status != "accepted" or target.status not in ("published", "retired")
            or rule.status not in ("published", "retired")):
        raise ScoringError("Accepted calibration and previously published configurations are required.")
    matrix = calibration.transform_matrix
    if (not isinstance(matrix, list) or len(matrix) != 3
            or any(not isinstance(row, list) or len(row) != 3 for row in matrix)):
        raise ScoringError("An explicit calibration matrix is required.")
    return ScoringInput(photo.analysis_width_px, photo.analysis_height_px, target.width_mm, target.height_mm,
        coordinate_contract, tuple(tuple(row) for row in matrix), session.caliber_mm,
        tuple(RingInput(ring.ring_number, ring.outer_radius_mm, ring.score) for ring in rings),
        tuple(PointInput(point.point_number, point.x_px, point.y_px, point.is_included) for point in points),
        RuleInput(rule.method, rule.algorithm_version, rule.boundary_policy, rule.numeric_precision))
