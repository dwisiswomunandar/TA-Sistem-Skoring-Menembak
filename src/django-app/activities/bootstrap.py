"""Deployment-authorized first activity administrator, never a recovery API."""

import hashlib
import json
from dataclasses import dataclass
from uuid import UUID

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.db import router, transaction
from django.utils.crypto import constant_time_compare
from django.views.decorators.debug import sensitive_variables

from assessments.models import AuditEvent
from .access_rules import ADMINISTRATIVE_PERMISSIONS, GRANT_PERMISSIONS
from .grant_services import OperationConflict, _append_audit, _direct, _identifier, _registry, _snapshot
from .models import Activity, ActivityPermissionGrant

ACTION = "activity_grant.admin_bootstrapped"
DENIED = "Deployment bootstrap is not authorized."
PLAN_FIELDS = frozenset(("actor_id", "activity_id", "recipient_id", "permissions", "operation_id", "reason", "approval_reference"))


@dataclass(frozen=True)
class BootstrapReceipt:
    operation_id: UUID
    audit_event_id: int
    activity_id: int
    recipient_id: int
    grant_versions: tuple[tuple[int, int], ...]
    added_global_permission_ids: tuple[int, ...]


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValidationError({name: "Explicit nonblank text is required."})
    return value.strip()


def _canonical(plan):
    if type(plan) is not dict or set(plan) != PLAN_FIELDS:
        raise ValidationError("Use the complete bootstrap plan with no extra fields.")
    result = {key: _identifier(plan[key], key) for key in ("actor_id", "activity_id", "recipient_id")}
    permissions = plan["permissions"]
    if (type(permissions) not in (list, tuple) or any(type(p) is not str for p in permissions)
            or len(permissions) != len(GRANT_PERMISSIONS) or set(permissions) != GRANT_PERMISSIONS):
        raise ValidationError("The plan must name each of the eight registered permissions exactly once.")
    result["permissions"] = sorted(permissions)
    operation = plan["operation_id"]
    if not isinstance(operation, (str, UUID)):
        raise ValidationError("Use an explicit operation UUID.")
    try:
        result["operation_id"] = str(UUID(str(operation)))
    except ValueError as error:
        raise ValidationError("Use an explicit operation UUID.") from error
    for key in ("reason", "approval_reference"):
        result[key] = _text(plan[key], key)
    return result


def deployment_plans():
    """Read protected settings, never request flags. Also used before CLI password prompt."""
    enabled = getattr(settings, "ACTIVITY_ADMIN_BOOTSTRAP_ENABLED", False)
    if enabled is False:
        raise PermissionDenied(DENIED)
    if enabled is not True:
        raise ImproperlyConfigured("Bootstrap deployment configuration is invalid.")
    plans = getattr(settings, "ACTIVITY_ADMIN_BOOTSTRAP_PLANS", ())
    if type(plans) not in (tuple, list) or not plans:
        raise ImproperlyConfigured("Bootstrap deployment approvals are invalid.")
    try:
        normalized = [_canonical(plan) for plan in plans]
        if (len({p["operation_id"] for p in normalized}) != len(normalized)
                or len({p["activity_id"] for p in normalized}) != len(normalized)):
            raise ValidationError("Ambiguous deployment plans.")
    except ValidationError:
        # Do not include configuration values, credentials or plan contents in errors.
        raise ImproperlyConfigured("Bootstrap deployment approvals are invalid.") from None
    return normalized


def _approved(plan):
    if plan not in deployment_plans():
        raise PermissionDenied(DENIED)


def _receipt(event):
    result = event.after_state["bootstrap_result"]
    return BootstrapReceipt(event.operation_id, event.pk, event.activity_id, result["recipient_id"],
        tuple((item["grant_id"], item["edit_version"]) for item in result["grant_versions"]),
        tuple(result["added_global_permission_ids"]))


def _replay(operation_id, digest, actor_id, activity_id, using):
    event = AuditEvent.objects.using(using).filter(operation_id=operation_id).first()
    if event is None:
        return None
    if (event.action != ACTION or event.actor_kind != "user" or event.actor_user_id != actor_id
            or event.activity_id != activity_id or not isinstance(event.after_state, dict)
            or event.after_state.get("bootstrap_command_sha256") != digest
            or "bootstrap_result" not in event.after_state):
        raise OperationConflict("Operation UUID is bound to another command.")
    return _receipt(event)


def _state(recipient_id, plan, direct, grants, registry):
    ids = {p.pk for p in registry.values()}
    return {"schema_version": 1, "recipient_id": recipient_id,
        "activity_id": plan["activity_id"], "permission_plan": plan["permissions"],
        "approval_reference": plan["approval_reference"],
        "direct_permission_ids": sorted(direct & ids),
        "grants": [_snapshot(row, registry[name])["grant"]
                   for name in sorted(registry) for row in grants if row.permission_id == registry[name].pk]}


