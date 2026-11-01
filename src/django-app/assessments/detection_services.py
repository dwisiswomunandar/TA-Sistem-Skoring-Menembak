"""Audited inference phases separated by HTTP outside every database transaction."""

import hashlib
import logging
from dataclasses import dataclass
from uuid import UUID, uuid5

from django.core.exceptions import ValidationError
from django.db import router, transaction

from activities.authorization import authorize_draft
from activities.history import lock_participation_references, lock_session_reference
from . import detection_client as client
from .models import AuditEvent, DetectionRun, TargetPhoto
from .result_models import context_for
from .services import OperationConflict, _append_audit, _identifier, _operation, _text

logger = logging.getLogger(__name__)
START_ACTION = "detection.started"
FINISH_ACTION = "detection.finished"
AUTHORIZATION_ACTION = "assessment.draft_updated"


@dataclass(frozen=True)
class DetectionReceipt:
    operation_id: UUID
    audit_event_id: int
    run_id: int
    status: str
    failure_code: str


@dataclass(frozen=True)
class _Work:
    operation_id: UUID
    root_id: int
    run_id: int
    photo_identity: dict
    session_context: dict
    run_state: dict
    original: client.Original
    config: client.Configuration


def _run_state(run):
    fields = ("id", "photo_id", "triggered_by_id", "model_name", "weights_sha256",
              "runtime_version", "adapter_version", "parameters", "failure_code")
    state = {key: getattr(run, key) for key in fields}
    state["status"] = str(run.status)
    for key in ("created_at", "updated_at", "started_at", "finished_at"):
        value = getattr(run, key)
        state[key] = value.isoformat() if value else None
    state["raw_result_sha256"] = client.digest(run.raw_result) if run.raw_result is not None else None
    return state


def _photo_identity(photo):
    fields = ("id", "session_id", "sha256", "byte_size", "width_px", "height_px", "verified_mime_type",
              "analysis_width_px", "analysis_height_px", "preprocessing_version", "uploaded_by_id")
    return {**{key: getattr(photo, key) for key in fields},
            "storage_key_sha256": hashlib.sha256(photo.storage_key.encode()).hexdigest()}


def _context(session_id, photo_id, using):
    session = lock_session_reference(session_id, using)
    lock_participation_references([session.participation_id], using)
    photo = TargetPhoto.objects.using(using).select_for_update().filter(pk=photo_id, session_id=session.pk).first()
    return session, photo


def _authorize(actor, session_id, using):
    return authorize_draft(actor=actor, action=AUTHORIZATION_ACTION, session_id=session_id, using=using, lock=True)


def _receipt(root, run, using):
    if run is None:
        raise ValidationError("The inference record is unavailable.")
    state = _run_state(run)
    if run.status == "running" and client.canonical(state) == client.canonical(root.after_state["run"]):
        return DetectionReceipt(root.operation_id, root.pk, run.pk, "running", "")
    terminal = AuditEvent.objects.using(using).filter(operation_id=uuid5(root.operation_id, FINISH_ACTION)).first()
    if (run.status not in ("succeeded", "failed") or terminal is None or terminal.action != FINISH_ACTION
            or terminal.actor_kind != "user" or terminal.actor_user_id != root.actor_user_id
            or terminal.session_id != root.session_id or terminal.caused_by_event_id != root.pk
            or client.canonical(terminal.after_state.get("run")) != client.canonical(state)):
        raise ValidationError("Inference state differs from its committed audit; reviewed recovery is required.")
    return DetectionReceipt(root.operation_id, terminal.pk, run.pk, str(run.status), run.failure_code)


