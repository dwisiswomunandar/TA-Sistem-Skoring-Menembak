"""Generic admin may manage basic profiles, never security authority."""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied

from .forms import AdminAccountCreationForm, AdminAccountProfileForm, PROFILE_FIELDS, SECURITY_FIELDS
from .models import User


def reject_security_post(request, fields=SECURITY_FIELDS):
    if request.method == "POST" and (
        fields.intersection(request.POST) or {"_saveasnew", "action", "_selected_action"}.intersection(request.POST)
    ):
        raise PermissionDenied("Security administration requires an audited workflow.")


@admin.register(User)
class AccountAdmin(UserAdmin):
    form = AdminAccountProfileForm
    add_form = AdminAccountCreationForm
    actions = None
    list_editable = ()
    filter_horizontal = ()
    save_as = False
    list_display = ("email", "first_name", "last_name", "is_staff", "is_active")
    search_fields = ("email", "first_name", "last_name")
    ordering = ("email",)
    readonly_fields = (
        "email", "is_active", "is_staff", "is_superuser", "groups", "user_permissions", "last_login", "date_joined",
    )
    fieldsets = (
        (None, {"fields": ("email",)}),
        ("Personal information", {"fields": PROFILE_FIELDS}),
        ("Permissions (read only)", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("email", "first_name", "last_name", "password1", "password2")}),
    )

    def get_readonly_fields(self, request, obj=None):
        return self.readonly_fields if obj is not None else ()

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        forbidden = SECURITY_FIELDS if object_id is None else self.form.forbidden_fields
        reject_security_post(request, forbidden)
        return super().changeform_view(request, object_id, form_url, extra_context)

    def changelist_view(self, request, extra_context=None):
        reject_security_post(request)
        return super().changelist_view(request, extra_context)

    def save_model(self, request, obj, form, change):
        reject_security_post(request, SECURITY_FIELDS if not change else self.form.forbidden_fields)
        if not self.has_change_permission(request, obj if change else None):
            raise PermissionDenied
        if change:
            if obj._state.adding or obj.pk is None:
                raise PermissionDenied
            obj.save(update_fields=PROFILE_FIELDS)
        else:
            if (not self.has_add_permission(request) or not obj._state.adding or obj.pk is not None
                    or obj.is_staff or obj.is_superuser or obj.is_active is not True):
                raise PermissionDenied
            obj.save(force_insert=True)

    def save_related(self, request, form, formsets, change):
        reject_security_post(request, SECURITY_FIELDS if not change else self.form.forbidden_fields)
        if formsets or SECURITY_FIELDS.intersection(form.cleaned_data):
            raise PermissionDenied
        # The allowlisted forms have no M2M fields or inlines to save.

    def has_delete_permission(self, request, obj=None):
        return False

    def delete_model(self, request, obj):
        raise PermissionDenied

    def delete_queryset(self, request, queryset):
        raise PermissionDenied

    def user_change_password(self, request, id, form_url=""):
        # Arbitrary-account reset could take over an existing authority holder.
        # Self-service with old-password verification remains on the site route.
        raise PermissionDenied


class ReadOnlyGroupAdmin(admin.ModelAdmin):
    fields = ("name", "permissions")
    readonly_fields = fields
    search_fields = ("name",)
    actions = None

    def changelist_view(self, request, extra_context=None):
        if request.method == "POST":
            raise PermissionDenied
        return super().changelist_view(request, extra_context)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        raise PermissionDenied

    def save_related(self, request, form, formsets, change):
        raise PermissionDenied

    def delete_model(self, request, obj):
        raise PermissionDenied

    def delete_queryset(self, request, queryset):
        raise PermissionDenied


if admin.site.is_registered(Group):
    admin.site.unregister(Group)
admin.site.register(Group, ReadOnlyGroupAdmin)
