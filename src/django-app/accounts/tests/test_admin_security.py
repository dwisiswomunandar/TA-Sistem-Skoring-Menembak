"""HTTP/admin-hook regression tests; privileged fixture ORM remains outside the boundary."""

import logging
from io import StringIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib import admin
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.forms import (
    AccountChangeForm, AdminAccountCreationForm, AdminAccountProfileForm,
    AdminSelfPasswordChangeForm, SECURITY_FIELDS,
)
from activities.models import Activity, ActivityPermissionGrant
from assessments.models import AuditEvent

User = get_user_model()


class AdminSecurityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.superuser = User.objects.create_superuser("site-admin@example.com", "Original-Admin-728!")
        cls.operator = User.objects.create_user("profile-editor@example.com", "Original-Editor-728!", is_staff=True)
        cls.operator.user_permissions.add(*Permission.objects.filter(content_type__app_label="accounts", content_type__model="user"))
        cls.operator.user_permissions.add(*Permission.objects.filter(content_type__app_label="auth", content_type__model="group"))
        cls.target = User.objects.create_user("authority-holder@example.com", "Original-Target-728!")
        cls.permission = Permission.objects.get(content_type__app_label="activities", codename="issue_activitypermissiongrant")
        cls.target.user_permissions.add(cls.permission)
        cls.group = Group.objects.create(name="Synthetic permissions")
        cls.group.permissions.add(Permission.objects.get(content_type__app_label="assessments", codename="add_assessment"))
        cls.target.groups.add(cls.group)
        activity = Activity.objects.create(name="Synthetic activity", training_date=timezone.localdate(), created_by=cls.superuser)
        ActivityPermissionGrant.objects.create(user=cls.target, activity=activity, permission=cls.permission, created_by=cls.superuser)

    def setUp(self):
        self.enterContext(patch.object(logging.getLogger("django.request"), "disabled", True))
        self.client.force_login(self.superuser)
        self.change_url = reverse("admin:accounts_user_change", args=[self.target.pk])
        self.model_admin = admin.site._registry[User]

    def state(self, user=None):
        user = user or self.target
        # last_login legitimately changes during authentication; exclude it here.
        row = User.objects.filter(pk=user.pk).values("email", "password", "is_active", "is_staff", "is_superuser", "first_name", "last_name").get()
        return (row, list(User.groups.through.objects.order_by("pk").values()),
                list(User.user_permissions.through.objects.order_by("pk").values()),
                list(Group.permissions.through.objects.order_by("pk").values()),
                list(ActivityPermissionGrant.objects.order_by("pk").values()), AuditEvent.objects.count())

    def request(self, data=None):
        request = RequestFactory().post(self.change_url, data or {})
        request.user = self.superuser
        return request

    def test_existing_form_exposes_only_profile_inputs(self):
        response = self.client.get(self.change_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.context["adminform"].form.fields), {"first_name", "last_name"})
        for field in SECURITY_FIELDS | {"email", "password"}:
            self.assertNotContains(response, f'name="{field}"')

    def test_crafted_security_post_is_rejected_even_for_superuser(self):
        for actor in (self.superuser, self.operator):
            self.client.force_login(actor)
            for field, value in (("is_active", ""), ("is_staff", "1"), ("is_superuser", "1"),
                                 ("groups", ""), ("user_permissions", "")):
                before = self.state()
                with self.subTest(actor=actor.email, field=field):
                    response = self.client.post(self.change_url, {"first_name": "Unauthorized", field: value, "_save": "1"})
                    self.assertEqual(response.status_code, 403)
                    self.assertEqual(self.state(), before)

    def test_creation_cannot_smuggle_authority_fields(self):
        url = reverse("admin:accounts_user_add")
        for field in SECURITY_FIELDS:
            before = User.objects.count()
            response = self.client.post(url, {"email": "smuggled@example.com", "password1": "New-Account-728!",
                "password2": "New-Account-728!", field: "1", "_save": "1"})
            with self.subTest(field=field):
                self.assertEqual(response.status_code, 403)
                self.assertEqual(User.objects.count(), before)

    def test_safe_account_creation_hashes_password_without_grants(self):
        self.client.force_login(self.operator)
        before = ActivityPermissionGrant.objects.count()
        response = self.client.post(reverse("admin:accounts_user_add"), {
            "email": " Normal@Example.COM ", "first_name": "Normal", "password1": "New-Account-728!",
            "password2": "New-Account-728!", "_save": "1"})
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(email="normal@example.com")
        self.assertTrue(user.check_password("New-Account-728!"))
        self.assertTrue(user.is_active)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.groups.exists())
        self.assertFalse(user.user_permissions.exists())
        self.assertEqual(ActivityPermissionGrant.objects.count(), before)

    def test_safe_profile_update_preserves_authority_and_audit_history(self):
        for actor in (self.superuser, self.operator):
            self.client.force_login(actor)
            before = self.state()
            response = self.client.post(self.change_url, {"first_name": "Updated", "last_name": "Profile", "_save": "1"})
            self.assertEqual(response.status_code, 302)
            after = self.state()
            before[0].update(first_name="Updated", last_name="Profile")
            self.assertEqual(after, before)

    def test_identity_credentials_and_dates_cannot_be_changed_through_profile_post(self):
        for field in ("email", "password", "password1", "usable_password", "new_password1", "date_joined", "last_login"):
            before = self.state()
            with self.subTest(field=field):
                self.assertEqual(self.client.post(self.change_url, {field: "untrusted", "_save": "1"}).status_code, 403)
                self.assertEqual(self.state(), before)

    def test_self_authority_post_is_denied(self):
        url = reverse("admin:accounts_user_change", args=[self.superuser.pk])
        before = self.state(self.superuser)
        self.assertEqual(self.client.post(url, {"is_superuser": "", "is_active": "", "_save": "1"}).status_code, 403)
        self.assertEqual(self.state(self.superuser), before)

    def test_arbitrary_account_password_reset_route_is_closed(self):
        for actor in (self.superuser, self.operator):
            self.client.force_login(actor)
            url = reverse("admin:auth_user_password_change", args=[self.target.pk])
            before = self.state()
            self.assertEqual(self.client.get(url).status_code, 403)
            self.assertEqual(self.client.post(url, {"password1": "Takeover-Password-728!", "password2": "Takeover-Password-728!"}).status_code, 403)
            self.assertEqual(self.state(), before)

    def test_delete_routes_are_closed_even_for_unreferenced_users(self):
        user = User.objects.create_user("unreferenced@example.com")
        url = reverse("admin:accounts_user_delete", args=[user.pk])
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, {"post": "yes"}).status_code, 403)
        self.assertTrue(User.objects.filter(pk=user.pk).exists())

    def test_bulk_actions_and_save_as_new_are_denied(self):
        before = self.state()
        count = User.objects.count()
        for action in ("delete_selected", "activate_users", "assign_permissions"):
            response = self.client.post(reverse("admin:accounts_user_changelist"), {
                "action": action, "_selected_action": [self.target.pk], "post": "yes"})
            self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.post(self.change_url, {"_saveasnew": "1", "first_name": "Clone"}).status_code, 403)
        self.assertEqual(self.state(), before)
        self.assertEqual(User.objects.count(), count)
        self.assertEqual(self.model_admin.get_actions(self.request()), {})

    def test_group_admin_is_read_only_and_cannot_change_effective_permissions(self):
        for actor in (self.superuser, self.operator):
            self.client.force_login(actor)
            change = reverse("admin:auth_group_change", args=[self.group.pk])
            response = self.client.get(change)
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.context["adminform"].form.fields)
            before = self.state()
            for url, data in ((change, {"name": "Escalated", "permissions": self.permission.pk}),
                              (reverse("admin:auth_group_add"), {"name": "Escalated", "permissions": self.permission.pk}),
                              (reverse("admin:auth_group_delete", args=[self.group.pk]), {"post": "yes"}),
                              (reverse("admin:auth_group_changelist"), {"action": "delete_selected", "_selected_action": self.group.pk})):
                self.assertEqual(self.client.post(url, data).status_code, 403)
            self.assertEqual(self.state(), before)
            self.group.refresh_from_db()
            self.assertEqual(self.group.name, "Synthetic permissions")

    def test_profile_form_and_creation_form_reject_security_payload(self):
        for field in SECURITY_FIELDS:
            form = AdminAccountProfileForm(instance=self.target, data={"first_name": "Denied", field: "1"})
            self.assertFalse(form.is_valid())
            form = AdminAccountCreationForm(data={"email": "new@example.com", "password1": "New-Account-728!", "password2": "New-Account-728!", field: "1"})
            self.assertFalse(form.is_valid())
        self.assertFalse(SECURITY_FIELDS.intersection(AccountChangeForm.base_fields))

    def test_stale_profile_save_does_not_overwrite_any_security_state(self):
        stale = User.objects.get(pk=self.target.pk)
        User.objects.filter(pk=stale.pk).update(is_active=False, is_staff=True, is_superuser=True,
            email="changed-identity@example.com", password="concurrently-replaced-password")
        stale.groups.clear()
        stale.user_permissions.clear()
        before = self.state()
        stale.first_name = "Allowed profile"
        form = SimpleNamespace(cleaned_data={"first_name": stale.first_name}, save_m2m=Mock())
        self.model_admin.save_model(self.request(), stale, form, True)
        self.model_admin.save_related(self.request(), form, [], True)
        form.save_m2m.assert_not_called()
        before[0]["first_name"] = "Allowed profile"
        self.assertEqual(self.state(), before)

    def test_direct_admin_hooks_cannot_save_security_m2m_or_delete(self):
        before = self.state()
        request = self.request({"is_staff": "1"})
        with self.assertRaises(PermissionDenied):
            self.model_admin.save_model(request, self.target, SimpleNamespace(), True)
        with self.assertRaises(PermissionDenied):
            self.model_admin.save_related(request, SimpleNamespace(), [], True)
        for callback in (lambda: self.model_admin.delete_model(self.request(), self.target),
                         lambda: self.model_admin.delete_queryset(self.request(), User.objects.all()),
                         lambda: self.model_admin.save_related(self.request(), SimpleNamespace(cleaned_data={"groups": []}), [], True)):
            with self.assertRaises(PermissionDenied):
                callback()
        self.assertEqual(self.state(), before)

    def test_unprivileged_staff_cannot_edit_profiles(self):
        actor = User.objects.create_user("staff-only@example.com", is_staff=True)
        self.client.force_login(actor)
        before = self.state()
        self.assertEqual(self.client.post(self.change_url, {"first_name": "Denied", "_save": "1"}).status_code, 403)
        self.assertEqual(self.state(), before)

    def test_self_password_change_keeps_authentication_and_authority(self):
        before = self.state(self.superuser)
        response = self.client.post(reverse("admin:password_change"), {"old_password": "Original-Admin-728!",
            "new_password1": "Changed-Own-Password-849!", "new_password2": "Changed-Own-Password-849!"})
        self.assertEqual(response.status_code, 302)
        user = authenticate(email=self.superuser.email, password="Changed-Own-Password-849!")
        self.assertEqual(user.pk, self.superuser.pk)
        after = self.state(self.superuser)
        before[0]["password"] = after[0]["password"]
        self.assertEqual(before, after)
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 200)

    def test_self_password_change_rejects_wrong_password_and_forged_flags(self):
        before = self.state(self.superuser)
        base = {"old_password": "Original-Admin-728!", "new_password1": "Changed-Own-Password-849!", "new_password2": "Changed-Own-Password-849!"}
        for data in ({**base, "old_password": "wrong"}, {**base, "is_staff": "0"}):
            self.assertEqual(self.client.post(reverse("admin:password_change"), data).status_code, 200)
            self.assertEqual(self.state(self.superuser), before)

    def test_stale_self_password_form_never_restores_security_flags(self):
        stale = User.objects.get(pk=self.superuser.pk)
        form = AdminSelfPasswordChangeForm(stale, data={"old_password": "Original-Admin-728!",
            "new_password1": "Changed-Own-Password-849!", "new_password2": "Changed-Own-Password-849!"})
        self.assertTrue(form.is_valid(), form.errors)
        User.objects.filter(pk=stale.pk).update(is_active=False, is_staff=False, is_superuser=False)
        form.save()
        current = User.objects.get(pk=stale.pk)
        self.assertFalse(current.is_active)
        self.assertFalse(current.is_staff)
        self.assertFalse(current.is_superuser)
        self.assertTrue(current.check_password("Changed-Own-Password-849!"))

    def test_csrf_and_login_are_preserved(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.superuser)
        self.assertEqual(client.post(self.change_url, {"first_name": "Denied"}).status_code, 403)
        self.assertEqual(client.post(reverse("admin:password_change"), {}).status_code, 403)
        anonymous = Client()
        self.assertEqual(anonymous.get(self.change_url).status_code, 302)
        self.assertEqual(anonymous.get(reverse("admin:login")).status_code, 200)

    def test_permission_and_grant_models_are_not_exposed_for_admin_edit(self):
        self.assertFalse(admin.site.is_registered(ActivityPermissionGrant))
        self.assertFalse(admin.site.is_registered(Permission))

    def test_createsuperuser_management_command_is_unaffected(self):
        call_command("createsuperuser", email="cli-superuser@example.com", interactive=False, stdout=StringIO())
        user = User.objects.get(email="cli-superuser@example.com")
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.is_active)
        self.assertFalse(user.activity_permission_grants.exists())