@sensitive_variables("actor_password")
def bootstrap_activity_administrator(*, actor_email, actor_password, activity_id, recipient_id,
                                    permissions, operation_id, reason, approval_reference, using=None):
    # Authenticate here: a caller-supplied User instance/ID is not authentication evidence.
    deployment_plans()
    if not isinstance(actor_email, str) or not isinstance(actor_password, str) or not actor_password:
        raise PermissionDenied(DENIED)
    using = using or router.db_for_write(ActivityPermissionGrant)
    actor = authenticate(email=actor_email, password=actor_password)
    model = get_user_model()
    if (not isinstance(actor, model) or actor._state.adding or actor._state.db != using
            or not actor.is_active or type(actor.pk) is not int):
        raise PermissionDenied(DENIED)
    plan = _canonical(dict(actor_id=actor.pk, activity_id=activity_id, recipient_id=recipient_id,
        permissions=permissions, operation_id=operation_id, reason=reason, approval_reference=approval_reference))
    _approved(plan)
    digest = hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    operation = UUID(plan["operation_id"])
    with transaction.atomic(using=using):
        activity = Activity.objects.using(using).select_for_update().filter(pk=plan["activity_id"]).first()
        if activity is None:
            raise PermissionDenied(DENIED)
        users = {row.pk: row for row in model.objects.using(using).select_for_update().filter(
            pk__in={actor.pk, plan["recipient_id"]}).order_by("pk")}
        current_actor = users.get(actor.pk)
        if (current_actor is None or not current_actor.is_active
                or not constant_time_compare(current_actor.password, actor.password)):
            raise PermissionDenied(DENIED)
        _approved(plan)  # Authorization is current and precedes any receipt replay.
        recipient = users.get(plan["recipient_id"])
        if recipient is None:
            raise PermissionDenied(DENIED)
        # The recipient User lock serializes compliant grant writers across activities.
        all_recipient_grants = list(ActivityPermissionGrant.objects.using(using).select_for_update().filter(
            user_id=recipient.pk).order_by("pk"))
        replay = _replay(operation, digest, current_actor.pk, activity.pk, using)
        if replay is not None:
            return replay
        if not recipient.is_active or activity.status == Activity.Status.CLOSED:
            raise ValidationError("Bootstrap requires an active recipient and a non-closed activity.")
        registry = _registry(using)
        if AuditEvent.objects.using(using).filter(activity_id=activity.pk, action=ACTION).exists():
            raise ValidationError("Activity was already bootstrapped; recovery is a separate workflow.")
        admin_ids = {registry[name].pk for name in ADMINISTRATIVE_PERMISSIONS}
        if ActivityPermissionGrant.objects.using(using).filter(activity_id=activity.pk, permission_id__in=admin_ids).exists():
            raise ValidationError("Existing administrative mandate requires a separate reviewed workflow.")
        grants = [row for row in all_recipient_grants if row.activity_id == activity.pk]
        valid_ids = {permission.pk for permission in registry.values()}
        if any(not row.is_active or row.permission_id not in valid_ids for row in grants):
            raise ValidationError("Bootstrap cannot repair or reactivate existing scope records.")
        direct_before = _direct(recipient, using)
        added = sorted(valid_ids - direct_before)
        if added and any(row.activity_id != activity.pk for row in all_recipient_grants):
            raise ValidationError("Global permission additions could activate another activity scope.")
        before = _state(recipient.pk, plan, direct_before, grants, registry)
        if added:
            recipient.user_permissions.add(*[p for p in registry.values() if p.pk in added])
        existing = {row.permission_id for row in grants}
        for name in sorted(registry):
            permission = registry[name]
            if permission.pk not in existing:
                row = ActivityPermissionGrant(user=recipient, activity=activity, permission=permission,
                                              created_by=current_actor, is_active=True)
                row.save(using=using)
                grants.append(row)
        after = _state(recipient.pk, plan, _direct(recipient, using), grants, registry)
        after["bootstrap_command_sha256"] = digest
        after["bootstrap_result"] = {"recipient_id": recipient.pk,
            "grant_versions": [{"grant_id": row.pk, "edit_version": row.edit_version} for row in sorted(grants, key=lambda g: g.pk)],
            "added_global_permission_ids": added}
        event = _append_audit(using, operation_id=operation, actor_kind="user", actor_user=current_actor,
            activity=activity, action=ACTION, reason=plan["reason"], before_state=before, after_state=after,
            version_before=None, version_after=None)
        return _receipt(event)
