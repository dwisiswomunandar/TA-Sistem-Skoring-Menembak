from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Lower

from .managers import UserManager


class User(AbstractUser):
    """Email identity only; operational roles await the agreed domain baseline."""

    username = None
    email = models.EmailField("email address", unique=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(Lower("email"), name="accounts_user_email_ci_unique"),
        ]

    def clean(self):
        super().clean()
        self.email = type(self).objects.normalize_email(self.email)

    def save(self, *args, **kwargs):
        self.email = type(self).objects.normalize_email(self.email)
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.email


class Participant(models.Model):
    """A participant identity independent of Django authentication."""

    class IdentityStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        VERIFIED = "verified", "Verified"

    full_name = models.CharField(max_length=255)
    identity_number = models.CharField(max_length=64, blank=True)
    identity_status = models.CharField(
        max_length=16, choices=IdentityStatus.choices, default=IdentityStatus.PENDING,
    )
    account = models.OneToOneField(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.PROTECT, related_name="participant",
    )
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.PROTECT, related_name="verified_participants",
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(full_name=""), name="participant_name_present",
            ),
            models.CheckConstraint(
                condition=models.Q(identity_status__in=("pending", "verified")),
                name="participant_status_valid",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(identity_status="verified")
                    | models.Q(verified_by__isnull=False, verified_at__isnull=False)
                ),
                name="participant_verified_metadata",
            ),
        ]

    def clean_fields(self, exclude=None):
        if isinstance(self.full_name, str):
            self.full_name = self.full_name.strip()
        if self.identity_number is None:
            self.identity_number = ""
        if isinstance(self.identity_number, str):
            self.identity_number = self.identity_number.strip()
        super().clean_fields(exclude=exclude)

    def clean(self):
        super().clean()
        if self.identity_status == self.IdentityStatus.VERIFIED:
            errors = {}
            if not self.verified_by_id:
                errors["verified_by"] = "Verified identity requires an accountable verifier."
            if self.verified_at is None:
                errors["verified_at"] = "Verified identity requires a verification time."
            if errors:
                raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.full_name
