"""Versioned configuration only; no inference or scoring execution."""

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, router, transaction
from django.utils import timezone

from .querysets import ConfigurationQuerySet


class ConfigurationStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    PUBLISHED = "published", "Published"
    RETIRED = "retired", "Retired"


class VersionedConfiguration(models.Model):
    """Shared lifecycle; frozen payloads require a new configuration version."""

    Status = ConfigurationStatus

    code = models.CharField(max_length=64)
    version = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    status = models.CharField(
        max_length=16, choices=ConfigurationStatus.choices, default=ConfigurationStatus.DRAFT,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="created_%(class)s_configs",
    )
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ConfigurationQuerySet.as_manager()

    class Meta:
        abstract = True
        base_manager_name = "objects"
        default_manager_name = "objects"
        constraints = [
            models.UniqueConstraint(
                fields=("code", "version"), name="%(class)s_code_version_uniq",
            ),
            models.CheckConstraint(
                condition=~models.Q(code=""), name="%(class)s_code_present",
            ),
            models.CheckConstraint(
                condition=models.Q(version__gte=1), name="%(class)s_version_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=("draft", "published", "retired")),
                name="%(class)s_status_valid",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status="draft", published_at__isnull=True)
                    | models.Q(status__in=("published", "retired"), published_at__isnull=False)
                ),
                name="%(class)s_publication_metadata",
            ),
        ]

    def clean_fields(self, exclude=None):
        if isinstance(self.code, str):
            self.code = self.code.strip()
        super().clean_fields(exclude=exclude)

    def clean(self):
        super().clean()
        if self.status in (ConfigurationStatus.PUBLISHED, ConfigurationStatus.RETIRED):
            self.validate_for_publication()

    def validate_for_publication(self):
        raise NotImplementedError

    def save(
        self, *, force_insert=False, force_update=False, using=None, update_fields=None,
    ):
        if update_fields is not None:
            raise ValidationError("Partial saves are disabled; save the complete draft configuration.")
        using = using or router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=using):
            original = (
                type(self).objects.using(using).select_for_update().filter(pk=self.pk).first()
                if self.pk is not None else None
            )
            if original is not None and original.status != ConfigurationStatus.DRAFT:
                raise ValidationError("Published or retired configurations are immutable; create a new version.")
            if original is None and self.status != ConfigurationStatus.DRAFT:
                raise ValidationError("Create a saved draft before publishing a configuration.")
            if self.status == ConfigurationStatus.RETIRED:
                raise ValidationError("Only a published configuration can be retired through retire().")
            if self.status == ConfigurationStatus.PUBLISHED:
                self.published_at = timezone.now()
            self.full_clean()
            return super().save(
                force_insert=force_insert, force_update=force_update, using=using,
            )

    def publish(self, *, using=None):
        old_status, old_time = self.status, self.published_at
        self.status = ConfigurationStatus.PUBLISHED
        try:
            self.save(using=using)
        except Exception:
            self.status, self.published_at = old_status, old_time
            raise
        return self

    def retire(self, *, using=None):
        if self.pk is None:
            raise ValidationError("An unsaved configuration cannot be retired.")
        using = using or router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=using):
            original = type(self).objects.using(using).select_for_update().get(pk=self.pk)
            if original.status != ConfigurationStatus.PUBLISHED:
                raise ValidationError("Only a published configuration can be retired.")
            # This controlled transition changes no payload or publication metadata.
            original.status = ConfigurationStatus.RETIRED
            models.Model.save(original, using=using, update_fields=("status", "updated_at"))
            self.refresh_from_db(using=using)
        return self

    def delete(self, using=None, keep_parents=False):
        if self.pk is None:
            raise ValueError("An unsaved configuration cannot be deleted.")
        using = using or router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=using):
            original = type(self).objects.using(using).select_for_update().get(pk=self.pk)
            if original.status != ConfigurationStatus.DRAFT:
                raise ValidationError("Published or retired configurations cannot be deleted.")
            return super().delete(using=using, keep_parents=keep_parents)

    def __str__(self):
        return f"{self.code} v{self.version}"


class TargetVersion(VersionedConfiguration):
    """Concentric circular rings on a rectangular physical field, measured in mm."""

    name = models.CharField(max_length=255)
    width_mm = models.DecimalField(
        max_digits=12, decimal_places=4, validators=[MinValueValidator(Decimal("0.0001"))],
    )
    height_mm = models.DecimalField(
        max_digits=12, decimal_places=4, validators=[MinValueValidator(Decimal("0.0001"))],
    )

    class Meta(VersionedConfiguration.Meta):
        abstract = False
        constraints = [
            *VersionedConfiguration.Meta.constraints,
            models.CheckConstraint(
                condition=~models.Q(name=""), name="targetversion_name_present",
            ),
            models.CheckConstraint(
                condition=models.Q(width_mm__gt=0), name="targetversion_width_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(height_mm__gt=0), name="targetversion_height_positive",
            ),
        ]

    def clean_fields(self, exclude=None):
        if isinstance(self.name, str):
            self.name = self.name.strip()
        super().clean_fields(exclude=exclude)

    def validate_for_publication(self):
        dimensions = (self.width_mm, self.height_mm)
        if not all(
            isinstance(value, Decimal) and value.is_finite() and value > 0
            for value in dimensions
        ):
            raise ValidationError("Publication requires finite, positive field dimensions in mm.")
        if self.pk is None:
            raise ValidationError("A target must have saved rings before publication.")
        using = router.db_for_write(type(self), instance=self)
        rings = list(self.rings.using(using).order_by("ring_number"))
        if not rings:
            raise ValidationError("Publication requires at least one target ring.")
        previous_radius = None
        previous_score = None
        maximum_radius = min(dimensions) / 2
        for ring in rings:
            ring.full_clean()
            if previous_radius is not None and ring.outer_radius_mm <= previous_radius:
                raise ValidationError("Ring radii must strictly increase with ring number.")
            if previous_score is not None and ring.score > previous_score:
                raise ValidationError("Scores must not increase toward outer rings; equal scores are allowed.")
            if ring.outer_radius_mm > maximum_radius:
                raise ValidationError("Each centered circular ring must fit inside the target field.")
            previous_radius, previous_score = ring.outer_radius_mm, ring.score


