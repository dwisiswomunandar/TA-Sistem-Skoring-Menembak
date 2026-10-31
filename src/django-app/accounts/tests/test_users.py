from django.contrib.auth import aauthenticate, authenticate, get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from accounts.forms import AccountCreationForm

User = get_user_model()


class UserTests(TestCase):
    def test_email_identity_and_password_hashing(self):
        user = User.objects.create_user(" Scorer@Example.COM ", "example-test-password")
        self.assertEqual(user.email, "scorer@example.com")
        self.assertTrue(user.check_password("example-test-password"))
        self.assertNotEqual(user.password, "example-test-password")
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertEqual(User.USERNAME_FIELD, "email")
        self.assertNotIn("username", {field.name for field in User._meta.fields})

    def test_email_login_and_inactive_account(self):
        user = User.objects.create_user("scorer@example.com", "example-test-password")
        self.assertEqual(
            authenticate(email="SCORER@EXAMPLE.COM", password="example-test-password"),
            user,
        )
        self.assertIsNone(authenticate(email=user.email, password="incorrect"))
        user.is_active = False
        user.save(update_fields=["is_active"])
        self.assertIsNone(authenticate(email=user.email, password="example-test-password"))

    def test_email_is_required(self):
        for email in ("", " ", None):
            with self.subTest(email=email), self.assertRaises(ValueError):
                User.objects.create_user(email=email)

    def test_case_insensitive_uniqueness_survives_bulk_writes(self):
        User.objects.create_user("scorer@example.com")
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.bulk_create([User(email="SCORER@example.com")])

    def test_no_password_creates_unusable_password(self):
        user = User.objects.create_user("scorer@example.com")
        self.assertFalse(user.has_usable_password())

    def test_superuser_flags(self):
        user = User.objects.create_superuser("admin@example.com", "example-test-password")
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        for flag in ("is_staff", "is_superuser"):
            with self.subTest(flag=flag), self.assertRaises(ValueError):
                User.objects.create_superuser("invalid@example.com", **{flag: False})

    def test_account_creation_form_uses_email(self):
        form = AccountCreationForm(
            data={
                "email": "new@example.com",
                "password1": "Sample-Password-728!",
                "password2": "Sample-Password-728!",
            },
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().email, "new@example.com")

    def test_admin_can_open_user_add_and_change_pages(self):
        admin = User.objects.create_superuser("admin@example.com", "example-test-password")
        self.client.force_login(admin)
        for url in (
            reverse("admin:accounts_user_add"),
            reverse("admin:accounts_user_change", args=[admin.pk]),
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                if url == reverse("admin:accounts_user_add"):
                    self.assertContains(response, 'name="email"')
                else:
                    self.assertNotContains(response, 'name="email"')
                    self.assertContains(response, admin.email)
                    self.assertContains(response, 'name="first_name"')
                self.assertNotContains(response, 'name="username"')


class AuthenticationRegressionTests(TestCase):
    email_variants = (
        "scorer@example.com",
        "SCORER@EXAMPLE.COM",
        " ScOrEr@Example.COM ",
        "\tscorer@example.com\r\n",
    )

    def setUp(self):
        self.password = "Authentication-Fixture-728!"
        self.user = User.objects.create_user("scorer@example.com", self.password)

    def test_sync_authentication_accepts_email_variants(self):
        for field in ("email", "username"):
            for email in self.email_variants:
                with self.subTest(field=field, email=email):
                    user = authenticate(**{field: email}, password=self.password)
                    self.assertEqual(user, self.user)

    async def test_async_authentication_accepts_email_variants(self):
        for field in ("email", "username"):
            for email in self.email_variants:
                with self.subTest(field=field, email=email):
                    user = await aauthenticate(**{field: email}, password=self.password)
                    self.assertEqual(user, self.user)

    async def test_async_authentication_rejects_unknown_email_and_wrong_password(self):
        for email, password in (
            ("missing@example.com", self.password),
            (" SCORER@EXAMPLE.COM ", "incorrect"),
            (" \t ", self.password),
        ):
            with self.subTest(email=email):
                self.assertIsNone(await aauthenticate(email=email, password=password))

    async def test_async_authentication_rejects_inactive_user(self):
        self.user.is_active = False
        await self.user.asave(update_fields=["is_active"])
        self.assertIsNone(
            await aauthenticate(email=" SCORER@EXAMPLE.COM ", password=self.password),
        )

    async def test_async_authentication_rejects_unusable_password(self):
        self.user.set_unusable_password()
        await self.user.asave(update_fields=["password"])
        self.assertIsNone(
            await aauthenticate(email=" SCORER@EXAMPLE.COM ", password=self.password),
        )
