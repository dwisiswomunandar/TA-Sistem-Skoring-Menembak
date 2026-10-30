"""Infrastructure endpoints only; no operational UI or database access."""

from django.http import JsonResponse
from django.views.decorators.http import require_safe


@require_safe
def health(request):
    """Liveness only: a successful response does not assert database readiness."""
    return JsonResponse({"status": "ok", "service": "ta-skoring-django"})
