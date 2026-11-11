from django.contrib import admin
from django.contrib.auth.views import LoginView, LogoutView
from django.urls import path

from accounts.ui_forms import OfficerAuthenticationForm

from .ui_views import dashboard
from .views import health

urlpatterns = [
    path("", dashboard, name="dashboard"),
    path("login/", LoginView.as_view(authentication_form=OfficerAuthenticationForm), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("health/", health, name="health"),
    path("admin/", admin.site.urls),
]
