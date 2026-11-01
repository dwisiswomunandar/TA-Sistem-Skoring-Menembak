"""Synthetic images/checkpoint bytes and mocked inference; no GPU or real weights."""

import asyncio
import hashlib
import json
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi.testclient import TestClient
from PIL import Image
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

from detector_runtime import Settings
from main_api import UploadBoundary, create_app
from test_detection_contract import Boxes, result


class SyntheticNetwork:
    pass


def image_bytes(format="PNG", *, orientation=None, animated=False):
    stream = BytesIO()
    image = Image.new("RGB", (30, 20), (255, 0, 0))
    options = {}
    if orientation is not None:
        exif = Image.Exif()
        exif[274] = orientation
        options["exif"] = exif
    if animated:
        options.update(save_all=True, append_images=[Image.new("RGB", (30, 20), "blue")], duration=100)
    image.save(stream, format=format, **options)
    return stream.getvalue()


class DetectionApiTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory(prefix="synthetic-ai-contract-"))
        self.path = Path(directory) / "selected-artifact.pt"
        self.path.write_bytes(b"synthetic checkpoint bytes; not an executable model")
        self.settings = Settings(weights_path=self.path)
        self.model = SimpleNamespace(task="detect", model=SyntheticNetwork(), ckpt={"version": "synthetic-fixture"},
            predict=Mock(return_value=[result(Boxes([[1.125, 2.25, 8.875, 9.5]], [0.875], [0.0]))]))
        self.factory = Mock(return_value=self.model)

    def client(self, **settings):
        app = create_app(replace(self.settings, **settings), model_factory=self.factory)
        return self.enterContext(TestClient(app))

    def upload(self, client, data=None, mime="image/png"):
        return client.post("/api/evaluate", files={"file": ("untrusted-name.bin", image_bytes() if data is None else data, mime)})

    def test_success_uses_configured_artifact_and_returns_real_contract_metadata(self):
        contents = image_bytes()
        response = self.upload(self.client(), contents)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "succeeded")
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["detections"][0]["bbox_xyxy"], [1.125, 2.25, 8.875, 9.5])
        self.assertEqual(payload["weights_sha256"], hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertEqual(payload["input_sha256"], hashlib.sha256(contents).hexdigest())
        self.assertEqual(payload["model_name"], "ultralytics.SyntheticNetwork")
        self.assertNotIn("YOLO26", json.dumps(payload))
        self.factory.assert_called_once_with(self.path)
        args = self.model.predict.call_args.kwargs
        self.assertEqual(args["source"].shape, (20, 30, 3))
        self.assertEqual(args["source"][0, 0].tolist(), [0, 0, 255])
        self.assertEqual(payload["parameters"], {key: value for key, value in args.items() if key != "source"})
        self.assertFalse(args["save"])
        self.assertEqual(args["device"], "cpu")
        json.dumps(payload, allow_nan=False)

    def test_zero_detection_success_differs_from_missing_output_failure(self):
        client = self.client()
        self.model.predict.return_value = [result()]
        payload = self.upload(client).json()
        self.assertEqual((payload["status"], payload["detections"]), ("succeeded", []))
        self.model.predict.return_value = []
        with self.assertLogs("main_api", level="WARNING"):
            response = self.upload(client)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"status": "failed", "failure_code": "INVALID_DETECTION_RESULT"})

    def test_no_configured_or_missing_weights_never_return_success_or_download(self):
        client = self.client(weights_path=None)
        self.assertEqual(self.upload(client).json(), {"status": "failed", "failure_code": "MODEL_UNAVAILABLE"})
        self.factory.assert_not_called()
        with self.assertLogs("main_api", level="ERROR"):
            other = self.client(weights_path=self.path.parent / "absent-model.pt")
        self.assertEqual(self.upload(other).status_code, 503)
        self.factory.assert_not_called()
        self.assertNotIn("ultralytics", sys.modules)

    def test_model_load_error_is_sanitized_and_does_not_infer(self):
        self.factory.side_effect = RuntimeError("private path and internal startup details")
        with self.assertLogs("main_api", level="ERROR"):
            client = self.client()
        response = self.upload(client)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private", response.text)
        self.model.predict.assert_not_called()

    def test_wrong_model_task_and_changed_weights_fail_closed(self):
        self.model.task = "classify"
        with self.assertLogs("main_api", level="ERROR"):
            client = self.client()
        self.assertEqual(self.upload(client).status_code, 503)
        self.model.task = "detect"
        def mutate(path):
            path.write_bytes(b"changed synthetic checkpoint")
            return self.model
        self.factory.side_effect = mutate
        with self.assertLogs("main_api", level="ERROR"):
            other = self.client()
        self.assertEqual(self.upload(other).status_code, 503)

    def test_empty_oversized_and_non_checkpoint_weights_are_rejected(self):
        for data, limit in ((b"", 100), (b"too-large", 1)):
            self.path.write_bytes(data)
            with self.assertLogs("main_api", level="ERROR"):
                client = self.client(max_weights_bytes=limit)
            self.assertEqual(self.upload(client).status_code, 503)
        yaml = self.path.with_suffix(".yaml")
        yaml.write_text("synthetic architecture definition")
        with self.assertLogs("main_api", level="ERROR"):
            client = self.client(weights_path=yaml)
        self.assertEqual(self.upload(client).status_code, 503)
        self.factory.assert_not_called()

    def test_inference_error_is_not_a_successful_empty_result_or_information_leak(self):
        self.model.predict.side_effect = RuntimeError("sensitive internal inference details")
        client = self.client()
        with self.assertLogs("main_api", level="ERROR"):
            response = self.upload(client)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["failure_code"], "INFERENCE_FAILED")
        self.assertNotIn("sensitive", response.text)
        self.model.predict.side_effect = None
        self.assertEqual(self.upload(client).status_code, 200)  # Lock was released after failure.

    def test_bad_boxes_cannot_be_silently_dropped_or_clipped(self):
        self.model.predict.return_value = [result(Boxes([[0, 0, 31, 20]], [0.8], [0]))]
        client = self.client()
        with self.assertLogs("main_api", level="WARNING"):
            response = self.upload(client)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["failure_code"], "INVALID_DETECTION_RESULT")

    def test_invalid_empty_and_truncated_images_reject_before_inference(self):
        client = self.client()
        for data in (b"", b"not an image", image_bytes()[:25]):
            with self.subTest(size=len(data)):
                response = self.upload(client, data)
                self.assertEqual(response.status_code, 400)
        self.model.predict.assert_not_called()

    def test_mime_mismatch_unsupported_format_and_animation_are_rejected(self):
        client = self.client()
        cases = ((image_bytes(), "image/jpeg"), (image_bytes("GIF"), "image/gif"),
                 (image_bytes(animated=True), "image/png"))
        for data, mime in cases:
            with self.subTest(mime=mime):
                self.assertEqual(self.upload(client, data, mime).status_code, 415)
        self.model.predict.assert_not_called()

    def test_encoded_size_dimensions_pixels_and_decompression_bombs_are_bounded(self):
        for settings in ({"max_upload_bytes": 20}, {"max_side": 10}, {"max_pixels": 100}):
            with self.subTest(settings=settings):
                self.assertEqual(self.upload(self.client(**settings)).status_code, 413)
        with patch.object(Image, "MAX_IMAGE_PIXELS", 1):
            self.assertEqual(self.upload(self.client()).status_code, 413)
        self.model.predict.assert_not_called()

    def test_jpeg_exif_does_not_rotate_the_analysis_coordinate_space(self):
        response = self.upload(self.client(), image_bytes("JPEG", orientation=6), "image/jpeg")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual((payload["analysis_width_px"], payload["analysis_height_px"]), (30, 20))
        self.assertEqual(self.model.predict.call_args.kwargs["source"].shape, (20, 30, 3))
        self.assertEqual(payload["preprocessing_version"], "decoded-rgb-to-bgr-no-exif-v1")

    def test_wrong_content_type_missing_extra_and_duplicate_parts_are_rejected(self):
        client = self.client()
        self.assertEqual(client.post("/api/evaluate", json={"file": "untrusted"}).status_code, 415)
        self.assertEqual(client.post("/api/evaluate", files={"wrong": ("x.png", image_bytes(), "image/png")}).status_code, 400)
        files = [("file", ("x.png", image_bytes(), "image/png")), ("file", ("y.png", image_bytes(), "image/png"))]
        self.assertEqual(client.post("/api/evaluate", files=files).status_code, 400)
        self.assertEqual(client.post("/api/evaluate", files={"file": ("x.png", image_bytes(), "image/png")},
            data={"weights_path": "untrusted.pt"}).status_code, 400)
        self.model.predict.assert_not_called()

    def test_malformed_multipart_is_a_safe_client_error(self):
        response = self.client().post("/api/evaluate", content=b"not multipart",
            headers={"content-type": "multipart/form-data; boundary=synthetic"})
        self.assertEqual(response.status_code, 400)
        self.model.predict.assert_not_called()

    def test_busy_native_detector_rejects_without_parallel_inference(self):
        client = self.client()
        runtime = client.app.state.detector
        runtime._inference_lock.acquire()
        try:
            response = self.upload(client)
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["failure_code"], "DETECTOR_BUSY")
            self.model.predict.assert_not_called()
        finally:
            runtime._inference_lock.release()

    def test_environment_config_is_explicit_and_invalid_limits_fail_closed(self):
        with patch.dict(os.environ, {"AI_WEIGHTS_PATH": str(self.path), "AI_CONFIDENCE": "0.35", "AI_IMAGE_SIZE": "320"}, clear=True):
            settings = Settings.from_environment()
        self.assertEqual((settings.weights_path, settings.confidence, settings.image_size), (self.path, 0.35, 320))
        for changes in ({"weights_path": Path("relative.pt")}, {"max_pixels": 0}, {"max_inflight": 1000},
                        {"confidence": float("nan")}, {"iou": True}, {"image_size": 63}, {"device": "arbitrary-device"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(self.settings, **changes)


class UploadBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def scope(self, length=None):
        return {"type": "http", "path": "/api/evaluate", "method": "POST",
                "headers": [] if length is None else [(b"content-length", length)]}

    async def test_declared_oversize_rejects_before_reading_body(self):
        downstream, receive, send = AsyncMock(), AsyncMock(), AsyncMock()
        boundary = UploadBoundary(downstream, Settings(max_upload_bytes=10))
        await boundary(self.scope(b"999999999999999999999999"), receive, send)
        self.assertEqual(send.call_args_list[0].args[0]["status"], 413)
        receive.assert_not_called()
        downstream.assert_not_called()

    async def test_mount_prefix_does_not_bypass_the_body_limit(self):
        downstream, receive, send = AsyncMock(), AsyncMock(), AsyncMock()
        boundary = UploadBoundary(downstream, Settings(max_upload_bytes=10))
        scope = {**self.scope(b"999999"), "path": "/internal/api/evaluate", "root_path": "/internal"}
        await boundary(scope, receive, send)
        self.assertEqual(send.call_args_list[0].args[0]["status"], 413)
        receive.assert_not_called()
        downstream.assert_not_called()

    async def test_streamed_size_limit_works_without_or_with_false_content_length(self):
        async def downstream(scope, receive, send):
            while (await receive()).get("more_body", False):
                pass
        boundary = UploadBoundary(downstream, Settings(max_upload_bytes=10))
        for length in (None, b"1"):
            receive = AsyncMock(side_effect=[{"type": "http.request", "body": b"x" * 65500, "more_body": True},
                                            {"type": "http.request", "body": b"x" * 100, "more_body": False}])
            with self.subTest(length=length), self.assertRaises(HTTPException) as error:
                await boundary(self.scope(length), receive, AsyncMock())
            self.assertEqual(error.exception.status_code, 413)

    async def test_inflight_gate_rejects_excess_requests_without_queueing(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def downstream(scope, receive, send):
            started.set()
            await release.wait()
            await JSONResponse({"ok": True})(scope, receive, send)
        boundary = UploadBoundary(downstream, Settings())
        first = asyncio.create_task(boundary(self.scope(), AsyncMock(), AsyncMock()))
        await started.wait()
        send = AsyncMock()
        await boundary(self.scope(), AsyncMock(), send)
        self.assertEqual(send.call_args_list[0].args[0]["status"], 503)
        release.set()
        await first

    async def test_upload_deadline_releases_the_slot(self):
        async def downstream(scope, receive, send):
            await receive()
        async def slow_receive():
            await asyncio.sleep(2)
            return {"type": "http.request", "body": b"", "more_body": False}
        boundary = UploadBoundary(downstream, Settings(upload_timeout_s=1))
        with self.assertRaises(HTTPException) as error:
            await boundary(self.scope(), slow_receive, AsyncMock())
        self.assertEqual(error.exception.status_code, 408)
        self.assertTrue(boundary.slots.acquire(blocking=False))
        boundary.slots.release()
