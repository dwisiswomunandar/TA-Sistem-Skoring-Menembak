"""Versioned continuity policy, independent of future permission registry growth.

Supplied users/grants must be fresh rows read under the caller's lock protocol.
This module takes no locks and never trusts Django's cached has_perm decisions.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError

from .models import ActivityPermissionGrant

ACTIVITY_ADMIN_POLICY = "activity_admin_v1"
ACTIVITY_ADMIN_PERMISSIONS = frozenset({
    "activities.issue_activitypermissiongrant",
    "activities.revoke_activitypermissiongrant",
    "activities.reactivate_activitypermissiongrant",
    "assessments.add_assessment",
    "assessments.change_assessment",
    "assessments.add_shotpoint",
    "assessments.change_shotpoint",
    "assessments.delete_shotpoint",
})
ACTIVITY_ADMIN_MANAGEMENT_PERMISSIONS = frozenset({
    "activities.issue_activitypermissiongrant",
    "activities.revoke_activitypermissiongrant",
    "activities.reactivate_activitypermissiongrant",
})


def administrator_permissions(using):
    """Resolve the fixed v1 content types, without deriving membership from a registry."""
    keys = {}
    for name in ACTIVITY_ADMIN_PERMISSIONS:
        app, code = name.split(".")
        model = "activitypermissiongrant" if app == "activities" else code.split("_", 1)[1]
        keys[(app, model, code)] = name
    rows = Permission.objects.using(using).filter(
        codename__in=[key[2] for key in keys],
        content_type__app_label__in=("activities", "assessments"),
    ).select_related("content_type")
    result = {keys[key]: row for row in rows
              if (key := (row.content_type.app_label, row.content_type.model, row.codename)) in keys}
    if len(result) != 8:
        raise PermissionDenied("Activity administrator policy is unavailable.")
    return result


def effective_administrator_ids(activity_id, *, using, users=None, grants=None):
    """Active + eight direct globals + eight active grants in exactly this activity.

    Without supplied rows, read current persisted users/grants. This is a policy
    check, not a synchronization primitive; mutators must lock before calling it.
    """
    required = {row.pk for row in administrator_permissions(using).values()}
    if grants is None:
        grants = list(ActivityPermissionGrant.objects.using(using).filter(activity_id=activity_id))
    else:
        grants = [row for row in grants if row.activity_id == activity_id]
    if users is None:
        users = {row.pk: row for row in get_user_model().objects.using(using).filter(
            pk__in={row.user_id for row in grants})}
    result = set()
    for user in users.values():
        if not user.is_active:
            continue
        scoped = {row.permission_id for row in grants if row.user_id == user.pk and row.is_active}
        direct = set(user.user_permissions.using(using).values_list("pk", flat=True))
        if required <= scoped and required <= direct:
            result.add(user.pk)
    return result


def require_administrator_continuity(activity_id, *, using, users, grants, required_user_id=None):
    administrators = effective_administrator_ids(activity_id, using=using, users=users, grants=grants)
    if not administrators or (required_user_id is not None and required_user_id not in administrators):
        raise ValidationError("An effective activity administrator must remain available.")
    return administrators
