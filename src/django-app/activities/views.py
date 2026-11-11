"""Read-only, scoped operational worklists. No business commands or mutations."""

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe

from .read_access import readable_activities, readable_participations, readable_sessions

PAGE_SIZE = 20


def _page(request, rows):
    return Paginator(rows, PAGE_SIZE).get_page(request.GET.get("page"))


@never_cache
@login_required
@require_safe
def activity_list(request):
    sessions = readable_sessions(actor=request.user)
    return render(request, "activities/activity_list.html", {
        "page_obj": _page(request, readable_activities(sessions=sessions)),
    })


@never_cache
@login_required
@require_safe
def activity_detail(request, activity_id):
    sessions = readable_sessions(actor=request.user)
    activity = get_object_or_404(readable_activities(sessions=sessions), pk=activity_id)
    return render(request, "activities/activity_detail.html", {
        "activity": activity,
        "page_obj": _page(request, readable_participations(sessions=sessions, activity_id=activity.pk)),
    })


@never_cache
@login_required
@require_safe
def participant_sessions(request, activity_id, participation_id):
    sessions = readable_sessions(actor=request.user)
    activity = get_object_or_404(readable_activities(sessions=sessions), pk=activity_id)
    participation = get_object_or_404(
        readable_participations(sessions=sessions, activity_id=activity.pk), pk=participation_id,
    )
    rows = sessions.filter(participation_id=participation.pk).order_by("session_number", "pk")
    return render(request, "activities/participant_sessions.html", {
        "activity": activity, "participation": participation, "page_obj": _page(request, rows),
    })
