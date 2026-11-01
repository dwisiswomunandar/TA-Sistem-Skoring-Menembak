"""Bounded detection endpoint; no scoring, calibration or implicit weights download."""

import logging
import threading
from contextlib import asynccontextmanager

import anyio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from python_multipart.exceptions import MultipartParseError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException
from starlette.requests import ClientDisconnect

from detection_contract import DetectionContractError
from detector_runtime import DetectorBusy, DetectorRuntime, ImageRejected, Settings, load_model

logger = logging.getLogger(__name__)


def failure(code, status):
    return JSONResponse({"status": "failed", "failure_code": code}, status_code=status)


class UploadBoundary:
    """Count streamed bytes before multipart spooling and limit in-flight work."""
    def __init__(self, app, settings):
        self.app, self.settings = app, settings
        self.slots = threading.BoundedSemaphore(settings.max_inflight)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        route_path = scope["path"].removeprefix(scope.get("root_path", ""))
        if route_path != "/api/evaluate" or scope["method"] != "POST":
            return await self.app(scope, receive, send)
        if not self.slots.acquire(blocking=False):
            return await failure("DETECTOR_BUSY", 503)(scope, receive, send)
        try:
            maximum = self.settings.max_upload_bytes + 64 * 1024
            lengths = [value for key, value in scope["headers"] if key.lower() == b"content-length"]
            if lengths:
                if len(lengths) != 1 or not lengths[0].isdigit():
                    return await failure("INVALID_REQUEST", 400)(scope, receive, send)
                if len(lengths[0]) > 20 or int(lengths[0]) > maximum:
                    return await failure("UPLOAD_TOO_LARGE", 413)(scope, receive, send)
            count, complete = 0, False
            deadline = anyio.current_time() + self.settings.upload_timeout_s

            async def bounded_receive():
                nonlocal count, complete
                if complete:
                    return await receive()
                try:
                    with anyio.fail_after(max(0, deadline - anyio.current_time())):
                        message = await receive()
                except TimeoutError as error:
                    raise HTTPException(408, detail="UPLOAD_TIMEOUT") from error
                if message["type"] == "http.request":
                    count += len(message.get("body", b""))
                    if count > maximum:
                        raise HTTPException(413, detail="UPLOAD_TOO_LARGE")
                    complete = not message.get("more_body", False)
                return message
            await self.app(scope, bounded_receive, send)
        finally:
            self.slots.release()


def create_app(settings=None, *, model_factory=load_model):
    settings = settings if settings is not None else Settings.from_environment()
    runtime = DetectorRuntime(settings, model_factory)

    @asynccontextmanager
    async def lifespan(app):
        try:
            await run_in_threadpool(runtime.load)
        except Exception:
            logger.exception("Configured detector could not be initialized.")
        yield

    app = FastAPI(title="Target detection contract v1", lifespan=lifespan)
    app.state.detector = runtime
    app.add_middleware(UploadBoundary, settings=settings)

    @app.exception_handler(HTTPException)
    async def rejected(request, error):
        code = error.detail if error.detail in ("UPLOAD_TOO_LARGE", "UPLOAD_TIMEOUT") else "INVALID_REQUEST"
        return failure(code, error.status_code)

    async def uploaded_result(request):
        async with request.form(max_files=1, max_fields=0, max_part_size=settings.max_upload_bytes) as form:
            entries = list(form.multi_items())
            if len(entries) != 1 or entries[0][0] != "file" or not isinstance(entries[0][1], UploadFile):
                return failure("INVALID_REQUEST", 400)
            upload = entries[0][1]
            if upload.size is not None and upload.size > settings.max_upload_bytes:
                return failure("UPLOAD_TOO_LARGE", 413)
            contents = await upload.read(settings.max_upload_bytes + 1)
            try:
                payload = await run_in_threadpool(runtime.evaluate, contents, upload.content_type)
            except ImageRejected as error:
                return failure(error.code, error.status)
            except DetectorBusy:
                return failure("DETECTOR_BUSY", 503)
            except DetectionContractError:
                logger.warning("Detector produced an invalid contract result.")
                return failure("INVALID_DETECTION_RESULT", 500)
            except Exception:
                logger.exception("Detector inference failed.")
                return failure("INFERENCE_FAILED", 500)
        return JSONResponse(payload)

    @app.post("/api/evaluate")
    async def evaluate_target(request: Request):
        if runtime.model is None:
            return failure("MODEL_UNAVAILABLE", 503)
        if request.headers.get("content-type", "").split(";", 1)[0].strip() != "multipart/form-data":
            return failure("UNSUPPORTED_REQUEST", 415)
        try:
            return await uploaded_result(request)
        except (MultipartParseError, ClientDisconnect):
            return failure("INVALID_REQUEST", 400)

    return app


app = create_app()
