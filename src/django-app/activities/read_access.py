"""Minimal worklist read policy; never grants access to assessment/evidence data.

Read only the activity, participation and session context of assigned work with
an effective change_assessment permission AND its active activity grant. Unlike
ModelBackend.has_perm, staff/superuser flags do not supply the global permission.
"""

from django.contrib.auth.models import Permission
from django.db import models, router

from .authorization import authenticated_user
from .models import Activity, ActivityParticipation, ActivityPermissionGrant, TrainingSession


def readable_sessions(*, actor, using=None):
    using = using or router.db_for_read(TrainingSession)
    # Reload the trusted authenticated actor; ignore stale permission caches.
    user = authenticated_user(actor, using, ())
    permission_ids = Permission.objects.using(using).filter(
        content_type__app_label="assessments", content_type__model="assessment",
        codename="change_assessment",
    ).filter(models.Q(user=user) | models.Q(group__user=user)).values("pk")
    grants = ActivityPermissionGrant.objects.using(using).filter(
        user_id=user.pk, is_active=True, permission_id__in=permission_ids,
    ).values("activity_id")
    return TrainingSession.objects.using(using).filter(
        assigned_assessor_id=user.pk, participation__activity_id__in=grants,
    ).only(
        "id", "participation_id", "session_number", "weapon_type",
        "shooting_position", "distance_m", "caliber_mm",
    )


def readable_activities(*, sessions):
    return Activity.objects.using(sessions.db).filter(
        pk__in=sessions.values("participation__activity_id"),
    ).only("id", "name", "training_date", "status").order_by("-training_date", "-pk")


def readable_participations(*, sessions, activity_id):
    return ActivityParticipation.objects.using(sessions.db).filter(
        activity_id=activity_id, pk__in=sessions.values("participation_id"),
    ).select_related("participant").only(
        "id", "activity_id", "participant_id", "participant_identifier",
        "registration_status", "participant__id", "participant__full_name",
    ).order_by("participant__full_name", "pk")
