from django import forms
from django.contrib.admin.forms import AdminPasswordChangeForm
from django.contrib.auth.forms import UserChangeForm, UserCreationForm
from django.core.exceptions import ValidationError

from .models import User

SECURITY_FIELDS = frozenset(("is_active", "is_staff", "is_superuser", "groups", "user_permissions"))
PROFILE_FIELDS = ("first_name", "last_name")


class AdminInputBoundary:
    forbidden_fields = SECURITY_FIELDS

    def clean(self):
        cleaned = super().clean()
        if self.is_bound and self.forbidden_fields.intersection(self.data):
            raise ValidationError("Security administration requires an audited workflow.")
        return cleaned


class AccountCreationForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("email", "first_name", "last_name")


class AccountChangeForm(UserChangeForm):
    class Meta(UserChangeForm.Meta):
        model = User
        fields = ("email", "first_name", "last_name", "password")


class AdminAccountCreationForm(AdminInputBoundary, AccountCreationForm):
    pass


class AdminAccountProfileForm(AdminInputBoundary, forms.ModelForm):
    forbidden_fields = SECURITY_FIELDS | frozenset((
        "email", "password", "password1", "password2", "usable_password",
        "new_password1", "new_password2", "date_joined", "last_login",
    ))

    class Meta:
        model = User
        fields = PROFILE_FIELDS


class AdminSelfPasswordChangeForm(AdminInputBoundary, AdminPasswordChangeForm):
    def save(self, commit=True):
        self.user.set_password(self.cleaned_data["new_password1"])
        if commit:
            # Preserve security flags changed since request.user was loaded.
            self.user.save(update_fields=("password",))
        return self.user
