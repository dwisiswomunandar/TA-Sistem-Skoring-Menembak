"""UI security regressions, using only a disposable test database."""

from django.contrib.auth import SESSION_KEY, get_user_model
from django.contrib.auth.models import Permission
from django.contrib.messages import INFO, add_message
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.staticfiles import finders
from django.db import connection
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Participant
from activities.models import Activity, ActivityPermissionGrant
from config.ui_views import dashboard

User = get_user_model()


class LoginTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.password = "Synthetic-Test-Password-728!"
        cls.user = User.objects.create_user("officer@example.com", cls.password)

    def login(self, **overrides):
        return self.client.post(reverse("login"), {
            "username": self.user.email, "password": self.password, **overrides,
        })

    def test_login_page_and_local_static_asset(self):
        response = self.client.get(reverse("login"))
        self.assertTemplateUsed(response, "registration/login.html")
        for content in ('lang="id"', 'name="csrfmiddlewaretoken"',
                        'autocomplete="username"', 'autocomplete="current-password"',
                        '/static/ui/app.css', 'name="viewport"'):
            self.assertContains(response, content)
        self.assertIsNotNone(finders.find("ui/app.css"))
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_existing_email_normalization_and_session_login(self):
        response = self.login(username="  OFFICER@EXAMPLE.COM  ")
        self.assertRedirects(response, reverse("dashboard"))
        self.assertEqual(self.client.session[SESSION_KEY], str(self.user.pk))

    def test_invalid_and_inactive_credentials_have_same_generic_error(self):
        response = self.login(password="wrong-password")
        self.assertContains(response, "Email atau kata sandi tidak valid.")
        self.assertNotIn(SESSION_KEY, self.client.session)
        self.user.is_active = False
        self.user.save(update_fields=("is_active",))
        response = self.login()
        self.assertContains(response, "Email atau kata sandi tidak valid.")
        self.assertNotIn(SESSION_KEY, self.client.session)
        self.assertNotContains(response, self.password)

    def test_required_fields_render_accessible_errors(self):
        response = self.client.post(reverse("login"), {})
        self.assertEqual(response.status_code, 200)
        for field in ("username", "password"):
            self.assertIn(field, response.context["form"].errors)
            self.assertContains(response, f'id="id_{field}_error"')
            self.assertContains(response, f'aria-describedby="id_{field}_error"')
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_local_next_survives_login_form(self):
        destination = reverse("dashboard") + "?from=login"
        response = self.client.get(reverse("login"), {"next": destination})
        self.assertContains(response, f'value="{destination}"')
        self.assertRedirects(self.login(next=destination), destination)

    def test_external_next_is_rejected_from_get_and_post(self):
        for destination in ("https://outside.example/", "//outside.example/", "javascript:alert(1)"):
            with self.subTest(destination=destination):
                response = self.client.get(reverse("login"), {"next": destination})
                self.assertEqual(response.context["next"], "")
                self.assertRedirects(self.login(next=destination), reverse("dashboard"))
                self.client.logout()
        response = self.client.post(reverse("login") + "?next=https://outside.example/", {
            "username": self.user.email, "password": self.password,
        })
        self.assertRedirects(response, reverse("dashboard"))

    def test_https_login_rejects_http_next(self):
        response = self.client.post(reverse("login"), {
            "username": self.user.email, "password": self.password,
            "next": "http://testserver/",
        }, secure=True)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboard"))

    def test_login_and_logout_require_csrf_and_logout_requires_post(self):
        client = Client(enforce_csrf_checks=True)
        credentials = {"username": self.user.email, "password": self.password}
        self.assertEqual(client.post(reverse("login"), credentials).status_code, 403)
        client.get(reverse("login"))
        credentials["csrfmiddlewaretoken"] = client.cookies["csrftoken"].value
        self.assertEqual(client.post(reverse("login"), credentials).status_code, 302)
        self.assertEqual(client.get(reverse("logout")).status_code, 405)
        self.assertEqual(client.post(reverse("logout")).status_code, 403)
        self.assertIn(SESSION_KEY, client.session)
        response = client.post(reverse("logout"), {
            "csrfmiddlewaretoken": client.cookies["csrftoken"].value,
        })
        self.assertRedirects(response, reverse("login"))
        self.assertNotIn(SESSION_KEY, client.session)
        self.assertRedirects(client.get(reverse("dashboard")),
                             reverse("login") + "?next=/")


class DashboardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("officer@example.com", first_name="Petugas", last_name="Uji")
        cls.other = User.objects.create_user("private-other@example.com", first_name="NamaRahasia")
        cls.activity = Activity.objects.create(
            name="KegiatanRahasia", training_date=timezone.localdate(), created_by=cls.user,
        )
        Participant.objects.create(full_name="PesertaRahasia")

    def test_anonymous_is_redirected_without_operational_queries(self):
        with self.assertNumQueries(0):
            response = self.client.get(reverse("dashboard"))
        self.assertRedirects(response, reverse("login") + "?next=/")

    def test_only_current_account_and_honest_unavailable_state(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("dashboard"))
        self.assertTemplateUsed(response, "ui/dashboard.html")
        for content in (self.user.email, "Petugas Uji", "Akses belum tersedia",
                        'aria-current="page"', 'action="/logout/"', 'method="post"'):
            self.assertContains(response, content)
        for content in (self.other.email, "NamaRahasia", "KegiatanRahasia", "PesertaRahasia"):
            self.assertNotContains(response, content)
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertIn("Cookie", response.headers["Vary"])

    def test_no_operational_reads_even_for_privileged_users_with_grants(self):
        permissions = list(Permission.objects.filter(content_type__app_label="assessments"))
        self.user.user_permissions.set(permissions)
        permission = next(p for p in permissions if p.codename == "change_assessment")
        grant = ActivityPermissionGrant.objects.create(
            activity=self.activity, user=self.user, permission=permission, created_by=self.user,
        )

        def forbid_domain_reads(execute, sql, params, many, context):
            for table in ("activities_", "assessments_", "accounts_participant"):
                self.assertNotIn(table, sql.lower(), sql)
            return execute(sql, params, many, context)

        for is_staff, is_superuser, active_grant in (
            (False, False, True), (True, False, True), (True, True, True), (True, True, False),
        ):
            with self.subTest(staff=is_staff, superuser=is_superuser, grant=active_grant):
                self.user.is_staff, self.user.is_superuser = is_staff, is_superuser
                self.user.save(update_fields=("is_staff", "is_superuser"))
                if grant.is_active != active_grant:
                    grant.is_active = active_grant
                    grant.save()
                self.client.force_login(self.user)
                with connection.execute_wrapper(forbid_domain_reads):
                    response = self.client.get(reverse("dashboard"), {"activity_id": self.activity.pk})
                    self.assertContains(response, "Akses belum tersedia")
                    self.assertNotContains(response, self.activity.name)

    def test_inactive_account_loses_dashboard_access(self):
        self.client.force_login(self.user)
        self.user.is_active = False
        self.user.save(update_fields=("is_active",))
        self.assertRedirects(self.client.get(reverse("dashboard")), reverse("login") + "?next=/")

    def test_dashboard_rejects_mutations(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(reverse("dashboard")).status_code, 405)
        self.assertEqual(self.client.head(reverse("dashboard")).status_code, 200)

    def test_profile_and_messages_are_escaped(self):
        self.user.first_name = '<script>alert("profile")</script>'
        self.user.save(update_fields=("first_name",))
        request = RequestFactory().get(reverse("dashboard"))
        request.user = self.user
        # Use the real message storage without a database-backed session request.
        request.session = {}
        request._messages = FallbackStorage(request)
        add_message(request, INFO, '<script>alert("message")</script>')
        response = dashboard(request)
        self.assertNotContains(response, "<script>")
        self.assertContains(response, "&lt;script&gt;")
        self.assertContains(response, "&quot;message&quot;")

    def test_empty_name_has_no_invented_identity(self):
        self.client.force_login(self.other)
        self.other.first_name = ""
        self.other.save(update_fields=("first_name",))
        self.assertContains(self.client.get(reverse("dashboard")), "Belum diisi")
