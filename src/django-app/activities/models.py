"""First domain iteration; operational policies awaiting observation are documented."""

from datetime import datetime
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, router
from django.utils import timezone

from .history import HistoricalParent, lock_participation_references, lock_rows


def normalize_optional_identifier(value):
    """Trim surrounding whitespace without inventing a case-folding policy."""
    if isinstance(value, str):
        return value.strip() or None
    return value


class Activity(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        CLOSED = "closed", "Closed"

    name = models.CharField(max_length=255)
    training_date = models.DateField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_activities",
    )
    entry_code = models.CharField(max_length=64, null=True, blank=True, unique=True)
    participant_access_opens_at = models.DateTimeField(null=True, blank=True)
    participant_access_closes_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(name=""), name="activity_name_present",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=("draft", "active", "closed")),
                name="activity_status_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(entry_code__isnull=True) | ~models.Q(entry_code=""),
                name="activity_entry_code_present",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(entry_code__isnull=True)
                    | models.Q(
                        participant_access_opens_at__isnull=False,
                        participant_access_closes_at__isnull=False,
                    )
                ),
                name="activity_entry_requires_window",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(participant_access_opens_at__isnull=True)
                    | models.Q(participant_access_closes_at__isnull=True)
                    | models.Q(
                        participant_access_closes_at__gt=models.F("participant_access_opens_at"),
                    )
                ),
                name="activity_access_window_order",
            ),
        ]

    def clean_fields(self, exclude=None):
        if isinstance(self.name, str):
            self.name = self.name.strip()
        self.entry_code = normalize_optional_identifier(self.entry_code)
        super().clean_fields(exclude=exclude)

    def clean(self):
        super().clean()
        errors = {}
        opens_at = self.participant_access_opens_at
        closes_at = self.participant_access_closes_at
        if self.entry_code:
            if opens_at is None:
                errors["participant_access_opens_at"] = "An entry code requires an access window."
            if closes_at is None:
                errors["participant_access_closes_at"] = "An entry code requires an access window."
        if isinstance(opens_at, datetime) and isinstance(closes_at, datetime):
            try:
                valid_order = closes_at > opens_at
            except TypeError:
                valid_order = False
            if not valid_order:
                errors["participant_access_closes_at"] = "Access must close after it opens."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class ActivityParticipation(HistoricalParent):
    class RegistrationStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        WITHDRAWN = "withdrawn", "Withdrawn"

    # The composite unique constraint already indexes the activity prefix.
    activity = models.ForeignKey(
        Activity, on_delete=models.PROTECT, related_name="participations", db_index=False,
    )
    participant = models.ForeignKey(
        "accounts.Participant", on_delete=models.PROTECT, related_name="participations",
    )
    registration_status = models.CharField(
        max_length=16, choices=RegistrationStatus.choices, default=RegistrationStatus.PENDING,
    )
    participant_identifier = models.CharField(max_length=64, null=True, blank=True)
    registered_at = models.DateTimeField(default=timezone.now)
    registered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
        related_name="registered_participations",
        help_text="Optional Django actor; NULL does not by itself prove self-registration.",
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
        related_name="decided_participations",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    status_reason = models.TextField(blank=True)
    edit_version = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta(HistoricalParent.Meta):
        abstract = False
        constraints = [
            models.UniqueConstraint(
                fields=("activity", "participant"), name="participation_activity_person_uniq",
            ),
            models.UniqueConstraint(
                fields=("activity", "participant_identifier"),
                condition=models.Q(participant_identifier__isnull=False),
                name="participation_activity_id_uniq",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    registration_status__in=("pending", "approved", "rejected", "withdrawn"),
                ),
                name="participation_status_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(edit_version__gte=1), name="participation_version_positive",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(participant_identifier__isnull=True)
                    | ~models.Q(participant_identifier="")
                ),
                name="participation_identifier_present",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(registration_status__in=("approved", "rejected"))
                    | models.Q(decided_by__isnull=False, decided_at__isnull=False)
                ),
                name="participation_decision_metadata",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(registration_status="approved")
                    | models.Q(participant_identifier__isnull=False)
                ),
                name="participation_approved_identifier",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(registration_status__in=("rejected", "withdrawn"))
                    | ~models.Q(status_reason="")
                ),
                name="participation_status_reason",
            ),
        ]
        indexes = [
            models.Index(
                fields=("activity", "registration_status"),
                name="participation_activity_state",
            ),
        ]

    def clean_fields(self, exclude=None):
        self.participant_identifier = normalize_optional_identifier(self.participant_identifier)
        if self.status_reason is None:
            self.status_reason = ""
        if isinstance(self.status_reason, str):
            self.status_reason = self.status_reason.strip()
        super().clean_fields(exclude=exclude)

    def clean(self):
        super().clean()
        errors = {}
        if self.registration_status in (
            self.RegistrationStatus.APPROVED, self.RegistrationStatus.REJECTED,
        ):
            if not self.decided_by_id:
                errors["decided_by"] = "Approval or rejection requires a decision maker."
            if self.decided_at is None:
                errors["decided_at"] = "Approval or rejection requires a decision time."
        if self.registration_status == self.RegistrationStatus.APPROVED:
            if not self.participant_identifier:
                errors["participant_identifier"] = "Approval requires an activity identifier."
        if self.registration_status in (
            self.RegistrationStatus.REJECTED, self.RegistrationStatus.WITHDRAWN,
        ) and not self.status_reason:
            errors["status_reason"] = "Rejection or withdrawal requires a reason."
        if errors:
            raise ValidationError(errors)

    def _frozen_fields(self, original, using):
        if (
            original.training_sessions.using(using).exists()
            or original.audit_events.using(using).exists()
            or original.acted_audit_events.using(using).exists()
        ):
            return ("activity", "participant")
        return ()

    def __str__(self):
        return f"Participation {self.pk} in activity {self.activity_id}"


