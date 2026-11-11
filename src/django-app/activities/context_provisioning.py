"""Deployment-approved creation of NEW draft/pending/pending context data only."""

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date
from uuid import UUID

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.db import IntegrityError, router, transaction
from django.utils.crypto import constant_time_compare
from django.views.decorators.debug import sensitive_variables

from accounts.models import Participant
from assessments.models import AuditEvent
from .grant_services import OperationConflict, _append_audit, _identifier
from .models import Activity, ActivityParticipation, normalize_optional_identifier

ACTION = "training_context.provisioned"
POLICY = "context_data_provisioning_v1"
DENIED = "Operational context provisioning is not authorized."
REQUEST_FIELDS = frozenset(("operation_id", "purpose", "activity", "participant",
                            "participation", "reason", "authorization_reference"))
PURPOSES = frozenset(("development", "simulation", "operational"))
STATUS = "prepared_pending_decisions"
ACCESS_DECISION = "authorized_for_context_data_only"
NOT_GRANTED = ("activate_activity", "verify_identity", "approve_participation",
               "grant_permissions", "assign_assessor", "create_session")


@dataclass(frozen=True)
class ContextProvisioningReceipt:
    operation_id: UUID
    audit_event_id: int
    actor_id: int
    activity_id: int
    participant_id: int
    participation_id: int
    purpose: str
    status: str = STATUS
    access_decision: str = ACCESS_DECISION
    not_granted_by_operation: tuple[str, ...] = NOT_GRANTED

    def as_dict(self):
        result = asdict(self)
        result["operation_id"] = str(self.operation_id)
        result["not_granted_by_operation"] = list(self.not_granted_by_operation)
        return result


def _object(value, fields):
    if type(value) is not dict or set(value) != set(fields):
        raise ValidationError("Supply exactly the documented data fields; extra fields are forbidden.")


def _text(value, field, *, maximum, optional=False):
    if optional and value is None:
        value = ""
    if type(value) is not str or len(value) > maximum or (not optional and not value.strip()):
        raise ValidationError({field: "Supply bounded text in the documented format."})
    if "\x00" in value:
        raise ValidationError({field: "NUL characters are forbidden."})
    try:
        value.encode("utf-8")
    except UnicodeError as error:
        raise ValidationError({field: "Supply valid Unicode text."}) from error
    return value.strip()


def canonical_request(data):
    """Query-free input validation and normalization, including model field limits."""
    _object(data, REQUEST_FIELDS)
    _object(data["activity"], ("name", "training_date"))
    _object(data["participant"], ("full_name", "identity_number"))
    _object(data["participation"], ("participant_identifier",))
    if not isinstance(data["operation_id"], (str, UUID)):
        raise ValidationError("Supply an operation UUID.")
    try:
        operation = str(UUID(str(data["operation_id"])))
    except ValueError as error:
        raise ValidationError("Supply an operation UUID.") from error
    if type(data["purpose"]) is not str or data["purpose"] not in PURPOSES:
        raise ValidationError("Select an explicit development, simulation or operational purpose.")
    training_date = data["activity"]["training_date"]
    if type(training_date) is str:
        try:
            parsed = date.fromisoformat(training_date)
        except ValueError as error:
            raise ValidationError("Use an ISO training date (YYYY-MM-DD).") from error
        if parsed.isoformat() != training_date:
            raise ValidationError("Use an ISO training date (YYYY-MM-DD).")
        training_date = parsed
    if type(training_date) is not date:
        raise ValidationError("Use an explicit training date.")
    activity_name = _text(data["activity"]["name"], "name", maximum=255)
    full_name = _text(data["participant"]["full_name"], "full_name", maximum=255)
    identity = _text(data["participant"]["identity_number"], "identity_number", maximum=64, optional=True)
    identifier = normalize_optional_identifier(_text(
        data["participation"]["participant_identifier"], "participant_identifier", maximum=64, optional=True,
    ))
    # Field validation has no FK/uniqueness queries; complete models are validated
    # again by their normal guarded save() paths inside the transaction.
    for model, name, value in (
        (Activity, "name", activity_name), (Activity, "training_date", training_date),
        (Participant, "full_name", full_name), (Participant, "identity_number", identity),
        (ActivityParticipation, "participant_identifier", identifier),
    ):
        model._meta.get_field(name).clean(value, None)
    return {
        "operation_id": operation, "purpose": data["purpose"],
        "activity": {"name": activity_name, "training_date": training_date.isoformat()},
        "participant": {"full_name": full_name, "identity_number": identity},
        "participation": {"participant_identifier": identifier},
        "reason": _text(data["reason"], "reason", maximum=2000),
        "authorization_reference": _text(data["authorization_reference"], "authorization_reference", maximum=255),
    }


