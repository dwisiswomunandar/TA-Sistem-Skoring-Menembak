"""Keep the existing admin self-password route with bounded writes."""

from django.contrib.admin import AdminSite
from django.contrib.auth.views import PasswordChangeView
from django.urls import reverse

from accounts.forms import AdminSelfPasswordChangeForm


class SecurityBoundaryAdminSite(AdminSite):
    def password_change(self, request, extra_context=None):
        request.current_app = self.name
        return PasswordChangeView.as_view(
            form_class=AdminSelfPasswordChangeForm,
            success_url=reverse("admin:password_change_done", current_app=self.name),
            template_name=self.password_change_template or "registration/password_change_form.html",
            extra_context={**self.each_context(request), **(extra_context or {})},
        )(request)