class TargetRing(models.Model):
    target_version = models.ForeignKey(
        TargetVersion, on_delete=models.PROTECT, related_name="rings", db_index=False,
    )
    ring_number = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    outer_radius_mm = models.DecimalField(
        max_digits=12, decimal_places=4, validators=[MinValueValidator(Decimal("0.0001"))],
    )
    score = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ConfigurationQuerySet.as_manager()

    class Meta:
        base_manager_name = "objects"
        default_manager_name = "objects"
        constraints = [
            models.UniqueConstraint(
                fields=("target_version", "ring_number"), name="targetring_version_number_uniq",
            ),
            models.CheckConstraint(
                condition=models.Q(ring_number__gte=1), name="targetring_number_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(outer_radius_mm__gt=0), name="targetring_radius_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(score__gte=1), name="targetring_score_positive",
            ),
        ]

    def _lock_draft_parents(self, using):
        if self.target_version_id is not None:
            field = self._meta.get_field("target_version")
            try:
                self.target_version_id = field.target_field.to_python(self.target_version_id)
            except ValidationError as error:
                raise ValidationError({"target_version": error.messages}) from error
        original_parent = (
            type(self).objects.using(using).filter(pk=self.pk)
            .values_list("target_version_id", flat=True).first()
            if self.pk is not None else None
        )
        parent_ids = {
            parent for parent in (original_parent, self.target_version_id) if parent is not None
        }
        parents = list(
            TargetVersion.objects.using(using).select_for_update()
            .filter(pk__in=parent_ids).order_by("pk"),
        )
        if any(parent.status != ConfigurationStatus.DRAFT for parent in parents):
            raise ValidationError("Rings belonging to published or retired targets are immutable.")
        if self.pk is not None:
            current_parent = (
                type(self).objects.using(using).select_for_update().filter(pk=self.pk)
                .values_list("target_version_id", flat=True).first()
            )
            if current_parent is not None and current_parent not in parent_ids:
                raise ValidationError("The ring changed concurrently; reload it before editing.")
        return original_parent

    def save(
        self, *, force_insert=False, force_update=False, using=None, update_fields=None,
    ):
        if update_fields is not None:
            raise ValidationError("Partial ring saves are disabled; save the complete draft ring.")
        using = using or router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=using):
            self._lock_draft_parents(using)
            self.full_clean()
            return super().save(
                force_insert=force_insert, force_update=force_update, using=using,
            )

    def delete(self, using=None, keep_parents=False):
        if self.pk is None:
            raise ValueError("An unsaved ring cannot be deleted.")
        using = using or router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=using):
            self._lock_draft_parents(using)
            return super().delete(using=using, keep_parents=keep_parents)

    def __str__(self):
        return f"Ring {self.ring_number} of target {self.target_version_id}"


class ScoringRuleVersion(VersionedConfiguration):
    """Opaque versioned policy metadata; never interprets formulas or executes code."""

    class Method(models.TextChoices):
        INWARD = "inward", "Inward"

    method = models.CharField(max_length=16, choices=Method.choices, default=Method.INWARD)
    algorithm_version = models.CharField(max_length=128, blank=True)
    boundary_policy = models.TextField(blank=True)
    numeric_precision = models.CharField(max_length=128, blank=True)

    class Meta(VersionedConfiguration.Meta):
        abstract = False
        constraints = [
            *VersionedConfiguration.Meta.constraints,
            models.CheckConstraint(
                condition=models.Q(method="inward"), name="scoringruleversion_method_valid",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status="draft")
                    | (
                        ~models.Q(algorithm_version="")
                        & ~models.Q(boundary_policy="")
                        & ~models.Q(numeric_precision="")
                    )
                ),
                name="scoringruleversion_policy_present",
            ),
        ]

    def clean_fields(self, exclude=None):
        for field in ("algorithm_version", "boundary_policy", "numeric_precision"):
            value = getattr(self, field)
            if value is None:
                setattr(self, field, "")
            elif isinstance(value, str):
                setattr(self, field, value.strip())
        super().clean_fields(exclude=exclude)

    def validate_for_publication(self):
        errors = {
            field: "Publication requires an explicit policy definition."
            for field in ("algorithm_version", "boundary_policy", "numeric_precision")
            if not getattr(self, field)
        }
        if errors:
            raise ValidationError(errors)