def deployment_plans():
    enabled = settings.OPERATIONAL_CONTEXT_PROVISIONING_ENABLED
    if enabled is False:
        raise PermissionDenied(DENIED)
    if enabled is not True:
        raise ImproperlyConfigured("Context provisioning configuration is invalid.")
    plans = settings.OPERATIONAL_CONTEXT_PROVISIONING_PLANS
    if type(plans) not in (list, tuple) or not plans:
        raise ImproperlyConfigured("Context provisioning approvals are invalid.")
    try:
        result = []
        operations = set()
        for plan in plans:
            _object(plan, REQUEST_FIELDS | {"actor_id"})
            actor_id = _identifier(plan["actor_id"], "actor_id")
            request = canonical_request({key: plan[key] for key in REQUEST_FIELDS})
            if request["operation_id"] in operations:
                raise ValidationError("Duplicate operation approvals.")
            operations.add(request["operation_id"])
            result.append({"actor_id": actor_id, **request})
        return result
    except ValidationError:
        # Do not disclose protected plan contents in errors.
        raise ImproperlyConfigured("Context provisioning approvals are invalid.") from None


def _approved(plan):
    if plan not in deployment_plans():
        raise PermissionDenied(DENIED)


def _digest(plan):
    return hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _replay(operation, digest, actor_id, using):
    event = AuditEvent.objects.using(using).filter(operation_id=operation).first()
    if event is None:
        return None
    state = event.after_state
    if (event.action != ACTION or event.actor_kind != "user" or event.actor_user_id != actor_id
            or not isinstance(state, dict) or state.get("provisioning_policy") != POLICY
            or state.get("provisioning_request_sha256") != digest):
        raise OperationConflict("Operation UUID is bound to another command.")
    result = state.get("provisioning_result")
    expected = {"actor_id", "activity_id", "participant_id", "participation_id", "purpose", "status",
                "access_decision", "not_granted_by_operation"}
    if (type(result) is not dict or set(result) != expected
            or any(type(result[key]) is not int or result[key] <= 0
                   for key in ("actor_id", "activity_id", "participant_id", "participation_id"))
            or result["actor_id"] != actor_id or event.participation_id != result["participation_id"]
            or event.activity_id is not None or event.session_id is not None or event.assessment_id is not None
            or type(result["purpose"]) is not str or result["purpose"] not in PURPOSES or result["status"] != STATUS
            or result["access_decision"] != ACCESS_DECISION or result["not_granted_by_operation"] != list(NOT_GRANTED)):
        raise OperationConflict("Provisioning receipt is unavailable.")
    return ContextProvisioningReceipt(operation, event.pk, **{
        **result, "not_granted_by_operation": tuple(result["not_granted_by_operation"]),
    })


