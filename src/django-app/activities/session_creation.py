"""Atomic session creation with an approval gate, server numbering and audit replay."""

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, router, transaction
from django.db.models import Max

from assessments.models import AuditEvent
from .models import Activity, ActivityParticipation, TrainingSession
from .parameters import PARAMETER_FIELDS, resolve_parameters
from .session_creation_policy import DENIED, POLICY_VERSION, authorize_creation

ACTION = "training_session.created"


@dataclass(frozen=True)
class SessionCreationReceipt:
    operation_id: UUID
    audit_event_id: int
    session_id: int
    activity_id: int
    participation_id: int
    session_number: int
    edit_version: int


def validated_parameters(data):
    if not isinstance(data, dict) or set(data) - set(PARAMETER_FIELDS):
        raise ValidationError("Hanya parameter penembakan yang boleh dikirim.")
    values = {}
    for name in PARAMETER_FIELDS:
        value = data.get(name)
        if name != "weapon_type" and (value is None or value == ""):
            values[name] = None
            continue
        if name in ("distance_m", "caliber_mm") and isinstance(value, bool):
            raise ValidationError({name: "Parameter angka tidak boleh berupa boolean."})
        values[name] = TrainingSession._meta.get_field(name).clean(value, None)
    resolved = resolve_parameters(**values)
    for name in ("distance_m", "caliber_mm"):
        precision = TrainingSession._meta.get_field(name).decimal_places
        resolved[name] = resolved[name].quantize(Decimal(1).scaleb(-precision))
    return resolved


def _request_digest(actor_id, participation_id, parameters, reason, approval):
    request = {
        "policy": POLICY_VERSION, "action": ACTION, "actor_user_id": actor_id,
        "participation_id": participation_id, "approval": approval, "reason": reason,
        "parameters": {key: str(value) for key, value in parameters.items()},
    }
    return hashlib.sha256(json.dumps(
        request, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()


def _replay(operation_id, digest, actor_id, participation_id, using):
    event = AuditEvent.objects.using(using).filter(operation_id=operation_id).first()
    if event is None:
        return None
    state = event.after_state
    if not isinstance(state, dict):
        raise ValidationError("Audit permintaan tidak valid.")
    result = state.get("creation_result", {})
    fields = {"session_id", "activity_id", "participation_id", "session_number", "edit_version"}
    if (not isinstance(result, dict) or set(result) != fields
            or any(type(value) is not int or value <= 0 for value in result.values())):
        raise ValidationError("ID permintaan sudah digunakan untuk operasi lain.")
    if (event.action != ACTION or event.actor_kind != "user" or event.actor_user_id != actor_id
            or state.get("creation_command_sha256") != digest
            or result.get("participation_id") != participation_id
            or result.get("session_id") != event.session_id):
        raise ValidationError("ID permintaan sudah digunakan untuk operasi lain.")
    return SessionCreationReceipt(operation_id, event.pk, **result)


def _eligible(participation, activity, person):
    if activity.status != Activity.Status.ACTIVE:
        raise ValidationError("Kegiatan harus aktif untuk membuat sesi.")
    if participation.registration_status != ActivityParticipation.RegistrationStatus.APPROVED:
        raise ValidationError("Pendaftaran peserta harus approved.")
    if person.identity_status != "verified":
        raise ValidationError("Identitas peserta harus verified.")


def _create(*, actor, participation_id, parameters, operation_id, reason, approval, digest, using):
    with transaction.atomic(using=using):
        # New rows start at participation. Serialize numbering with every guarded
        # TrainingSession.save(), then lock activity/identity/User/grants in order.
        participation = ActivityParticipation.objects.using(using).select_for_update().filter(
            pk=participation_id, activity_id=approval["activity_id"],
        ).first()
        if participation is None:
            raise PermissionDenied(DENIED)
        activity = Activity.objects.using(using).select_for_update().get(pk=participation.activity_id)
        person_model = ActivityParticipation._meta.get_field("participant").remote_field.model
        person = person_model.objects.using(using).select_for_update().get(pk=participation.participant_id)
        current, locked_approval = authorize_creation(
            actor=actor, participation_id=participation_id, using=using, lock=True,
        )
        if locked_approval != approval:
            raise PermissionDenied(DENIED)
        _eligible(participation, activity, person)
        replay = _replay(operation_id, digest, current.pk, participation_id, using)
        if replay is not None:
            return replay
        last = TrainingSession.objects.using(using).filter(participation_id=participation_id).aggregate(
            last=Max("session_number"),
        )["last"] or 0
        session = TrainingSession(
            participation=participation, session_number=last + 1,
            assigned_assessor=current, created_by=current, **parameters,
        )
        # Normal guarded save validates eligibility, parameters, uniqueness and
        # version. No bulk writes, SQL insertion or model guard bypass.
        session.save(using=using)
        result = {
            "session_id": session.pk, "activity_id": activity.pk,
            "participation_id": participation.pk, "session_number": session.session_number,
            "edit_version": session.edit_version,
        }
        snapshot = {
            **result, "participant_id": person.pk, "assigned_assessor_id": current.pk,
            "created_by_id": current.pk, "created_at": session.created_at.isoformat(),
            **{key: str(value) for key, value in parameters.items()},
        }
        event = AuditEvent(
            operation_id=operation_id, actor_kind="user", actor_user=current, session=session,
            action=ACTION, reason=reason, version_before=None, version_after=session.edit_version,
            before_state={"schema_version": 1, "session": None},
            after_state={
                "schema_version": 1, "session": snapshot, "creation_approval": approval,
                "creation_policy": POLICY_VERSION, "creation_command_sha256": digest,
                "creation_result": result,
                "eligibility": {"activity_status": activity.status,
                                "registration_status": participation.registration_status,
                                "participation_edit_version": participation.edit_version,
                                "identity_status": person.identity_status},
            },
        )
        # This is our uncommitted new session, so AuditEvent.save() re-locking it
        # cannot contend with an existing session held by another transaction.
        event.save(using=using)
        return SessionCreationReceipt(operation_id, event.pk, **result)


def create_training_session(*, actor, participation_id, data, operation_id, reason, using=None):
    using = using or router.db_for_write(TrainingSession)
    current, approval = authorize_creation(actor=actor, participation_id=participation_id, using=using)
    if not isinstance(operation_id, (UUID, str)):
        raise ValidationError({"operation_id": "ID permintaan harus berupa UUID."})
    try:
        operation_id = UUID(str(operation_id))
    except ValueError as error:
        raise ValidationError({"operation_id": "ID permintaan harus berupa UUID."}) from error
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
        raise ValidationError({"reason": "Isi alasan pembuatan sesi, maksimal 2000 karakter."})
    reason = reason.strip()
    parameters = validated_parameters(data)
    digest = _request_digest(current.pk, participation_id, parameters, reason, approval)
    for attempt in range(2):
        try:
            return _create(actor=actor, participation_id=participation_id, parameters=parameters,
                           operation_id=operation_id, reason=reason, approval=approval, digest=digest, using=using)
        except (IntegrityError, ValidationError) as error:
            # After full rollback, retry an operation-ID collision once. The
            # second transaction rechecks current authority before any receipt.
            collision = AuditEvent.objects.using(using).filter(operation_id=operation_id).exists()
            if attempt or not collision:
                if isinstance(error, ValidationError):
                    raise
                raise ValidationError("Sesi gagal disimpan. Muat ulang dan periksa permintaan Anda.") from error
    raise AssertionError("Unreachable")
