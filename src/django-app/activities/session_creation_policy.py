"""Provisional approval gate, deliberately unconfigured in all shipped settings.

An approval authorizes only self-assigned creation and its minimal form context.
It does not expand ITER-09B read selectors or confer organizational roles.
"""

from django.conf import settings
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied
from django.db import models, router

from .authorization import authenticated_user
from .models import ActivityParticipation, ActivityPermissionGrant

DENIED = "Pembuatan sesi tidak tersedia untuk konteks ini."
POLICY_VERSION = "session_creation_approval_v1"
APPROVAL_FIELDS = frozenset(("user_id", "activity_id", "participation_id", "approval_reference"))


def approvals():
    rows = settings.TRAINING_SESSION_CREATION_APPROVALS
    if not isinstance(rows, (tuple, list)):
        raise PermissionDenied(DENIED)
    result = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != APPROVAL_FIELDS:
            raise PermissionDenied(DENIED)
        if any(type(row[key]) is not int or row[key] <= 0
               for key in ("user_id", "activity_id", "participation_id")):
            raise PermissionDenied(DENIED)
        reference = row["approval_reference"]
        if not isinstance(reference, str) or not reference.strip() or len(reference) > 255:
            raise PermissionDenied(DENIED)
        key = (row["user_id"], row["participation_id"])
        if key in result:
            raise PermissionDenied(DENIED)
        result[key] = {**row, "approval_reference": reference.strip()}
    return result


def _permissions(user, using):
    # Real direct/group grants only: no ModelBackend superuser shortcut/cache.
    rows = Permission.objects.using(using).filter(
        models.Q(content_type__app_label="activities", content_type__model="trainingsession",
                 codename="add_trainingsession")
        | models.Q(content_type__app_label="assessments", content_type__model="assessment",
                   codename="change_assessment"),
    ).filter(models.Q(user=user) | models.Q(group__user=user))
    ids = {row.codename: row.pk for row in rows}
    if set(ids) != {"add_trainingsession", "change_assessment"}:
        raise PermissionDenied(DENIED)
    return ids


def authorize_creation(*, actor, participation_id, using, lock=False):
    if type(participation_id) is not int or participation_id <= 0:
        raise PermissionDenied(DENIED)
    user = authenticated_user(actor, using, (), lock=lock)
    approval = approvals().get((user.pk, participation_id))
    if approval is None:
        raise PermissionDenied(DENIED)
    permissions = _permissions(user, using)
    grants = ActivityPermissionGrant.objects.using(using).filter(
        user_id=user.pk, activity_id=approval["activity_id"],
        permission_id=permissions["change_assessment"], is_active=True,
    )
    if lock:
        grants = grants.select_for_update()
    if not list(grants.order_by("pk")):
        raise PermissionDenied(DENIED)
    return user, approval


def creation_contexts(*, actor, using=None):
    """Only explicitly approved, eligible form contexts, including first sessions.

    Missing authority returns an empty queryset and does not query domain data.
    Other code errors/database failures propagate rather than becoming empty data.
    """
    using = using or router.db_for_read(ActivityParticipation)
    empty = ActivityParticipation.objects.using(using).none()
    try:
        user = authenticated_user(actor, using, ())
        plans = [row for row in approvals().values() if row["user_id"] == user.pk]
        if not plans:
            return empty
        permissions = _permissions(user, using)
    except PermissionDenied:
        return empty
    scopes = models.Q(pk__in=[])
    for plan in plans:
        scopes |= models.Q(pk=plan["participation_id"], activity_id=plan["activity_id"])
    granted = ActivityPermissionGrant.objects.using(using).filter(
        user_id=user.pk, permission_id=permissions["change_assessment"], is_active=True,
    ).values("activity_id")
    return ActivityParticipation.objects.using(using).filter(
        scopes, activity_id__in=granted, activity__status="active",
        registration_status="approved", participant__identity_status="verified",
    ).select_related("activity", "participant").only(
        "id", "activity_id", "participant_id", "participant_identifier",
        "activity__id", "activity__name", "participant__id", "participant__full_name",
    ).order_by("activity_id", "participant__full_name", "pk")