def _execute(actor, plan, digest, using):
    operation = UUID(plan["operation_id"])
    with transaction.atomic(using=using):
        current = get_user_model().objects.using(using).select_for_update().filter(pk=actor.pk).first()
        if (current is None or not current.is_active
                or not constant_time_compare(current.password, actor.password)):
            raise PermissionDenied(DENIED)
        _approved(plan)  # Current authorization is required even for a replay.
        replay = _replay(operation, digest, current.pk, using)
        if replay is not None:
            return replay
        # Only NEW rows are created after the User lock. This path never locks or
        # mutates an existing Activity/Participant/Participation or security grant.
        activity = Activity(name=plan["activity"]["name"], training_date=plan["activity"]["training_date"],
                            status=Activity.Status.DRAFT.value, created_by=current)
        activity.save(using=using)
        person = Participant(**plan["participant"], identity_status=Participant.IdentityStatus.PENDING.value)
        person.save(using=using)
        participation = ActivityParticipation(
            activity=activity, participant=person, registration_status=ActivityParticipation.RegistrationStatus.PENDING.value,
            participant_identifier=plan["participation"]["participant_identifier"], registered_by=current,
        )
        participation.save(using=using)
        result = {
            "actor_id": current.pk, "activity_id": activity.pk, "participant_id": person.pk,
            "participation_id": participation.pk, "purpose": plan["purpose"], "status": STATUS,
            "access_decision": ACCESS_DECISION, "not_granted_by_operation": list(NOT_GRANTED),
        }
        # Participation scope preserves XOR and freezes its activity/person
        # references via the existing historical guards. No standalone person scope
        # or schema change is needed for this single-new-context aggregate.
        event = _append_audit(
            using, operation_id=operation, actor_kind="user", actor_user=current, participation=participation,
            action=ACTION, reason=plan["reason"], version_before=None, version_after=participation.edit_version,
            before_state={"schema_version": 1, "activity": None, "participant": None, "participation": None},
            after_state={
                "schema_version": 1, "provisioning_policy": POLICY, "provisioning_request_sha256": digest,
                "authorization_reference": plan["authorization_reference"], "provisioning_result": result,
                "activity": {"id": activity.pk, "status": activity.status, "created_by_id": current.pk},
                "participant": {"id": person.pk, "identity_status": person.identity_status,
                                "account_id": None, "verified_by_id": None, "verified_at": None},
                "participation": {"id": participation.pk, "activity_id": activity.pk, "participant_id": person.pk,
                                  "registration_status": participation.registration_status, "registered_by_id": current.pk,
                                  "decided_by_id": None, "decided_at": None, "edit_version": participation.edit_version},
            },
        )
        return ContextProvisioningReceipt(operation, event.pk, **{
            **result, "not_granted_by_operation": tuple(NOT_GRANTED),
        })


@sensitive_variables("actor_password")
def provision_operational_context(*, actor_email, actor_password, data, using=None):
    """Credentials + exact protected plan; no caller User/actor ID or bypass flags."""
    deployment_plans()  # Fail closed before authentication when unconfigured.
    request = canonical_request(data)
    if type(actor_email) is not str or type(actor_password) is not str or not actor_password:
        raise PermissionDenied(DENIED)
    using = using or router.db_for_write(ActivityParticipation)
    actor = authenticate(email=actor_email, password=actor_password)
    model = get_user_model()
    if (not isinstance(actor, model) or actor._state.adding or actor._state.db != using
            or not actor.is_active or type(actor.pk) is not int):
        raise PermissionDenied(DENIED)
    plan = {"actor_id": actor.pk, **request}
    _approved(plan)
    digest = _digest(plan)
    for attempt in range(2):
        try:
            return _execute(actor, plan, digest, using)
        except (IntegrityError, ValidationError) as error:
            # Full rollback precedes the bounded retry. The User lock serializes
            # same-actor requests; the unique audit UUID covers external collisions.
            collision = AuditEvent.objects.using(using).filter(operation_id=UUID(plan["operation_id"])).exists()
            if attempt or not collision:
                if isinstance(error, ValidationError):
                    raise
                raise ValidationError("Context provisioning failed atomically.") from error
    raise AssertionError("Unreachable")
