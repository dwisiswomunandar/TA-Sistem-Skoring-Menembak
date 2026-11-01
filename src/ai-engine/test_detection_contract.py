"""Synthetic Ultralytics-shaped results; no torch, GPU or weights imports."""

import unittest
from types import SimpleNamespace

from detection_contract import DetectionContractError, detection_result


class Values:
    def __init__(self, values):
        self.values = values

    def tolist(self):
        return self.values


class Boxes:
    def __init__(self, xyxy=(), conf=(), cls=()):
        self.xyxy, self.conf, self.cls = Values(list(xyxy)), Values(list(conf)), Values(list(cls))

    def __len__(self):
        return len(self.xyxy.values)

    def cpu(self):
        return self


def result(boxes=None, shape=(20, 30)):
    return SimpleNamespace(orig_shape=shape, boxes=boxes if boxes is not None else Boxes())


class DetectionContractTests(unittest.TestCase):
    def convert(self, boxes, **changes):
        return detection_result([result(boxes)], **{**dict(width=30, height=20, max_detections=10), **changes})

    def test_native_boxes_confidence_and_classes_preserve_coordinates_and_unique_ids(self):
        boxes = Boxes([[1.125, 2.25, 8.875, 9.5], [0, 0, 30, 20]], [0.75, 1.0], [0.0, 2.0])
        payload = self.convert(boxes)
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["detections"][0], {"id": "d000001", "class_id": 0,
            "confidence": 0.75, "bbox_xyxy": [1.125, 2.25, 8.875, 9.5]})
        self.assertEqual(payload["detections"][1]["id"], "d000002")
        self.assertEqual(payload["detections"][1]["class_id"], 2)

    def test_empty_boxes_are_successful_zero_detections(self):
        self.assertEqual(self.convert(Boxes()), {"schema_version": 1, "detections": []})

    def test_missing_or_multiple_results_are_not_zero_detections(self):
        for results in (None, [], [result(), result()], [SimpleNamespace()], [SimpleNamespace(orig_shape=(20, 30), boxes=None)]):
            with self.subTest(results=results), self.assertRaises(DetectionContractError):
                detection_result(results, width=30, height=20, max_detections=10)

    def test_invalid_numbers_boxes_and_classes_fail_instead_of_clipping_or_filtering(self):
        for coordinates, confidence, class_id in (([0, 0, 31, 20], 0.8, 0), ([1, 1, 1, 2], 0.8, 0),
                ([-1, 0, 10, 10], 0.8, 0), ([0, 0, float("nan"), 10], 0.8, 0),
                ([True, 0, 10, 10], 0.8, 0), ([0, 0, 10, 10], float("inf"), 0),
                ([0, 0, 10, 10], 1.1, 0), ([0, 0, 10, 10], 0.8, 1.5),
                ([0, 0, 10, 10], 0.8, -1), ([0, 0, 10, 10], 0.8, True)):
            with self.subTest(coordinates=coordinates, confidence=confidence, class_id=class_id), self.assertRaises(DetectionContractError):
                self.convert(Boxes([coordinates], [confidence], [class_id]))

    def test_analysis_shape_and_tensor_lengths_must_match(self):
        with self.assertRaises(DetectionContractError):
            self.convert(Boxes([[0, 0, 10, 10]], [], [0]))
        with self.assertRaises(DetectionContractError):
            detection_result([result(shape=(30, 20))], width=30, height=20, max_detections=10)

    def test_resource_limits_and_dimensions_are_strict(self):
        with self.assertRaises(DetectionContractError):
            self.convert(Boxes([[0, 0, 1, 1]] * 2, [1] * 2, [0] * 2), max_detections=1)
        for changes in ({"width": True}, {"height": 0}, {"max_detections": -1}):
            with self.subTest(changes=changes), self.assertRaises(DetectionContractError):
                self.convert(Boxes(), **changes)
