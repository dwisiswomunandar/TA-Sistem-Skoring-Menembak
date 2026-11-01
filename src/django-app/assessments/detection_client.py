"""Bounded private-original snapshot and internal HTTP inference, never ORM writes."""

import hashlib
import ipaddress
import json
import math
import threading
import time
import warnings
from dataclasses import dataclass
from io import BytesIO
from urllib.parse import urlsplit

import httpx
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.files.storage import storages
from PIL import Image, UnidentifiedImageError

from .validators import validate_detection_result, validate_private_key, validate_sha256, validate_strict_json

PREPROCESSING_VERSION = "decoded-rgb-to-bgr-no-exif-v1"
ADAPTER_VERSION = "ultralytics-boxes-v1"
MAX_UPLOAD = 8 * 1024 * 1024
MAX_RESPONSE = 2 * 1024 * 1024
HTTP_SLOTS = threading.BoundedSemaphore(2)  # Per process, includes file verification.
PROVENANCE_FIELDS = {"model_name", "weights_sha256", "runtime_version", "adapter_version", "parameters"}


def canonical(value):
    validate_strict_json(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


@dataclass(frozen=True)
class Configuration:
    endpoint: str
    provenance: dict
    timeouts: dict

    @classmethod
    def current(cls):
        value = getattr(settings, "DETECTION_SERVICE", None)
        try:
            if type(value) is not dict or set(value) != {"endpoint", "provenance", "timeouts"}:
                raise ValueError
            endpoint = value["endpoint"]
            if type(endpoint) is not str or any(c.isspace() for c in endpoint):
                raise ValueError
            url = urlsplit(endpoint)
            port = url.port if url.port is not None else (443 if url.scheme == "https" else 80)
            if (url.scheme not in ("http", "https") or url.path != "/api/evaluate" or url.query or url.fragment
                    or url.username is not None or url.password is not None or "%" in (url.hostname or "")
                    or not 1 <= port <= 65535):
                raise ValueError
            address = ipaddress.ip_address(url.hostname)  # No DNS/proxy/rebinding path.
            networks = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")
            if not (address.is_loopback or any(address in ipaddress.ip_network(n) for n in networks)):
                raise ValueError
            if url.scheme == "http" and not address.is_loopback:
                raise ValueError
            provenance = value["provenance"]
            if type(provenance) is not dict or set(provenance) != PROVENANCE_FIELDS:
                raise ValueError
            for key in ("model_name", "runtime_version", "adapter_version"):
                if type(provenance[key]) is not str or not provenance[key].strip() or len(provenance[key]) > 128:
                    raise ValueError
            validate_sha256(provenance["weights_sha256"])
            if provenance["adapter_version"] != ADAPTER_VERSION:
                raise ValueError
            params = provenance["parameters"]
            fixed = {"half": False, "augment": False, "rect": False, "batch": 1,
                     "stream": False, "save": False, "verbose": False}
            if type(params) is not dict or set(params) != set(fixed) | {"conf", "iou", "imgsz", "max_det", "device"}:
                raise ValueError
            if canonical({key: params[key] for key in fixed}) != canonical(fixed):
                raise ValueError
            for key in ("conf", "iou"):
                if type(params[key]) not in (int, float) or not math.isfinite(params[key]) or not 0 <= params[key] <= 1:
                    raise ValueError
            if type(params["imgsz"]) is not int or not 32 <= params["imgsz"] <= 1280 or params["imgsz"] % 32:
                raise ValueError
            if type(params["max_det"]) is not int or not 1 <= params["max_det"] <= 1000:
                raise ValueError
            device = params["device"]
            if type(device) is not str or not (device == "cpu" or (device.isascii() and device.isdigit())):
                raise ValueError
            timeouts = value["timeouts"]
            if type(timeouts) is not dict or set(timeouts) != {"connect", "read", "write", "pool", "total"}:
                raise ValueError
            for key, upper in (("connect", 10), ("read", 120), ("write", 30), ("pool", 5), ("total", 180)):
                if type(timeouts[key]) not in (int, float) or not math.isfinite(timeouts[key]) or not 0 < timeouts[key] <= upper:
                    raise ValueError
            # Isolate this operation from mutable deployment dictionaries.
            return cls(endpoint, json.loads(canonical(provenance)), {k: float(v) for k, v in timeouts.items()})
        except (TypeError, ValueError, ValidationError, KeyError):
            raise ImproperlyConfigured("Configure an approved internal detection endpoint, provenance and bounded timeouts.") from None

    def fingerprint(self):
        return digest({"endpoint": self.endpoint, "provenance": self.provenance, "timeouts": self.timeouts})


class DetectionFailure(Exception):
    def __init__(self, code):
        self.code = code


@dataclass(frozen=True)
class Original:
    storage_key: str
    sha256: str
    byte_size: int
    width_px: int
    height_px: int
    mime: str


def verified_bytes(original):
    """Verify and send the same bounded byte snapshot, never reopen for the upload."""
    validate_private_key(original.storage_key)
    if original.byte_size > MAX_UPLOAD:
        raise DetectionFailure("ORIGINAL_TOO_LARGE")
    try:
        with storages["target_photos"].open(original.storage_key, "rb") as source:
            data = source.read(MAX_UPLOAD + 1)
        if len(data) > MAX_UPLOAD:
            raise DetectionFailure("ORIGINAL_TOO_LARGE")
        if len(data) != original.byte_size or hashlib.sha256(data).hexdigest() != original.sha256:
            raise DetectionFailure("ORIGINAL_MISMATCH")
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data), formats=("JPEG", "PNG")) as image:
                width, height = image.size
                if max(width, height) > 8192 or width * height > 16_000_000:
                    raise DetectionFailure("ORIGINAL_TOO_LARGE")
                mime = {"JPEG": "image/jpeg", "PNG": "image/png"}[image.format]
                if (width, height, mime) != (original.width_px, original.height_px, original.mime) or getattr(image, "n_frames", 1) != 1:
                    raise DetectionFailure("ORIGINAL_MISMATCH")
                image.verify()
            with Image.open(BytesIO(data), formats=("JPEG", "PNG")) as image:
                image.load()
        return data
    except (OSError, UnidentifiedImageError, ValueError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise DetectionFailure("ORIGINAL_UNAVAILABLE") from error


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key.")
        result[key] = value
    return result


def _nonfinite(value):
    raise ValueError("Nonfinite JSON number.")


def validate_response(payload, original, config):
    expected = {"schema_version", "detections", "status", "analysis_width_px", "analysis_height_px",
                "preprocessing_version", "input_sha256", "checkpoint_runtime_version"} | PROVENANCE_FIELDS
    if type(payload) is not dict or set(payload) != expected or payload["status"] != "succeeded":
        raise DetectionFailure("INVALID_RESPONSE")
    validate_detection_result(payload, width=original.width_px, height=original.height_px)
    if any(type(payload[k]) is not int for k in ("analysis_width_px", "analysis_height_px")):
        raise DetectionFailure("INVALID_RESPONSE")
    if ((payload["analysis_width_px"], payload["analysis_height_px"]) != (original.width_px, original.height_px)
            or payload["preprocessing_version"] != PREPROCESSING_VERSION or payload["input_sha256"] != original.sha256):
        raise DetectionFailure("INPUT_MISMATCH")
    if canonical({key: payload[key] for key in PROVENANCE_FIELDS}) != canonical(config.provenance):
        raise DetectionFailure("PROVENANCE_MISMATCH")
    if len(payload["detections"]) > config.provenance["parameters"]["max_det"]:
        raise DetectionFailure("INVALID_RESPONSE")
    reported = payload["checkpoint_runtime_version"]
    if reported is not None and (type(reported) is not str or len(reported) > 128):
        raise DetectionFailure("INVALID_RESPONSE")
    if any(set(row) != {"id", "class_id", "confidence", "bbox_xyxy"} for row in payload["detections"]):
        raise DetectionFailure("INVALID_RESPONSE")
    return payload


def infer(original, config):
    """No retries, redirects, environment proxies or unbounded response decoding."""
    if not HTTP_SLOTS.acquire(blocking=False):
        raise DetectionFailure("CLIENT_BUSY")
    try:
        contents = verified_bytes(original)
        deadline = time.monotonic() + config.timeouts["total"]
        timeout = httpx.Timeout(**{key: config.timeouts[key] for key in ("connect", "read", "write", "pool")})
        limits = httpx.Limits(max_connections=1, max_keepalive_connections=0)
        transport = httpx.HTTPTransport(verify=True, trust_env=False, retries=0, limits=limits)
        with httpx.Client(transport=transport, timeout=timeout, limits=limits, trust_env=False, follow_redirects=False) as client:
            with client.stream("POST", config.endpoint, headers={"Accept": "application/json", "Accept-Encoding": "identity"},
                               files={"file": ("original.bin", BytesIO(contents), original.mime)}) as response:
                if response.status_code != 200:
                    raise DetectionFailure("REMOTE_HTTP_ERROR")
                if (response.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json"
                        or response.headers.get("content-encoding", "identity").lower() != "identity"):
                    raise DetectionFailure("INVALID_RESPONSE")
                length = response.headers.get("content-length")
                if length is not None and (not length.isascii() or not length.isdigit() or len(length) > 10 or int(length) > MAX_RESPONSE):
                    raise DetectionFailure("INVALID_RESPONSE")
                body = bytearray()
                for chunk in response.iter_raw():
                    if time.monotonic() > deadline:
                        raise DetectionFailure("INFERENCE_TIMEOUT")
                    if len(body) + len(chunk) > MAX_RESPONSE:
                        raise DetectionFailure("INVALID_RESPONSE")
                    body.extend(chunk)
                if time.monotonic() > deadline:
                    raise DetectionFailure("INFERENCE_TIMEOUT")
        payload = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_pairs, parse_constant=_nonfinite)
        payload = validate_response(payload, original, config)
        verified_bytes(original)  # Detect a replaced original while HTTP was running.
        return payload
    except httpx.TimeoutException as error:
        raise DetectionFailure("INFERENCE_TIMEOUT") from error
    except httpx.RequestError as error:
        raise DetectionFailure("INFERENCE_UNAVAILABLE") from error
    except (ValueError, ValidationError, RecursionError) as error:
        raise DetectionFailure("INVALID_RESPONSE") from error
    finally:
        HTTP_SLOTS.release()
