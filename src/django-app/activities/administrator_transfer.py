"""Deployment-approved normal handover; deliberately not an emergency recovery API."""

import hashlib
import json
from dataclasses import dataclass
from uuid import UUID

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.db import router, transaction
from django.db.models import Q
from django.utils.crypto import constant_time_compare
from django.views.decorators.debug import sensitive_variables

from assessments.models import AuditEvent
from .authority_policy import (
    ACTIVITY_ADMIN_POLICY, ACTIVITY_ADMIN_PERMISSIONS, ACTIVITY_ADMIN_MANAGEMENT_PERMISSIONS,
    administrator_permissions, effective_administrator_ids, require_administrator_continuity,
)
from .bootstrap import _text
from .grant_services import OperationConflict, _append_audit, _direct, _identifier, _snapshot
from .models import Activity, ActivityPermissionGrant

ACTION = "activity_grant.admin_transferred"
DENIED = "Deployment transfer is not authorized."
PLAN_FIELDS = frozenset({
    "actor_id", "activity_id", "recipient_id", "policy", "expected_grant_versions",
    "expected_state_sha256", "operation_id", "reason", "approval_reference",
})


@dataclass(frozen=True)
class TransferReceipt:
    operation_id: UUID
    audit_event_id: int
    activity_id: int
    source_id: int
    recipient_id: int
    grant_versions: tuple[tuple[int, int], ...]
    added_global_permission_ids: tuple[int, ...]


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _operation(value):
    if not isinstance(value, (str, UUID)):
        raise ValidationError("An explicit operation UUID is required.")
    try:
        return str(UUID(str(value)))
    except ValueError as error:
        raise ValidationError("An explicit operation UUID is required.") from error


def _canonical(plan):
    if type(plan) is not dict or set(plan) != PLAN_FIELDS:
        raise ValidationError("Use a complete transfer plan with no extra fields.")
    result = {key: _identifier(plan[key], key) for key in ("actor_id", "activity_id", "recipient_id")}
    if result["actor_id"] == result["recipient_id"] or plan["policy"] != ACTIVITY_ADMIN_POLICY:
        raise ValidationError("A normal transfer requires distinct users and the fixed administrator policy.")
    result["policy"] = ACTIVITY_ADMIN_POLICY
    versions = plan["expected_grant_versions"]
    if type(versions) is not dict or set(versions) != {"source", "successor"}:
        raise ValidationError("Explicit source and successor grant versions are required.")
    result["expected_grant_versions"] = {}
    for role in ("source", "successor"):
        items = versions[role]
        if (type(items) is not dict or set(items) != ACTIVITY_ADMIN_PERMISSIONS
                or any(type(v) is not int or not (1 if role == "source" else 0) <= v <= 9223372036854775807
                       for v in items.values())):
            raise ValidationError("Use all eight grant versions; 0 means an absent successor grant.")
        result["expected_grant_versions"][role] = dict(sorted(items.items()))
    state_digest = plan["expected_state_sha256"]
    if (type(state_digest) is not str or len(state_digest) != 64
            or any(c not in "0123456789abcdef" for c in state_digest)):
        raise ValidationError("An approved authority state digest is required.")
    result["expected_state_sha256"] = state_digest
    result["operation_id"] = _operation(plan["operation_id"])
    for key in ("reason", "approval_reference"):
        result[key] = _text(plan[key], key)
    return result


def deployment_plans():
    enabled = getattr(settings, "ACTIVITY_ADMIN_TRANSFER_ENABLED", False)
    if enabled is False:
        raise PermissionDenied(DENIED)
    if enabled is not True:
        raise ImproperlyConfigured("Transfer deployment configuration is invalid.")
    plans = getattr(settings, "ACTIVITY_ADMIN_TRANSFER_PLANS", ())
    if type(plans) not in (tuple, list) or not plans:
        raise ImproperlyConfigured("Transfer deployment approvals are invalid.")
    try:
        normalized = [_canonical(plan) for plan in plans]
        if len({p["operation_id"] for p in normalized}) != len(normalized):
            raise ValidationError("Ambiguous operation approvals.")
    except ValidationError:
        raise ImproperlyConfigured("Transfer deployment approvals are invalid.") from None
    return normalized


def _approved(plan):
    if plan not in deployment_plans():
        raise PermissionDenied(DENIED)


