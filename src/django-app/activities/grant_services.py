"""Audited single-grant commands. No bootstrap, authority delegation or global permission writes."""

import hashlib
import json
from dataclasses import dataclass
from uuid import UUID

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, router, transaction

from assessments.models import AuditEvent
from assessments.result_validators import validate_snapshot
from .access_rules import (
    ADMINISTRATIVE_PERMISSIONS, DELEGABLE_PERMISSIONS, GRANT_COMMAND_PERMISSIONS,
    permission_key, registered_permission_key,
)
from .models import Activity, ActivityPermissionGrant

DENIED = "Grant command is unavailable."


class OperationConflict(ValidationError):
    """An operation UUID belongs to a different command."""


@dataclass(frozen=True)
class GrantReceipt:
    operation_id: UUID
    audit_event_id: int
    grant_id: int
    edit_version: int
    is_active: bool


def _identifier(value, field):
    if type(value) is not int or not 0 < value <= 9223372036854775807:
        raise ValidationError({field: "Use a positive 64-bit identifier."})
    return value


def _principal(actor, using):
    model = get_user_model()
    if (not isinstance(actor, model) or not actor.is_authenticated or actor._state.adding
            or actor._state.db != using or type(actor.pk) is not int):
        raise PermissionDenied(DENIED)
    return actor.pk


def _registry(using):
    keys = {registered_permission_key(name): name for name in DELEGABLE_PERMISSIONS | ADMINISTRATIVE_PERMISSIONS}
    rows = Permission.objects.using(using).filter(
        content_type__app_label__in=("activities", "assessments"),
        codename__in=[key[2] for key in keys],
    ).select_related("content_type")
    found = {keys[permission_key(row)]: row for row in rows if permission_key(row) in keys}
    if len(found) != len(keys):
        raise PermissionDenied(DENIED)
    return found


def _direct(user, using):
    # Deliberately bypass has_perm(), superuser shortcuts and all caller caches.
    return set(user.user_permissions.using(using).values_list("pk", flat=True))


def _authorize(user, activity_id, action, permission, registry, using, grants=None):
    if user is None or not user.is_active:
        raise PermissionDenied(DENIED)
    management = registry[GRANT_COMMAND_PERMISSIONS[action]].pk
    required = {management}
    if action != "activity_grant.revoked":
        required.add(permission.pk)
    if not required <= _direct(user, using):
        raise PermissionDenied(DENIED)
    if grants is None:
        scoped = set(ActivityPermissionGrant.objects.using(using).filter(
            activity_id=activity_id, user_id=user.pk, is_active=True,
            permission_id__in=required,
        ).values_list("permission_id", flat=True))
    else:
        scoped = {row.permission_id for row in grants if row.user_id == user.pk and row.is_active}
    if not required <= scoped:
        raise PermissionDenied(DENIED)


def _snapshot(row, permission):
    fields = ("id", "user_id", "activity_id", "permission_id", "is_active", "edit_version", "created_by_id")
    value = None if row is None else {field: getattr(row, field) for field in fields}
    if row is not None:
        value.update(created_at=row.created_at.isoformat(), updated_at=row.updated_at.isoformat())
    result = {"schema_version": 1, "grant": value, "permission": {
        "id": permission.pk, "app_label": permission.content_type.app_label,
        "model": permission.content_type.model, "codename": permission.codename,
    }}
    validate_snapshot(result)
    return result


def _replay(operation_id, digest, action, actor_id, activity_id, using):
    event = AuditEvent.objects.using(using).filter(operation_id=operation_id).first()
    if event is None:
        return None
    if (event.actor_kind != "user" or event.actor_user_id != actor_id or event.activity_id != activity_id
            or event.action != action or not isinstance(event.after_state, dict)
            or event.after_state.get("grant_command_sha256") != digest
            or "grant_command_result" not in event.after_state):
        raise OperationConflict("Operation UUID is bound to another command.")
    receipt = event.after_state["grant_command_result"]
    return GrantReceipt(operation_id, event.pk, receipt["grant_id"], receipt["edit_version"], receipt["is_active"])


def _append_audit(using, **values):
    try:
        with transaction.atomic(using=using):
            event = AuditEvent(**values)
            event.save(using=using)
            return event
    except (IntegrityError, ValidationError) as error:
        if AuditEvent.objects.using(using).filter(operation_id=values["operation_id"]).exists():
            raise OperationConflict("Operation UUID is bound to another command.") from error
        raise


def _administrators(users, grants, registry, using):
    # A continuity administrator can manage and delegate every supported draft permission.
    all_ids = {row.pk for row in registry.values()}
    result = set()
    for user in users.values():
        scoped = {row.permission_id for row in grants if row.user_id == user.pk and row.is_active}
        if user.is_active and all_ids <= scoped and all_ids <= _direct(user, using):
            result.add(user.pk)
    return result


