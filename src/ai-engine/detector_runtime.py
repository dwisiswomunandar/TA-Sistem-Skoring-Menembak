"""Local configured detector and bounded image decoding; no scoring or calibration."""

import hashlib
import math
import os
import threading
import warnings
from dataclasses import dataclass
from importlib.metadata import version
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image, UnidentifiedImageError

from detection_contract import ADAPTER_VERSION, detection_result

PREPROCESSING_VERSION = "decoded-rgb-to-bgr-no-exif-v1"


class ImageRejected(ValueError):
    def __init__(self, code, status=400):
        self.code, self.status = code, status


class DetectorBusy(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    weights_path: Path | None = None
    confidence: float = 0.2
    iou: float = 0.7
    image_size: int = 640
    max_detections: int = 300
    device: str = "cpu"
    max_upload_bytes: int = 8 * 1024 * 1024
    max_pixels: int = 16_000_000
    max_side: int = 8192
    max_inflight: int = 1
    upload_timeout_s: int = 15
    max_weights_bytes: int = 1024 * 1024 * 1024

    def __post_init__(self):
        for name, upper in (("image_size", 1280), ("max_detections", 1000),
                ("max_upload_bytes", 32 * 1024 * 1024), ("max_pixels", 32_000_000),
                ("max_side", 16384), ("max_inflight", 4), ("upload_timeout_s", 120),
                ("max_weights_bytes", 4 * 1024 * 1024 * 1024)):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= upper:
                raise ValueError(f"Invalid detector setting: {name}")
        if self.image_size < 32 or self.image_size % 32:
            raise ValueError("Inference image size must be a multiple of 32.")
        for value in (self.confidence, self.iou):
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError("Confidence and IoU must be finite values in [0, 1].")
        if self.device != "cpu" and not (isinstance(self.device, str) and self.device.isascii() and self.device.isdigit()):
            raise ValueError("Select cpu or one explicit GPU index.")
        if self.weights_path is not None and (not isinstance(self.weights_path, Path) or not self.weights_path.is_absolute()):
            raise ValueError("Configure an absolute local weights path.")

    @classmethod
    def from_environment(cls):
        values = {}
        for name in cls.__dataclass_fields__:
            raw = os.getenv("AI_" + name.upper())
            if raw is not None:
                values[name] = (Path(raw) if name == "weights_path" else raw if name == "device"
                                else float(raw) if name in ("confidence", "iou") else int(raw))
        return cls(**values)

    @property
    def parameters(self):
        return {"conf": self.confidence, "iou": self.iou, "imgsz": self.image_size,
                "max_det": self.max_detections, "device": self.device, "half": False,
                "augment": False, "rect": False, "batch": 1, "stream": False,
                "save": False, "verbose": False}


def load_model(path):
    # Lazy import: test factories never load torch, a GPU or real weights.
    from ultralytics import YOLO
    return YOLO(str(path))


def weights_digest(path, maximum):
    if not path.is_file() or not 0 < path.stat().st_size <= maximum:
        raise ValueError("Weights must be a bounded nonempty local file.")
    digest, count = hashlib.sha256(), 0
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            count += len(block)
            if count > maximum:
                raise ValueError("Weights file exceeds limit.")
            digest.update(block)
    return digest.hexdigest()


def decode_image(contents, mime_type, settings):
    if not contents:
        raise ImageRejected("INVALID_IMAGE")
    if len(contents) > settings.max_upload_bytes:
        raise ImageRejected("UPLOAD_TOO_LARGE", 413)
    allowed = {"JPEG": "image/jpeg", "PNG": "image/png"}
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(contents)) as image:
                if image.format not in allowed or allowed[image.format] != mime_type:
                    raise ImageRejected("UNSUPPORTED_IMAGE", 415)
                width, height = image.size
                if max(width, height) > settings.max_side or width * height > settings.max_pixels:
                    raise ImageRejected("IMAGE_TOO_LARGE", 413)
                if getattr(image, "n_frames", 1) != 1:
                    raise ImageRejected("ANIMATED_IMAGE", 415)
                image.verify()
            with Image.open(BytesIO(contents)) as image:
                # No EXIF transpose, resize or placeholder perspective transform.
                return np.asarray(image.convert("RGB"))[:, :, ::-1].copy()
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise ImageRejected("IMAGE_TOO_LARGE", 413) from error
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as error:
        if isinstance(error, ImageRejected):
            raise
        raise ImageRejected("INVALID_IMAGE") from error


class DetectorRuntime:
    def __init__(self, settings, model_factory=load_model):
        self.settings, self.model_factory = settings, model_factory
        self.model = None
        self.metadata = {}
        self._inference_lock = threading.Lock()

    def load(self):
        self.model, self.metadata = None, {}
        path = self.settings.weights_path
        if path is None:
            return
        if path.suffix.lower() != ".pt":
            raise ValueError("Provision a trusted local .pt checkpoint, not an untrained model definition.")
        digest = weights_digest(path, self.settings.max_weights_bytes)
        model = self.model_factory(path)
        if model.task != "detect":
            raise ValueError("Only axis-aligned object detection models are supported.")
        if weights_digest(path, self.settings.max_weights_bytes) != digest:
            raise ValueError("Weights changed while loading.")
        checkpoint = getattr(model, "ckpt", None)
        reported_version = checkpoint.get("version") if isinstance(checkpoint, dict) else None
        self.metadata = {"model_name": f"ultralytics.{type(model.model).__name__}",
            "weights_sha256": digest, "runtime_version": f"ultralytics/{version('ultralytics')}",
            "adapter_version": ADAPTER_VERSION,
            "checkpoint_runtime_version": reported_version if type(reported_version) is str else None}
        self.model = model

    def evaluate(self, contents, mime_type):
        if not self._inference_lock.acquire(blocking=False):
            raise DetectorBusy()
        try:
            image = decode_image(contents, mime_type, self.settings)
            height, width = image.shape[:2]
            results = self.model.predict(source=image, **self.settings.parameters)
            payload = detection_result(results, width=width, height=height, max_detections=self.settings.max_detections)
            payload.update(status="succeeded", analysis_width_px=width, analysis_height_px=height,
                preprocessing_version=PREPROCESSING_VERSION, input_sha256=hashlib.sha256(contents).hexdigest(),
                parameters=self.settings.parameters, **self.metadata)
            return payload
        finally:
            self._inference_lock.release()
