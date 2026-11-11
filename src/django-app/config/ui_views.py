"""Authenticated shell only. No operational read policy exists for this UI yet."""

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe


@never_cache
@login_required
@require_safe
def dashboard(request):
    # The authenticated account is the entire read scope. Do not reuse the
    # action-specific draft selector, infer read access from grants/assignment,
    # or query operational totals (including for staff and superusers).
    return render(request, "ui/dashboard.html")
