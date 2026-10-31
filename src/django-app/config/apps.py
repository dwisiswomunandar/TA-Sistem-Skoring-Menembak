from django.contrib.admin.apps import AdminConfig


class SecurityBoundaryAdminConfig(AdminConfig):
    default_site = "config.admin.SecurityBoundaryAdminSite"
