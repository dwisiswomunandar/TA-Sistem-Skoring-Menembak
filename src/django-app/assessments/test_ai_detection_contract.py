"""Verify the actual AI adapter with Django's validator; no HTTP bridge/AI imports."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from django.test import SimpleTestCase
from .validators import validate_detection_result

path = Path(__file__).resolve().parents[2] / "ai-engine" / "detection_contract.py"
spec = importlib.util.spec_from_file_location("ai_contract_under_test", path)
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


class DetectionAdapterCompatibilityTests(SimpleTestCase):
    def payload(self, rows, confidences, classes):
        class Boxes:
            def __len__(self):
                return len(rows)
        boxes = Boxes()
        boxes.xyxy = Mock(tolist=lambda: rows)
        boxes.conf = Mock(tolist=lambda: confidences)
        boxes.cls = Mock(tolist=lambda: classes)
        boxes.cpu = lambda: boxes
        return adapter.detection_result([SimpleNamespace(orig_shape=(20, 30), boxes=boxes)],
                                       width=30, height=20, max_detections=300)

    def test_actual_adapter_result_passes_django_validator(self):
        payload = self.payload([[1.125, 2.25, 8.875, 9.5], [0, 0, 30, 20]], [0.75, 1.0], [0.0, 2.0])
        validate_detection_result(payload, width=30, height=20)
        self.assertEqual(len({row["id"] for row in payload["detections"]}), 2)
        self.assertEqual(payload["detections"][0]["class_id"], 0)

    def test_zero_detections_passes_django_validator(self):
        payload = self.payload([], [], [])
        validate_detection_result(payload, width=30, height=20)
        self.assertEqual(payload, {"schema_version": 1, "detections": []})

    def test_response_metadata_is_compatible_without_changing_raw_detection_contract(self):
        payload = self.payload([[0, 0, 10, 10]], [0.8], [0])
        payload.update(status="succeeded", analysis_width_px=30, analysis_height_px=20,
            preprocessing_version="decoded-rgb-to-bgr-no-exif-v1", model_name="synthetic-fixture",
            weights_sha256="a" * 64, runtime_version="synthetic-runtime", adapter_version=adapter.ADAPTER_VERSION,
            parameters={"conf": 0.2, "device": "cpu", "save": False})
        validate_detection_result(payload, width=30, height=20)
