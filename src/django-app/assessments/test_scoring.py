"""Synthetic geometry, not certified target dimensions or measured accuracy."""

from dataclasses import replace
from decimal import Decimal, ROUND_DOWN, ROUND_UP, localcontext
from fractions import Fraction

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from assessments.models import Assessment, Calibration, ScoringRuleVersion, ShotPoint, TargetRing
from assessments.scoring import (
    BOUNDARY_POLICY, COORDINATE_CONTRACT, ENGINE_VERSION, NUMERIC_PRECISION,
    PointInput, RingInput, RuleInput, ScoringError, ScoringInput, score_target,
)
from assessments.scoring_inputs import scoring_input_from_models
from assessments.test_results import ResultTestCase


class PureScoringTests(SimpleTestCase):
    def data(self, **changes):
        return replace(ScoringInput(1000, 1000, Decimal("100"), Decimal("100"), COORDINATE_CONTRACT,
            ((1, 0, -100), (0, 1, -100), (0, 0, 1)), Decimal("9"),
            (RingInput(1, Decimal("10"), 10), RingInput(2, Decimal("30"), 8)),
            (PointInput(1, Decimal("100"), Decimal("100"), True),),
            RuleInput("inward", ENGINE_VERSION, BOUNDARY_POLICY, NUMERIC_PRECISION)), **changes)

    def point(self, x, y=100, included=True):
        return PointInput(1, x, y, included)

    def test_center_uses_calibration_not_image_midpoint(self):
        result = score_target(self.data())
        self.assertEqual((result.points[0].x_mm, result.points[0].y_mm), (0, 0))
        self.assertEqual(result.points[0].score, 10)
        self.assertEqual(result.points[0].ring_number, 1)
        self.assertEqual(result.total_score, 10)

    def test_inner_boundary_and_neighboring_values(self):
        for x, expected in (("114.499999", 10), ("114.5", 10), ("114.500001", 8)):
            with self.subTest(x=x):
                self.assertEqual(score_target(self.data(points=(self.point(Decimal(x)),))).total_score, expected)

    def test_outer_boundary_and_miss_are_distinct(self):
        for x, expected in (("134.5", 8), ("134.500001", 0), ("900", 0)):
            with self.subTest(x=x):
                result = score_target(self.data(points=(self.point(Decimal(x)),)))
                self.assertEqual(result.total_score, expected)
                self.assertEqual(result.points[0].score, expected)

    def test_diagonal_exact_tangency_without_square_root(self):
        data = self.data(caliber_mm=10, rings=(RingInput(1, 5, 10),), points=(self.point(106, 108),))
        self.assertEqual(score_target(data).total_score, 10)  # 6**2 + 8**2 == (5+5)**2
        self.assertEqual(score_target(replace(data, points=(self.point(Decimal("106.000001"), 108),))).total_score, 0)

    def test_caliber_is_diameter_and_changes_inward_contact(self):
        point = (self.point(114),)
        self.assertEqual(score_target(self.data(points=point, caliber_mm=8)).total_score, 10)
        self.assertEqual(score_target(self.data(points=point, caliber_mm=Decimal("7.999998"))).total_score, 8)

    def test_touching_multiple_rings_selects_highest_inner_score(self):
        data = self.data(caliber_mm=80, points=(self.point(140),))
        self.assertEqual(score_target(data).points[0].ring_number, 1)

    def test_excluded_points_are_null_and_not_added_to_total(self):
        data = self.data(points=(PointInput(2, 140, 100, True), self.point(100, included=False)))
        result = score_target(data)
        self.assertIsNone(result.points[0].score)
        self.assertIsNone(result.points[0].ring_number)
        self.assertEqual(result.points[1].score, 0)
        self.assertEqual(result.total_score, 0)
        self.assertEqual(score_target(self.data(points=())).total_score, 0)

    def test_projective_transform_and_homogeneous_scaling(self):
        matrix = ((1, 0, -100), (0, 1, -100), (0.01, 0, 1))
        data = self.data(transform_matrix=matrix, points=(self.point(120),))
        result = score_target(data)
        self.assertEqual(result.points[0].x_mm, Fraction(100, 11))
        # Exact integer homogeneous scaling gives the same geometric map.
        integer_matrix = ((100, 0, -10000), (0, 100, -10000), (1, 0, 100))
        self.assertEqual(score_target(replace(data, transform_matrix=integer_matrix)), result)
        negative_matrix = tuple(tuple(-v for v in row) for row in integer_matrix)
        self.assertEqual(score_target(replace(data, transform_matrix=negative_matrix)), result)

    def test_json_decimal_float_boundary_is_exact(self):
        data = self.data(transform_matrix=((0.1, 0, 0), (0, 0.1, -10), (0, 0, 1)),
                         points=(self.point(145),))
        self.assertEqual(score_target(data).points[0].x_mm, Fraction(29, 2))
        self.assertEqual(score_target(data).total_score, 10)

    def test_result_is_independent_of_decimal_context_and_input_order(self):
        data = self.data(points=(PointInput(2, Decimal("114.5"), 100, True), self.point(100)),
                         rings=(RingInput(7, 30, 8), RingInput(2, 10, 10)))
        expected = score_target(data)
        for precision, rounding in ((2, ROUND_DOWN), (50, ROUND_UP)):
            with localcontext() as context:
                context.prec, context.rounding = precision, rounding
                self.assertEqual(score_target(data), expected)
        reordered = replace(data, points=tuple(reversed(data.points)), rings=tuple(reversed(data.rings)))
        self.assertEqual(score_target(reordered), expected)

    def test_equal_adjacent_scores_and_gaps_in_ring_number_are_supported(self):
        data = self.data(rings=(RingInput(1, 10, 10), RingInput(4, 30, 10)), points=(self.point(120),))
        self.assertEqual(score_target(data).points[0].ring_number, 4)

    def test_invalid_or_unsupported_rule_and_origin_are_rejected(self):
        for field, value in (("method", "outward"), ("algorithm_version", "synthetic-v1"),
                             ("boundary_policy", "arbitrary formula"), ("numeric_precision", "float64")):
            with self.subTest(field=field), self.assertRaises(ScoringError):
                score_target(self.data(rule=replace(self.data().rule, **{field: value})))
        for contract in (None, "", "photo-midpoint"):
            with self.subTest(contract=contract), self.assertRaises(ScoringError):
                score_target(self.data(coordinate_contract=contract))

    def test_invalid_caliber_and_dimensions_are_rejected(self):
        for field in ("caliber_mm", "target_width_mm", "target_height_mm"):
            for value in (0, -1, True, None, "9", float("nan"), float("inf"), Decimal("sNaN")):
                with self.subTest(field=field, value=repr(value)), self.assertRaises(ScoringError):
                    score_target(self.data(**{field: value}))
        for field in ("image_width_px", "image_height_px"):
            for value in (0, -1, True, 1.5, "100"):
                with self.subTest(field=field, value=value), self.assertRaises(ScoringError):
                    score_target(self.data(**{field: value}))

    def test_invalid_coordinates_and_excluded_coordinates_are_rejected(self):
        for value in (-1, 1000, True, None, "100", float("inf"), Decimal("NaN")):
            for axis in ("x_px", "y_px"):
                with self.subTest(value=repr(value), axis=axis), self.assertRaises(ScoringError):
                    point = replace(self.point(100, included=False), **{axis: value})
                    score_target(self.data(points=(point,)))

    def test_invalid_ring_geometry_and_scores_are_rejected(self):
        cases = ((), (RingInput(1, 0, 1),), (RingInput(1, 51, 1),), (RingInput(1, 10, 0),),
            (RingInput(True, 10, 10),), (RingInput(1, 10, True),), (RingInput(1, True, 10),),
            (RingInput(1, 10, 8), RingInput(2, 20, 10)),
            (RingInput(1, 10, 10), RingInput(2, 10, 8)),
            (RingInput(1, 20, 10), RingInput(2, 10, 8)),
            (RingInput(1, 10, 10), RingInput(1, 20, 8)))
        for rings in cases:
            with self.subTest(rings=rings), self.assertRaises(ScoringError):
                score_target(self.data(rings=rings))

    def test_invalid_matrix_shape_entries_and_singularity_are_rejected(self):
        for matrix in (None, (), ((1, 0), (0, 1)), ((1, 0, 0), (0, 0, 0), (0, 0, 1)),
                       ((True, 0, 0), (0, 1, 0), (0, 0, 1)),
                       ((float("nan"), 0, 0), (0, 1, 0), (0, 0, 1)),
                       (("1", 0, 0), (0, 1, 0), (0, 0, 1))):
            with self.subTest(matrix=matrix), self.assertRaises(ScoringError):
                score_target(self.data(transform_matrix=matrix))

    def test_invertible_projective_matrix_can_still_reject_a_point_at_infinity(self):
        with self.assertRaisesMessage(ScoringError, "projective infinity"):
            score_target(self.data(transform_matrix=((1, 0, 0), (0, 1, 0), (1, 0, -100))))

    def test_duplicate_number_invalid_inclusion_and_non_numeric_types_are_rejected(self):
        cases = ((self.point(100), self.point(101)), (replace(self.point(100), point_number=True),),
                 (replace(self.point(100), is_included=1),), (None,))
        for points in cases:
            with self.subTest(points=points), self.assertRaises(ScoringError):
                score_target(self.data(points=points))
        with self.assertRaises(ScoringError):
            score_target(None)

    def test_unbounded_numeric_inputs_are_rejected_before_expensive_conversion(self):
        for value in (Decimal("1e1000000"), Decimal("1e-1000000"), 1 << 4097):
            with self.subTest(value_type=type(value)), self.assertRaises(ScoringError):
                score_target(self.data(caliber_mm=value))


