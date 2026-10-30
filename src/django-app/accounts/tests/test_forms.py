from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.forms import AccountChangeForm, AccountCreationForm

User = get_user_model()


class AccountFormTests(TestCase):
    def creation_data(self, **overrides):
        data = {
            "email": "new@example.com",
            "password1": "Sample-Password-728!",
            "password2": "Sample-Password-728!",
        }
        data.update(overrides)
        return data

    def change_data(self, user, **overrides):
        data = {
            "email": user.email,
            "date_joined": user.date_joined.isoformat(),
            "is_active": user.is_active,
        }
        data.update(overrides)
        return data

    def test_creation_normalizes_email_and_hashes_password(self):
        form = AccountCreationForm(
            data=self.creation_data(email=" New@Example.COM "),
        )
        self.assertTrue(form.is_valid(), form.errors)
        user = form.save()
        user.refresh_from_db()
        self.assertEqual(user.email, "new@example.com")
        self.assertTrue(user.check_password("Sample-Password-728!"))

    def test_creation_rejects_missing_or_malformed_email(self):
        for email in ("", " ", "not-an-email", "user@", "@example.com"):
            with self.subTest(email=email):
                form = AccountCreationForm(data=self.creation_data(email=email))
                self.assertFalse(form.is_valid())
                self.assertIn("email", form.errors)

    def test_creation_rejects_case_variant_of_existing_email(self):
        User.objects.create_user("existing@example.com")
        form = AccountCreationForm(
            data=self.creation_data(email=" EXISTING@EXAMPLE.COM "),
        )
        self.assertFalse(form.is_valid())
        self.assertIn("email", form.errors)

    def test_creation_rejects_mismatched_passwords(self):
        form = AccountCreationForm(
            data=self.creation_data(password2="Different-Password-849!"),
        )
        self.assertFalse(form.is_valid())
        codes = {error.code for error in form.errors.as_data()["password2"]}
        self.assertIn("password_mismatch", codes)

    def test_creation_enforces_each_password_validator(self):
        for password, expected_code in (
            ("A7!", "password_too_short"),
            ("password", "password_too_common"),
            ("728194637584", "password_entirely_numeric"),
            ("fieldscorer", "password_too_similar"),
        ):
            with self.subTest(expected_code=expected_code):
                form = AccountCreationForm(
                    data=self.creation_data(
                        email="fieldscorer@example.com",
                        password1=password,
                        password2=password,
                    ),
                )
                self.assertFalse(form.is_valid())
                codes = {error.code for error in form.errors.as_data()["password2"]}
                self.assertIn(expected_code, codes)

    def test_change_normalizes_own_email_and_preserves_password_hash(self):
        user = User.objects.create_user("scorer@example.com", "Original-Password-728!")
        original_hash = user.password
        form = AccountChangeForm(
            instance=user,
            data=self.change_data(
                user,
                email=" SCORER@EXAMPLE.COM ",
                password="untrusted-form-value",
            ),
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        user.refresh_from_db()
        self.assertEqual(user.email, "scorer@example.com")
        self.assertEqual(user.password, original_hash)
        self.assertTrue(user.check_password("Original-Password-728!"))

    def test_change_rejects_another_accounts_email(self):
        User.objects.create_user("existing@example.com")
        user = User.objects.create_user("scorer@example.com")
        form = AccountChangeForm(
            instance=user,
            data=self.change_data(user, email=" EXISTING@EXAMPLE.COM "),
        )
        self.assertFalse(form.is_valid())
        self.assertIn("email", form.errors)
        user.refresh_from_db()
        self.assertEqual(user.email, "scorer@example.com")

    def test_change_rejects_malformed_email(self):
        user = User.objects.create_user("scorer@example.com")
        form = AccountChangeForm(
            instance=user,
            data=self.change_data(user, email="not-an-email"),
        )
        self.assertFalse(form.is_valid())
        self.assertIn("email", form.errors)