class TrainingSession(HistoricalParent):
    class WeaponType(models.TextChoices):
        LONG_GUN = "laras_panjang", "Laras panjang"
        PISTOL = "pistol", "Pistol"

    class ShootingPosition(models.TextChoices):
        STANDING = "berdiri", "Berdiri"
        SITTING = "duduk", "Duduk"
        PRONE = "tiarap", "Tiarap"

    # Provisional baseline candidates, not a certified field standard.
    BASELINE_CALIBERS = {
        WeaponType.LONG_GUN: Decimal("5.56"),
        WeaponType.PISTOL: Decimal("9.00"),
    }

    # The composite unique constraint already indexes the participation prefix.
    participation = models.ForeignKey(
        ActivityParticipation, on_delete=models.PROTECT,
        related_name="training_sessions", db_index=False,
    )
    session_number = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    assigned_assessor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="assigned_training_sessions",
    )
    weapon_type = models.CharField(max_length=16, choices=WeaponType.choices)
    shooting_position = models.CharField(max_length=16, choices=ShootingPosition.choices)
    distance_m = models.DecimalField(
        max_digits=8, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))],
    )
    caliber_mm = models.DecimalField(
        max_digits=5, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))],
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="created_training_sessions",
    )
    edit_version = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta(HistoricalParent.Meta):
        abstract = False
        constraints = [
            models.UniqueConstraint(
                fields=("participation", "session_number"),
                name="training_participation_number_uniq",
            ),
            models.CheckConstraint(
                condition=models.Q(session_number__gte=1), name="training_number_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(edit_version__gte=1), name="training_version_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(distance_m__gt=0), name="training_distance_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(caliber_mm__gt=0), name="training_caliber_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(weapon_type__in=("laras_panjang", "pistol")),
                name="training_weapon_type_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(shooting_position__in=("berdiri", "duduk", "tiarap")),
                name="training_position_valid",
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        expected_caliber = self.BASELINE_CALIBERS.get(self.weapon_type)
        if (
            expected_caliber is not None
            and isinstance(self.caliber_mm, Decimal)
            and self.caliber_mm.is_finite()
            and self.caliber_mm != expected_caliber
        ):
            errors["caliber_mm"] = "Caliber does not match the provisional baseline weapon type."
        using = router.db_for_write(type(self), instance=self)
        original = self._stored(using)
        new_context = original is None or original.participation_id != self.participation_id
        if new_context and self.participation_id is not None:
            try:
                parent_id = self._meta.get_field("participation").target_field.to_python(self.participation_id)
            except ValidationError as error:
                raise ValidationError({"participation": error.messages}) from error
            participation = (
                ActivityParticipation.objects.using(using)
                .select_related("activity", "participant")
                .filter(pk=parent_id).first()
            )
            if participation is not None:
                if participation.activity.status != Activity.Status.ACTIVE:
                    errors["participation"] = "New sessions require an active activity."
                elif (
                    participation.registration_status
                    != ActivityParticipation.RegistrationStatus.APPROVED
                ):
                    errors["participation"] = "New sessions require approved participation."
                elif participation.participant.identity_status != "verified":
                    errors["participation"] = "New sessions require verified participant identity."
        if errors:
            raise ValidationError(errors)

    def _frozen_fields(self, original, using):
        if (
            original.target_photos.using(using).exists()
            or original.assessments.using(using).exists()
            or original.audit_events.using(using).exists()
        ):
            # Assignment/creator corrections need the future audit service; changing
            # edit_version would also invalidate the existing 04A session snapshot.
            return (
                "participation", "session_number", "weapon_type", "shooting_position",
                "distance_m", "caliber_mm", "assigned_assessor", "created_by", "edit_version",
            )
        return ()

    def _lock_context(self, original, using):
        identifiers = [self.participation_id]
        if original is not None:
            identifiers.append(original.participation_id)
        parents = lock_participation_references(identifiers, using)
        if original is None or original.participation_id != self.participation_id:
            # Eligibility parents are locked explicitly, without an implicit join lock.
            lock_rows(Activity, [parent.activity_id for parent in parents.values()], using, field_name="activity")
            person_model = ActivityParticipation._meta.get_field("participant").remote_field.model
            lock_rows(person_model, [parent.participant_id for parent in parents.values()], using, field_name="participant")

    def __str__(self):
        return f"Session {self.session_number} for participation {self.participation_id}"


class ActivityPermissionGrant(HistoricalParent):
    """Trusted maintenance API only; business provisioning awaits atomic audit services."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="activity_permission_grants")
    activity = models.ForeignKey(Activity, on_delete=models.PROTECT, related_name="permission_grants")
    permission = models.ForeignKey("auth.Permission", on_delete=models.PROTECT, related_name="activity_grants")
    is_active = models.BooleanField(default=True)
    edit_version = models.PositiveBigIntegerField(default=1, validators=[MinValueValidator(1)])
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_activity_grants")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta(HistoricalParent.Meta):
        abstract = False
        constraints = [
            models.UniqueConstraint(fields=("user", "activity", "permission"), name="activity_grant_user_scope_perm"),
            models.CheckConstraint(condition=models.Q(edit_version__gte=1), name="activity_grant_version_positive"),
        ]

    def _frozen_fields(self, original, using):
        return ("user", "activity", "permission", "created_by")

    def clean(self):
        super().clean()
        from .access_rules import GRANT_PERMISSIONS
        from django.contrib.auth.models import Permission
        permission = Permission.objects.using(router.db_for_write(type(self), instance=self)).filter(pk=self.permission_id).select_related("content_type").first()
        if permission is not None and (
            f"{permission.content_type.app_label}.{permission.codename}" not in GRANT_PERMISSIONS
            or permission.content_type.model not in ("assessment", "shotpoint")
            or not permission.codename.endswith("_" + permission.content_type.model)
        ):
            raise ValidationError({"permission": "Only registered draft domain permissions may be granted."})

    def delete(self, using=None, keep_parents=False):
        raise ValidationError("Deactivate a grant through the future audited service; grant deletion is disabled.")
