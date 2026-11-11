"""Scoped worklists and a separately approved session-creation command adapter."""

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_safe

from .read_access import readable_activities, readable_participations, readable_sessions
from .session_creation import create_training_session
from .session_creation_policy import creation_contexts
from .session_forms import SessionCreationForm, automatic_parameter_info

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


@never_cache
@login_required
@require_http_methods(["GET", "HEAD", "POST"])
def session_create(request):
    contexts = creation_contexts(actor=request.user)
    available = contexts.exists()
    if request.method == "POST" and not available:
        raise PermissionDenied("Pembuatan sesi belum tersedia.")
    form = SessionCreationForm(request.POST if request.method == "POST" else None, contexts=contexts)
    if request.method == "POST" and form.is_valid():
        try:
            receipt = create_training_session(
                actor=request.user, participation_id=form.cleaned_data["participation"].pk,
                data=form.parameters, operation_id=form.cleaned_data["operation_id"],
                reason=form.cleaned_data["reason"],
            )
        except ValidationError as error:
            # Context has already been checked by the scoped form queryset.
            form.add_error(None, ValidationError(error.messages))
        else:
            messages.success(request, f"Sesi {receipt.session_number} berhasil disimpan.")
            return redirect("activities:participant_sessions", activity_id=receipt.activity_id,
                            participation_id=receipt.participation_id)
    return render(request, "activities/session_create.html", {
        "form": form, "creation_available": available, "automatic": automatic_parameter_info(),
    })