def _begin(*, actor, session_id, photo_id, operation_id, reason, request_digest, using):
    with transaction.atomic(using=using):
        session, photo = _context(session_id, photo_id, using)
        root = AuditEvent.objects.using(using).filter(operation_id=operation_id).first()
        # Only lock a run in this photo's scope; foreign UUIDs do not select foreign rows.
        candidate = None
        if (photo is not None and root is not None and root.action == START_ACTION
                and root.actor_user_id == actor.pk and root.session_id == session.pk and isinstance(root.after_state, dict)):
            candidate = root.after_state.get("run", {}).get("id")
        run = DetectionRun.objects.using(using).select_for_update().filter(pk=candidate, photo_id=photo_id).first() if type(candidate) is int else None
        current = _authorize(actor, session.pk, using)
        if root is not None:
            if (root.action != START_ACTION or root.actor_kind != "user" or root.actor_user_id != current.pk
                    or root.session_id != session.pk or not isinstance(root.after_state, dict)
                    or root.after_state.get("request_sha256") != request_digest
                    or root.after_state.get("photo_identity", {}).get("id") != photo_id):
                raise OperationConflict("Operation UUID is already bound to another command.")
            return _receipt(root, run, using)  # No dispatch or repair on retries, including running runs.
        if photo is None:
            raise ValidationError("Photo is unavailable in this session.")
        config = client.Configuration.current()
        session.full_clean()
        photo.full_clean()
        if (photo.preprocessing_version != client.PREPROCESSING_VERSION
                or (photo.analysis_width_px, photo.analysis_height_px) != (photo.width_px, photo.height_px)):
            raise ValidationError("This detector requires the explicit unchanged-raster preprocessing contract.")
        run = DetectionRun(photo=photo, triggered_by=current, status="pending", **config.provenance)
        run.save(using=using)
        run.status = "running"
        run.save(using=using)
        identity, context, state = _photo_identity(photo), context_for(session, using), _run_state(run)
        root = _append_audit(using=using, operation_id=operation_id, actor_kind="user", actor_user=current,
            session=session, action=START_ACTION, reason=reason,
            before_state={"schema_version": 1, "run": None},
            after_state={"schema_version": 1, "run": state, "photo_identity": identity, "session_context": context,
                         "configuration_sha256": config.fingerprint(), "request_sha256": request_digest,
                         "lifecycle": ["pending", "running"]}, version_before=None, version_after=None)
        original = client.Original(photo.storage_key, photo.sha256, photo.byte_size, photo.width_px, photo.height_px, photo.verified_mime_type)
        return _Work(operation_id, root.pk, run.pk, identity, context, state, original, config)


def _finish(*, actor, session_id, photo_id, work, payload, failure_code, reason, using):
    with transaction.atomic(using=using):
        session, photo = _context(session_id, photo_id, using)
        run = DetectionRun.objects.using(using).select_for_update().filter(pk=work.run_id, photo_id=photo_id).first()
        current = _authorize(actor, session.pk, using)
        root = AuditEvent.objects.using(using).filter(pk=work.root_id, operation_id=work.operation_id).first()
        if (photo is None or run is None or root is None or run.status != "running"
                or root.action != START_ACTION or root.actor_kind != "user" or root.actor_user_id != current.pk
                or root.session_id != session.pk or run.triggered_by_id != current.pk
                or client.canonical(_run_state(run)) != client.canonical(work.run_state)
                or client.canonical(_photo_identity(photo)) != client.canonical(work.photo_identity)
                or client.canonical(context_for(session, using)) != client.canonical(work.session_context)
                or client.canonical(root.after_state.get("run")) != client.canonical(work.run_state)):
            raise ValidationError("Inference context changed while HTTP was running; result was not persisted.")
        run.status = "failed" if failure_code else "succeeded"
        run.failure_code, run.raw_result = failure_code, None if failure_code else payload
        run.save(using=using)
        terminal = _append_audit(using=using, operation_id=uuid5(work.operation_id, FINISH_ACTION),
            actor_kind="user", actor_user=current, session=session, action=FINISH_ACTION, reason=reason,
            caused_by_event=root, before_state={"schema_version": 1, "run": work.run_state},
            after_state={"schema_version": 1, "run": _run_state(run), "root_operation_id": str(work.operation_id)},
            version_before=None, version_after=None)
        return DetectionReceipt(work.operation_id, terminal.pk, run.pk, str(run.status), run.failure_code)


def detect_photo(*, actor, session_id, photo_id, operation_id, reason, using=None):
    """Trusted Django User, explicit photo/session and UUID. No client provenance or URLs.

    Requires autocommit: ATOMIC_REQUESTS/outer atomic would retain locks over HTTP.
    Crash/uncertain running operations need a separate reviewed recovery workflow.
    """
    using = using or router.db_for_write(DetectionRun)
    if transaction.get_connection(using).in_atomic_block or not transaction.get_autocommit(using):
        raise ValidationError("Invoke detection outside every outer transaction.")
    authorize_draft(actor=actor, action=AUTHORIZATION_ACTION, session_id=session_id, using=using)
    session_id, photo_id = _identifier(session_id, "session_id"), _identifier(photo_id, "photo_id")
    operation_id, reason = _operation(operation_id), _text(reason, "reason", required=True)
    request_digest = client.digest({"action": START_ACTION, "actor_user_id": actor.pk,
        "session_id": session_id, "photo_id": photo_id, "reason": reason})
    work = _begin(actor=actor, session_id=session_id, photo_id=photo_id, operation_id=operation_id,
                  reason=reason, request_digest=request_digest, using=using)
    if isinstance(work, DetectionReceipt):
        return work
    payload, failure_code = None, ""
    try:
        payload = client.infer(work.original, work.config)
    except client.DetectionFailure as error:
        failure_code = error.code
    except Exception:
        logger.exception("Internal detection operation failed before finalization.")
        failure_code = "INFERENCE_ERROR"
    return _finish(actor=actor, session_id=session_id, photo_id=photo_id, work=work, payload=payload,
                   failure_code=failure_code, reason=reason, using=using)
