import os
import subprocess
import sys

from django.conf import settings
from django.test import Client, SimpleTestCase
from django.urls import reverse


class FoundationTests(SimpleTestCase):
    # SimpleTestCase rejects any database query, including accidental health checks.
    def test_liveness_without_database(self):
        response = self.client.get(reverse("health"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "service": "ta-skoring-django"})
        self.assertEqual(self.client.head(reverse("health")).status_code, 200)

    def test_liveness_rejects_mutation(self):
        self.assertEqual(self.client.post(reverse("health")).status_code, 405)

    def test_csrf_is_enabled(self):
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.post(reverse("admin:login")).status_code, 403)

    def test_admin_login_is_available(self):
        self.assertEqual(self.client.get(reverse("admin:login")).status_code, 200)

    def test_test_database_is_disposable(self):
        self.assertEqual(settings.DATABASES["default"]["ENGINE"], "django.db.backends.sqlite3")
        self.assertEqual(settings.TIME_ZONE, "Asia/Jakarta")
        self.assertTrue(settings.USE_TZ)


class ProductionSettingsTests(SimpleTestCase):
    def load_production(self, **overrides):
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("DJANGO_", "POSTGRES_"))
        }
        env.update(
            DJANGO_SETTINGS_MODULE="config.settings.production",
            PYTHON_DOTENV_DISABLED="1",
            # A test fixture only, not a deployed secret.
            DJANGO_SECRET_KEY="production-settings-test-fixture-abcdefghijklmnopqrstuvwxyz0123456789",
            DJANGO_ALLOWED_HOSTS="scoring.example.com",
            POSTGRES_DB="test_config_only",
            POSTGRES_USER="test_config_only",
            POSTGRES_HOST="db.example.invalid",
        )
        env.update(overrides)
        return subprocess.run(
            [
                sys.executable,
                "-c",
                "import django; django.setup(); "
                "from django.conf import settings; "
                "assert not settings.DEBUG; "
                "assert settings.SECURE_SSL_REDIRECT; "
                "assert settings.SESSION_COOKIE_SECURE; "
                "assert settings.CSRF_COOKIE_SECURE",
            ],
            cwd=settings.BASE_DIR,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def test_secure_configuration_loads_without_database_connection(self):
        result = self.load_production()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_or_development_secret_is_rejected(self):
        for secret in ("", "django-insecure-development-only-replace-before-production"):
            with self.subTest(secret=secret):
                result = self.load_production(DJANGO_SECRET_KEY=secret)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("DJANGO_SECRET_KEY", result.stderr)

    def test_missing_or_wildcard_hosts_are_rejected(self):
        for hosts in ("", "*"):
            with self.subTest(hosts=hosts):
                result = self.load_production(DJANGO_ALLOWED_HOSTS=hosts)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("DJANGO_ALLOWED_HOSTS", result.stderr)

    def test_missing_database_configuration_is_rejected(self):
        result = self.load_production(POSTGRES_DB="")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("POSTGRES_DB", result.stderr)
