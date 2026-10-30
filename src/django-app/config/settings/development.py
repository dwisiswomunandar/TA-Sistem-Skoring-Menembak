"""Local development only; PostgreSQL remains explicitly configured via env."""

from .base import *  # noqa: F403

DEBUG = True
SECRET_KEY = SECRET_KEY or "django-insecure-development-only-do-not-use-in-production"
ALLOWED_HOSTS = ALLOWED_HOSTS or ["localhost", "127.0.0.1", "[::1]"]
