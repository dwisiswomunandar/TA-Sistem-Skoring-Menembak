"""Exercise real entry points without loading .env or opening any database."""

import json
import os
import subprocess
import sys
from textwrap import dedent

from django.conf import settings
from django.test import SimpleTestCase


class SettingsSelectionTests(SimpleTestCase):
    entry_script = dedent(
        """
        import importlib
        import json
        import runpy
        import sys
        from unittest.mock import patch

        from django.db.backends.base.base import BaseDatabaseWrapper

        entry, options = sys.argv[1], sys.argv[2:]
        with patch.object(
            BaseDatabaseWrapper,
            "connect",
            side_effect=AssertionError("Database connections are forbidden in bootstrap tests."),
        ):
            if entry == "manage":
                sys.argv = ["manage.py", "check", *options]
                runpy.run_path("manage.py", run_name="__main__")
            else:
                importlib.import_module(entry)

            from django.conf import settings

            database = settings.DATABASES["default"]
            print("SETTINGS_SNAPSHOT=" + json.dumps({
                "module": settings.SETTINGS_MODULE,
                "debug": settings.DEBUG,
                "database": {
                    "engine": database["ENGINE"],
                    "name": database["NAME"],
                    "user": database.get("USER", ""),
                    "host": database.get("HOST", ""),
                },
                "https_redirect": settings.SECURE_SSL_REDIRECT,
                "session_cookie_secure": settings.SESSION_COOKIE_SECURE,
                "csrf_cookie_secure": settings.CSRF_COOKIE_SECURE,
            }))
        """,
    )

    def run_entry_point(
        self, *, entry="manage", environment=None, cli_settings=None, missing=(),
    ):
        # Pass only operating-system essentials; do not inherit application secrets.
        env = {
            name: os.environ[name]
            for name in ("SystemRoot", "WINDIR", "PATH", "TEMP", "TMP")
            if name in os.environ
        }
        env.update(
            PYTHONDONTWRITEBYTECODE="1",
            PYTHON_DOTENV_DISABLED="1",
            DJANGO_SECRET_KEY="bootstrap-test-fixture-only-abcdefghijklmnopqrstuvwxyz0123456789",
            DJANGO_ALLOWED_HOSTS="scoring.example.invalid",
            POSTGRES_DB="bootstrap_fixture_only",
            POSTGRES_USER="bootstrap_fixture_only",
            POSTGRES_PASSWORD="bootstrap_fixture_only",
            POSTGRES_HOST="db.example.invalid",
        )
        if environment is not None:
            env["DJANGO_SETTINGS_MODULE"] = environment
        for name in missing:
            env.pop(name, None)
        options = [f"--settings={cli_settings}"] if cli_settings else []
        return subprocess.run(
            [sys.executable, "-B", "-c", self.entry_script, entry, *options],
            cwd=settings.BASE_DIR,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def snapshot(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        marker = "SETTINGS_SNAPSHOT="
        lines = [line for line in result.stdout.splitlines() if line.startswith(marker)]
        self.assertEqual(len(lines), 1, result.stdout)
        return json.loads(lines[0][len(marker):])

    def assert_selected_settings(self, result, expected_module):
        snapshot = self.snapshot(result)
        self.assertEqual(snapshot["module"], expected_module)
        self.assertEqual(snapshot["debug"], expected_module.endswith(".development"))
        if expected_module.endswith(".test"):
            self.assertEqual(
                snapshot["database"],
                {
                    "engine": "django.db.backends.sqlite3",
                    "name": ":memory:",
                    "user": "",
                    "host": "",
                },
            )
        else:
            self.assertEqual(snapshot["database"]["engine"], "django.db.backends.postgresql")
        if expected_module.endswith(".production"):
            self.assertTrue(snapshot["https_redirect"])
            self.assertTrue(snapshot["session_cookie_secure"])
            self.assertTrue(snapshot["csrf_cookie_secure"])

    def test_manage_defaults_to_development(self):
        self.assert_selected_settings(
            self.run_entry_point(), "config.settings.development",
        )

    def test_process_environment_selects_each_configuration(self):
        for mode in ("development", "production", "test"):
            module = f"config.settings.{mode}"
            with self.subTest(module=module):
                self.assert_selected_settings(
                    self.run_entry_point(environment=module), module,
                )

    def test_cli_settings_override_process_environment(self):
        for current_mode, selected_mode in (
            ("development", "production"),
            ("production", "test"),
            ("test", "development"),
        ):
            module = f"config.settings.{selected_mode}"
            with self.subTest(module=module):
                self.assert_selected_settings(
                    self.run_entry_point(
                        environment=f"config.settings.{current_mode}",
                        cli_settings=module,
                    ),
                    module,
                )

    def test_wsgi_and_asgi_default_to_production(self):
        for entry in ("config.wsgi", "config.asgi"):
            with self.subTest(entry=entry):
                self.assert_selected_settings(
                    self.run_entry_point(entry=entry), "config.settings.production",
                )

    def test_wsgi_and_asgi_honor_process_environment(self):
        for entry in ("config.wsgi", "config.asgi"):
            for mode in ("development", "test"):
                module = f"config.settings.{mode}"
                with self.subTest(entry=entry, module=module):
                    self.assert_selected_settings(
                        self.run_entry_point(entry=entry, environment=module), module,
                    )

    def test_production_fails_closed_when_required_environment_is_missing(self):
        required = (
            "DJANGO_SECRET_KEY", "DJANGO_ALLOWED_HOSTS",
            "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_HOST",
        )
        for selection in ("environment", "cli"):
            for name in required:
                with self.subTest(selection=selection, missing=name):
                    options = (
                        {"environment": "config.settings.production"}
                        if selection == "environment"
                        else {"cli_settings": "config.settings.production"}
                    )
                    result = self.run_entry_point(missing=(name,), **options)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("ImproperlyConfigured", result.stderr)
                    self.assertIn(name, result.stderr)

    def test_test_configuration_discards_postgresql_connection_values(self):
        self.assert_selected_settings(
            self.run_entry_point(
                environment="config.settings.production",
                cli_settings="config.settings.test",
                missing=("DJANGO_SECRET_KEY", "DJANGO_ALLOWED_HOSTS"),
            ),
            "config.settings.test",
        )
