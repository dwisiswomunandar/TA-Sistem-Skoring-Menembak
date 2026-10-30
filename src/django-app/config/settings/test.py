"""Explicit, disposable test database; never inherits PostgreSQL credentials."""

from .base import *  # noqa: F403

SECRET_KEY = "django-insecure-isolated-tests-only"
DEBUG = False
ALLOWED_HOSTS = ["testserver", "localhost"]
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