def _execute(action, *, actor, activity_id, recipient_id, permission_id,
             expected_edit_version, operation_id, reason, using=None):
    using = using or router.db_for_write(ActivityPermissionGrant)
    actor_id = _principal(actor, using)
    activity_id = _identifier(activity_id, "activity_id")
    recipient_id = _identifier(recipient_id, "recipient_id")
    permission_id = _identifier(permission_id, "permission_id")
    if recipient_id == actor_id:
        raise PermissionDenied(DENIED)
    if type(expected_edit_version) is not int or not 0 <= expected_edit_version <= 9223372036854775807:
        raise ValidationError({"expected_edit_version": "Supply an explicit nonnegative integer version."})
    issuing = action == "activity_grant.issued"
    if (issuing and expected_edit_version != 0) or (not issuing and expected_edit_version < 1):
        raise ValidationError({"expected_edit_version": "Use 0 for issue; a positive stored version otherwise."})
    if not isinstance(operation_id, (str, UUID)):
        raise ValidationError({"operation_id": "Supply a UUID."})
    try:
        operation_id = UUID(str(operation_id))
    except ValueError as error:
        raise ValidationError({"operation_id": "Supply a UUID."}) from error
    if not isinstance(reason, str) or not reason.strip():
        raise ValidationError({"reason": "An explicit nonblank reason is required."})
    reason = reason.strip()
    registry = _registry(using)
    permission = next((registry[name] for name in DELEGABLE_PERMISSIONS if registry[name].pk == permission_id), None)
    if permission is None:
        raise PermissionDenied(DENIED)
    user_model = get_user_model()
    _authorize(user_model.objects.using(using).filter(pk=actor_id).first(), activity_id,
               action, permission, registry, using)
    request = {"schema_version": 1, "action": action, "actor_user_id": actor_id,
               "activity_id": activity_id, "recipient_id": recipient_id, "permission_id": permission_id,
               "expected_edit_version": expected_edit_version, "reason": reason}
    digest = hashlib.sha256(json.dumps(request, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    with transaction.atomic(using=using):
        activity = Activity.objects.using(using).select_for_update().filter(pk=activity_id).first()
        if activity is None:
            raise PermissionDenied(DENIED)
        registry = _registry(using)
        permission = next((registry[name] for name in DELEGABLE_PERMISSIONS if registry[name].pk == permission_id), None)
        if permission is None:
            raise PermissionDenied(DENIED)
        management_ids = [registry[name].pk for name in ADMINISTRATIVE_PERMISSIONS]
        candidate_ids = set(ActivityPermissionGrant.objects.using(using).filter(
            activity_id=activity_id, permission_id__in=management_ids,
        ).values_list("user_id", flat=True))
        ids = candidate_ids | {actor_id, recipient_id}
        users = {row.pk: row for row in user_model.objects.using(using).select_for_update().filter(pk__in=ids).order_by("pk")}
        grants = list(ActivityPermissionGrant.objects.using(using).select_for_update().filter(
            activity_id=activity_id, user_id__in=ids,
        ).order_by("pk"))
        current = users.get(actor_id)
        _authorize(current, activity_id, action, permission, registry, using, grants)
        replay = _replay(operation_id, digest, action, actor_id, activity_id, using)
        if replay is not None:
            return replay
        recipient = users.get(recipient_id)
        if recipient is None:
            raise PermissionDenied(DENIED)
        if action != "activity_grant.revoked":
            if not recipient.is_active or not recipient.has_perm(f"{permission.content_type.app_label}.{permission.codename}"):
                raise PermissionDenied(DENIED)
            if activity.status == Activity.Status.CLOSED:
                raise ValidationError("New activation on a closed activity is disabled.")
        row = next((g for g in grants if g.user_id == recipient_id and g.permission_id == permission_id), None)
        if issuing:
            if row is not None:
                raise ValidationError("Grant already exists; use the appropriate lifecycle command.")
        else:
            if row is None:
                raise ValidationError("Grant is unavailable.")
            if row.edit_version != expected_edit_version:
                raise ValidationError({"expected_edit_version": "Grant version is stale."})
            if row.is_active == (action == "activity_grant.reactivated"):
                raise ValidationError("Grant is already in the requested state.")
        before = _snapshot(row, permission)
        administrators_before = _administrators(users, grants, registry, using)
        version_before = row.edit_version if row else None
        if row is None:
            row = ActivityPermissionGrant(user=recipient, activity=activity, permission=permission,
                                          created_by=current, is_active=True)
            row.save(using=using)
            grants.append(row)
        else:
            row.is_active = action == "activity_grant.reactivated"
            row.save(using=using)
        if administrators_before and not _administrators(users, grants, registry, using):
            raise ValidationError("The last effective grant administrator cannot be removed.")
        row.refresh_from_db(using=using)
        after = _snapshot(row, permission)
        after["grant_command_sha256"] = digest
        after["grant_command_result"] = {"grant_id": row.pk, "edit_version": row.edit_version, "is_active": row.is_active}
        event = _append_audit(using, operation_id=operation_id, actor_kind="user", actor_user=current,
            activity=activity, action=action, reason=reason, before_state=before, after_state=after,
            version_before=version_before, version_after=row.edit_version)
        return GrantReceipt(operation_id, event.pk, row.pk, row.edit_version, row.is_active)


def issue_grant(*, actor, activity_id, recipient_id, permission_id, expected_edit_version, operation_id, reason, using=None):
    return _execute("activity_grant.issued", actor=actor, activity_id=activity_id, recipient_id=recipient_id,
        permission_id=permission_id, expected_edit_version=expected_edit_version, operation_id=operation_id, reason=reason, using=using)


def revoke_grant(*, actor, activity_id, recipient_id, permission_id, expected_edit_version, operation_id, reason, using=None):
    return _execute("activity_grant.revoked", actor=actor, activity_id=activity_id, recipient_id=recipient_id,
        permission_id=permission_id, expected_edit_version=expected_edit_version, operation_id=operation_id, reason=reason, using=using)


def reactivate_grant(*, actor, activity_id, recipient_id, permission_id, expected_edit_version, operation_id, reason, using=None):
    return _execute("activity_grant.reactivated", actor=actor, activity_id=activity_id, recipient_id=recipient_id,
        permission_id=permission_id, expected_edit_version=expected_edit_version, operation_id=operation_id, reason=reason, using=using)