class ModelScoringInputTests(ResultTestCase):
    def aggregate(self):
        rule = ScoringRuleVersion.objects.create(code="synthetic-engine-rule", version=1, created_by=self.actor,
            algorithm_version=ENGINE_VERSION, boundary_policy=BOUNDARY_POLICY, numeric_precision=NUMERIC_PRECISION).publish()
        assessment = self.complete_draft(scoring_rule_version=rule)
        point = self.make_point(assessment, x_px=Decimal("40"), y_px=Decimal("30"))
        assessment.refresh_from_db()
        return dict(assessment=assessment, session=self.session, photo=assessment.photo,
            target=self.target, rings=list(self.target.rings.all()), rule=rule, calibration=assessment.calibration,
            points=[point], coordinate_contract=COORDINATE_CONTRACT)

    def test_adapter_is_query_free_and_scoring_does_not_persist_or_increment_version(self):
        aggregate = self.aggregate()
        assessment, point = aggregate["assessment"], aggregate["points"][0]
        before = Assessment.objects.filter(pk=assessment.pk).values().get()
        point_before = ShotPoint.objects.filter(pk=point.pk).values().get()
        with self.assertNumQueries(0):
            data = scoring_input_from_models(**aggregate)
            result = score_target(data)
        self.assertEqual(result.total_score, 1)
        self.assertEqual(result.points[0].x_mm, 0)
        self.assertEqual(result.points[0].y_mm, 0)
        self.assertEqual(Assessment.objects.filter(pk=assessment.pk).values().get(), before)
        self.assertEqual(ShotPoint.objects.filter(pk=point.pk).values().get(), point_before)

    def test_adapter_rejects_cross_entity_references(self):
        aggregate = self.aggregate()
        for obj, field in ((aggregate["assessment"], "session_id"), (aggregate["assessment"], "photo_id"),
                (aggregate["assessment"], "calibration_id"), (aggregate["assessment"], "scoring_rule_version_id"),
                (aggregate["calibration"], "photo_id"), (aggregate["calibration"], "target_version_id"),
                (aggregate["rings"][0], "target_version_id"), (aggregate["points"][0], "assessment_id")):
            old = getattr(obj, field)
            setattr(obj, field, old + 10000)
            with self.subTest(field=field), self.assertNumQueries(0), self.assertRaises(ScoringError):
                scoring_input_from_models(**aggregate)
            setattr(obj, field, old)

    def test_adapter_requires_accepted_calibration_and_published_configurations(self):
        aggregate = self.aggregate()
        for obj in (aggregate["target"], aggregate["rule"], aggregate["calibration"]):
            old = obj.status
            obj.status = "draft"
            with self.subTest(model=type(obj).__name__), self.assertRaises(ScoringError):
                scoring_input_from_models(**aggregate)
            obj.status = old
        aggregate["target"].status = aggregate["rule"].status = "retired"
        self.assertEqual(score_target(scoring_input_from_models(**aggregate)).total_score, 1)

    def test_adapter_ignores_supplied_scores_and_preserves_model_write_guards(self):
        aggregate = self.aggregate()
        aggregate["points"][0].score = 999
        aggregate["assessment"].total_score = 999
        self.assertEqual(score_target(scoring_input_from_models(**aggregate)).total_score, 1)
        with self.assertRaises(ValidationError):
            aggregate["points"][0].save(expected_edit_version=aggregate["assessment"].edit_version)
        with self.assertRaises(ValidationError):
            aggregate["assessment"].save(expected_edit_version=aggregate["assessment"].edit_version)

    def test_adapter_rejects_deferred_unsaved_mixed_database_and_missing_matrix(self):
        aggregate = self.aggregate()
        with self.assertRaises(ScoringError):
            scoring_input_from_models(**{**aggregate, "rings": [TargetRing()]})
        deferred = Calibration.objects.only("pk").get(pk=aggregate["calibration"].pk)
        with self.assertNumQueries(0), self.assertRaises(ScoringError):
            scoring_input_from_models(**{**aggregate, "calibration": deferred})
        aggregate["calibration"]._state.db = "different"
        with self.assertRaises(ScoringError):
            scoring_input_from_models(**aggregate)
        aggregate["calibration"]._state.db = "default"
        aggregate["calibration"].transform_matrix = None
        with self.assertRaises(ScoringError):
            scoring_input_from_models(**aggregate)

    def test_adapter_rejects_lazy_querysets_without_evaluating_them(self):
        aggregate = self.aggregate()
        for field, queryset in (("rings", self.target.rings.all()),
                                ("points", aggregate["assessment"].shot_points.all())):
            with self.subTest(field=field), self.assertNumQueries(0), self.assertRaises(ScoringError):
                scoring_input_from_models(**{**aggregate, field: queryset})