def _versions(grants, registry, source_id, recipient_id):
    return {role: {name: next((row.edit_version for row in grants
        if row.user_id == identifier and row.permission_id == permission.pk), 0)
        for name, permission in sorted(registry.items())}
        for role, identifier in (("source", source_id), ("successor", recipient_id))}


def _state(activity, users, grants, registry, source_id, recipient_id, using):
    required = {permission.pk for permission in registry.values()}
    names = {permission.pk: name for name, permission in registry.items()}
    return {"schema_version": 1, "policy": ACTIVITY_ADMIN_POLICY,
        "activity_id": activity.pk, "activity_status": activity.status,
        "source_id": source_id, "recipient_id": recipient_id,
        "users": [{"id": identifier, "is_active": users[identifier].is_active,
            "direct_permission_ids": sorted(_direct(users[identifier], using) & required)}
            for identifier in sorted({source_id, recipient_id})],
        "grants": [_snapshot(row, registry[names[row.permission_id]])["grant"]
                   for row in sorted(grants, key=lambda row: row.pk)
                   if row.activity_id == activity.pk and row.user_id in {source_id, recipient_id}
                   and row.permission_id in required]}


def transfer_preconditions(*, activity_id, source_id, recipient_id, using=None):
    """Trusted read-only planning helper; not authentication or an authorization API.

    This unlocked observation may become stale immediately. The approved command
    validates every version and the entire observed state again under locks.
    """
    using = using or router.db_for_write(ActivityPermissionGrant)
    activity_id = _identifier(activity_id, "activity_id")
    source_id = _identifier(source_id, "source_id")
    recipient_id = _identifier(recipient_id, "recipient_id")
    activity = Activity.objects.using(using).filter(pk=activity_id).first()
    users = {row.pk: row for row in get_user_model().objects.using(using).filter(pk__in={source_id, recipient_id})}
    if activity is None or len(users) != 2:
        raise ValidationError("Distinct persisted users and an activity are required.")
    registry = administrator_permissions(using)
    grants = list(ActivityPermissionGrant.objects.using(using).filter(
        activity_id=activity_id, user_id__in=users))
    return {"expected_grant_versions": _versions(grants, registry, source_id, recipient_id),
        "expected_state_sha256": _digest(_state(activity, users, grants, registry, source_id, recipient_id, using))}


def _receipt(event):
    result = event.after_state["transfer_result"]
    return TransferReceipt(event.operation_id, event.pk, event.activity_id, result["source_id"], result["recipient_id"],
        tuple((item["grant_id"], item["edit_version"]) for item in result["grant_versions"]),
        tuple(result["added_global_permission_ids"]))


def _replay(operation, digest, source_id, activity_id, using):
    event = AuditEvent.objects.using(using).filter(operation_id=operation).first()
    if event is None:
        return None
    if (event.action != ACTION or event.actor_kind != "user" or event.actor_user_id != source_id
            or event.activity_id != activity_id or not isinstance(event.after_state, dict)
            or event.after_state.get("transfer_command_sha256") != digest or "transfer_result" not in event.after_state):
        raise OperationConflict("Operation UUID is bound to another command.")
    return _receipt(event)


