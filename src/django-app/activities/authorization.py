"""Explicit User policies. Caller must supply a server-authenticated User, never a payload ID."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied
from django.db import router

from .access_rules import DRAFT_PERMISSIONS
from .models import ActivityPermissionGrant, TrainingSession

DENIED = "Draft access is unavailable."


def authenticated_user(actor, using, permissions, *, lock=False):
    model = get_user_model()
    if (not isinstance(actor, model) or not actor.is_authenticated or actor._state.adding
            or actor._state.db != using or type(actor.pk) is not int):
        raise PermissionDenied(DENIED)
    rows = model.objects.using(using)
    if lock:
        rows = rows.select_for_update()
    current = rows.filter(pk=actor.pk).first()
    if current is None or not current.is_active or not current.has_perms(permissions):
        raise PermissionDenied(DENIED)
    return current


def _permissions(action, using):
    if not isinstance(action, str):
        raise PermissionDenied(DENIED)
    names = DRAFT_PERMISSIONS.get(action)
    if names is None:
        raise PermissionDenied(DENIED)
    ids = list(Permission.objects.using(using).filter(
        content_type__app_label="assessments", codename__in=[p.split(".")[1] for p in names],
    ).values_list("pk", flat=True))
    if len(ids) != len(names):
        raise PermissionDenied(DENIED)
    return names, ids


def _sessions(user, permission_ids, using):
    rows = TrainingSession.objects.using(using).filter(assigned_assessor_id=user.pk)
    for permission_id in permission_ids:
        activities = ActivityPermissionGrant.objects.using(using).filter(
            user_id=user.pk, permission_id=permission_id, is_active=True,
        ).values("activity_id")
        rows = rows.filter(participation__activity_id__in=activities)
    return rows


def draft_sessions(*, actor, action, using=None):
    """Authorized session selector for this action, not a general read/download policy."""
    using = using or router.db_for_write(TrainingSession)
    names, ids = _permissions(action, using)
    user = authenticated_user(actor, using, names)
    return _sessions(user, ids, using)


def authorize_draft(*, actor, action, session_id, using=None, lock=False):
    """With lock=True, caller already holds session/participation/domain locks in 04B.1 order.

    Locks User then grant rows (PK order). Never locks joined Permission/Activity rows.
    No grant-management workflow is exposed here.
    """
    using = using or router.db_for_write(TrainingSession)
    if type(session_id) is not int or session_id <= 0:
        raise PermissionDenied(DENIED)
    names, ids = _permissions(action, using)
    user = authenticated_user(actor, using, names, lock=lock)
    session = _sessions(user, ids, using).filter(pk=session_id).first()
    if session is None:
        raise PermissionDenied(DENIED)
    if lock:
        activity_id = session.participation.activity_id
        granted = set(ActivityPermissionGrant.objects.using(using).select_for_update().filter(
            user_id=user.pk, activity_id=activity_id, permission_id__in=ids, is_active=True,
        ).order_by("pk").values_list("permission_id", flat=True))
        if not set(ids) <= granted:
            raise PermissionDenied(DENIED)
    return user
