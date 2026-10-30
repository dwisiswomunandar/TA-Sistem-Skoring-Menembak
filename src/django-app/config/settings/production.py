"""Production configuration fails closed when required values are missing."""

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403

if (
    len(SECRET_KEY) < 50
    or len(set(SECRET_KEY)) < 5
    or SECRET_KEY.startswith("django-insecure-")
):
    raise ImproperlyConfigured("Set a strong DJANGO_SECRET_KEY for production.")
if not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS:
    raise ImproperlyConfigured("Set explicit DJANGO_ALLOWED_HOSTS for production.")
if not all(DATABASES["default"][field] for field in ("NAME", "USER", "HOST")):
    raise ImproperlyConfigured("Set POSTGRES_DB, POSTGRES_USER and POSTGRES_HOST.")

DEBUG = False
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 3600
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