@sensitive_variables("source_password", "successor_password")
def transfer_activity_administrator(*, source_email, source_password, successor_email,
                                    successor_password, operation_id, using=None):
    """Authenticate both users; only protected settings supply the mutation plan.

    A still-approved, authenticated source can read a committed receipt after
    handover. That read never requires or restores the source's former mandate.
    """
    plans = deployment_plans()
    operation = _operation(operation_id)
    plan = next((p for p in plans if p["operation_id"] == operation), None)
    if plan is None:
        raise PermissionDenied(DENIED)
    using = using or router.db_for_write(ActivityPermissionGrant)
    model = get_user_model()
    if not isinstance(source_email, str) or not isinstance(source_password, str) or not source_password:
        raise PermissionDenied(DENIED)
    source = authenticate(email=source_email, password=source_password)
    if (not isinstance(source, model) or source._state.adding or source._state.db != using
            or not source.is_active or source.pk != plan["actor_id"]):
        raise PermissionDenied(DENIED)
    # Password hashing happens outside row locks. Successor proof is only needed
    # for a new mutation, so an unavailable successor cannot invalidate history.
    successor = None
    if isinstance(successor_email, str) and isinstance(successor_password, str) and successor_password:
        successor = authenticate(email=successor_email, password=successor_password)
    digest = _digest(plan)
    with transaction.atomic(using=using):
        activity = Activity.objects.using(using).select_for_update().filter(pk=plan["activity_id"]).first()
        if activity is None:
            raise PermissionDenied(DENIED)
        registry = administrator_permissions(using)
        candidates = set(ActivityPermissionGrant.objects.using(using).filter(
            activity_id=activity.pk, permission_id__in=[p.pk for p in registry.values()]).values_list("user_id", flat=True))
        users = {row.pk: row for row in model.objects.using(using).select_for_update().filter(
            pk__in=candidates | {source.pk, plan["recipient_id"]}).order_by("pk")}
        current = users.get(source.pk)
        if (current is None or not current.is_active or not constant_time_compare(current.password, source.password)):
            raise PermissionDenied(DENIED)
        _approved(plan)  # Current receipt authorization precedes replay, as well as mutation.
        grants = list(ActivityPermissionGrant.objects.using(using).select_for_update().filter(
            Q(activity_id=activity.pk, user_id__in=users) | Q(user_id=plan["recipient_id"])).order_by("pk"))
        replay = _replay(UUID(operation), digest, source.pk, activity.pk, using)
        if replay is not None:
            return replay
        recipient = users.get(plan["recipient_id"])
        if (recipient is None or not recipient.is_active or not isinstance(successor, model)
                or successor._state.adding or successor._state.db != using or successor.pk != recipient.pk
                or not successor.is_active or not constant_time_compare(recipient.password, successor.password)):
            raise PermissionDenied(DENIED)
        if activity.status == Activity.Status.CLOSED:
            raise ValidationError("Normal transfer on a closed activity is disabled.")
        if source.pk not in effective_administrator_ids(activity.pk, using=using, users=users, grants=grants):
            raise PermissionDenied(DENIED)
        scoped = [row for row in grants if row.activity_id == activity.pk]
        if _versions(scoped, registry, source.pk, recipient.pk) != plan["expected_grant_versions"]:
            raise ValidationError("Approved grant versions are stale.")
        before = _state(activity, users, grants, registry, source.pk, recipient.pk, using)
        if _digest(before) != plan["expected_state_sha256"]:
            raise ValidationError("Approved authority state is stale.")
        added = sorted({p.pk for p in registry.values()} - _direct(recipient, using))
        if added and any(row.user_id == recipient.pk and row.activity_id != activity.pk for row in grants):
            raise ValidationError("Global permission additions could activate another activity scope.")
        if added:
            recipient.user_permissions.add(*[p for p in registry.values() if p.pk in added])
        # Establish the successor's entire mandate before removing any source authority.
        for name, permission in sorted(registry.items()):
            row = next((g for g in scoped if g.user_id == recipient.pk and g.permission_id == permission.pk), None)
            if row is None:
                row = ActivityPermissionGrant(user=recipient, activity=activity, permission=permission,
                                              created_by=current, is_active=True)
                row.save(using=using)
                scoped.append(row)
            elif not row.is_active:
                row.is_active = True
                row.save(using=using)
        require_administrator_continuity(activity.pk, using=using, users=users, grants=scoped,
                                         required_user_id=recipient.pk)
        for name in sorted(ACTIVITY_ADMIN_MANAGEMENT_PERMISSIONS):
            row = next(g for g in scoped if g.user_id == source.pk and g.permission_id == registry[name].pk)
            row.is_active = False
            row.save(using=using)
        administrators = require_administrator_continuity(activity.pk, using=using, users=users, grants=scoped,
                                                         required_user_id=recipient.pk)
        after = _state(activity, users, scoped, registry, source.pk, recipient.pk, using)
        after.update(approval_reference=plan["approval_reference"],
                     effective_administrator_ids=sorted(administrators), transfer_command_sha256=digest)
        before["approval_reference"] = plan["approval_reference"]
        after["transfer_result"] = {"source_id": source.pk, "recipient_id": recipient.pk,
            "grant_versions": [{"grant_id": row.pk, "edit_version": row.edit_version}
                for row in sorted(scoped, key=lambda row: row.pk) if row.user_id in {source.pk, recipient.pk}
                and row.permission_id in {p.pk for p in registry.values()}],
            "added_global_permission_ids": added}
        event = _append_audit(using, operation_id=UUID(operation), actor_kind="user", actor_user=current,
            activity=activity, action=ACTION, reason=plan["reason"], before_state=before, after_state=after,
            version_before=None, version_after=None)
        return _receipt(event)
