"""Versioned configuration and evidence; no inference or scoring execution."""

import json
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, router, transaction
from django.utils import timezone

from .photo_metadata import inspect_original
from .querysets import ConfigurationQuerySet, EvidenceQuerySet
from .validators import (
    validate_detection_result, validate_parameters, validate_private_key,
    validate_sha256, validate_transform_matrix,
)


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


class EvidenceRecord(models.Model):
    """Full validated writes with persisted-state guards, including stale instances."""

    created_at = models.DateTimeField(auto_now_add=True)
    objects = EvidenceQuerySet.as_manager()
    mutable_states = ()
    initial_status = None

    class Meta:
        abstract = True
        base_manager_name = "objects"
        default_manager_name = "objects"

    def prepare_save(self, original, using):
        pass

    def save(self, *, force_insert=False, force_update=False, using=None, update_fields=None):
        if update_fields is not None:
            raise ValidationError("Partial saves are disabled; save the complete evidence record.")
        using = using or router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=using):
            original = (
                type(self).objects.using(using).select_for_update().filter(pk=self.pk).first()
                if self.pk is not None else None
            )
            if original is None and not self._state.adding:
                raise ValidationError("A deleted evidence record cannot be recreated by a stale instance.")
            if original is not None and getattr(original, "status", None) not in self.mutable_states:
                raise ValidationError("This evidence record is immutable; create a new record.")
            if original is None and getattr(self, "status", None) != self.initial_status:
                raise ValidationError("Evidence must begin in its initial lifecycle state.")
            if original is not None and self.created_at != original.created_at:
                raise ValidationError({"created_at": "Evidence creation time cannot be changed."})
            self.prepare_save(original, using)
            self.full_clean()
            return super().save(force_insert=force_insert, force_update=force_update, using=using)

    def delete(self, using=None, keep_parents=False):
        if self.pk is None:
            raise ValueError("An unsaved evidence record cannot be deleted.")
        using = using or router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=using):
            original = type(self).objects.using(using).select_for_update().get(pk=self.pk)
            if getattr(original, "status", None) not in self.mutable_states:
                raise ValidationError("Historical evidence cannot be deleted.")
            return super().delete(using=using, keep_parents=keep_parents)


class TargetPhoto(EvidenceRecord):
    """Immutable original metadata, verified against the private stored bytes on insert."""

    session = models.ForeignKey(
        "activities.TrainingSession", on_delete=models.PROTECT, related_name="target_photos",
    )
    storage_key = models.CharField(max_length=512, unique=True, validators=[validate_private_key])
    sha256 = models.CharField(max_length=64, editable=False, validators=[validate_sha256])
    byte_size = models.PositiveBigIntegerField(editable=False, validators=[MinValueValidator(1)])
    width_px = models.PositiveIntegerField(editable=False, validators=[MinValueValidator(1)])
    height_px = models.PositiveIntegerField(editable=False, validators=[MinValueValidator(1)])
    verified_mime_type = models.CharField(
        max_length=32, choices=(("image/jpeg", "JPEG"), ("image/png", "PNG")), editable=False,
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="uploaded_target_photos",
    )
    preprocessing_version = models.CharField(max_length=128)
    # Analysis dimensions are explicit: no assumed resize, orientation, or EXIF policy.
    analysis_width_px = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    analysis_height_px = models.PositiveIntegerField(validators=[MinValueValidator(1)])

    class Meta(EvidenceRecord.Meta):
        abstract = False
        constraints = [
            models.CheckConstraint(condition=~models.Q(storage_key=""), name="photo_key_present"),
            models.CheckConstraint(
                condition=models.Q(sha256__regex=r"^[0-9a-f]{64}\Z"), name="photo_sha256_valid",
            ),
            models.CheckConstraint(condition=models.Q(byte_size__gt=0), name="photo_size_positive"),
            models.CheckConstraint(condition=models.Q(width_px__gt=0), name="photo_width_positive"),
            models.CheckConstraint(condition=models.Q(height_px__gt=0), name="photo_height_positive"),
            models.CheckConstraint(
                condition=models.Q(analysis_width_px__gt=0, analysis_height_px__gt=0),
                name="photo_analysis_size_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(verified_mime_type__in=("image/jpeg", "image/png")),
                name="photo_mime_valid",
            ),
            models.CheckConstraint(
                condition=~models.Q(preprocessing_version=""), name="photo_preprocessing_present",
            ),
        ]

    def clean_fields(self, exclude=None):
        if isinstance(self.preprocessing_version, str):
            self.preprocessing_version = self.preprocessing_version.strip()
        super().clean_fields(exclude=exclude)

    def prepare_save(self, original, using):
        metadata = inspect_original(self.storage_key)
        for field, verified in metadata.items():
            supplied = getattr(self, field)
            if supplied not in (None, "") and supplied != verified:
                raise ValidationError({field: "Metadata does not match the stored original."})
            setattr(self, field, verified)


class DetectionRun(EvidenceRecord):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    mutable_states = (Status.PENDING, Status.RUNNING)
    initial_status = Status.PENDING

    photo = models.ForeignKey(
        TargetPhoto, on_delete=models.PROTECT, related_name="detection_runs", db_index=False,
    )
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="triggered_detection_runs",
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    model_name = models.CharField(max_length=128)
    weights_sha256 = models.CharField(max_length=64, validators=[validate_sha256])
    runtime_version = models.CharField(max_length=128)
    adapter_version = models.CharField(max_length=128)
    # Empty parameters are permitted only when supplied explicitly by the caller.
    parameters = models.JSONField(blank=True, validators=[validate_parameters])
    raw_result = models.JSONField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True, editable=False)
    finished_at = models.DateTimeField(null=True, blank=True, editable=False)
    failure_code = models.CharField(max_length=128, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta(EvidenceRecord.Meta):
        abstract = False
        indexes = [models.Index(fields=("photo", "created_at"), name="run_photo_created_idx")]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=("pending", "running", "succeeded", "failed")),
                name="run_status_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(weights_sha256__regex=r"^[0-9a-f]{64}\Z"),
                name="run_weights_sha256_valid",
            ),
            *[
                models.CheckConstraint(condition=~models.Q(**{field: ""}), name=f"run_{field}_present")
                for field in ("model_name", "runtime_version", "adapter_version")
            ],
            models.CheckConstraint(
                condition=(
                    models.Q(status="pending", started_at__isnull=True, finished_at__isnull=True,
                             raw_result__isnull=True, failure_code="")
                    | models.Q(status="running", started_at__isnull=False, finished_at__isnull=True,
                               raw_result__isnull=True, failure_code="")
                    | models.Q(status="succeeded", started_at__isnull=False, finished_at__isnull=False,
                               raw_result__isnull=False, failure_code="")
                    | (models.Q(status="failed", finished_at__isnull=False, raw_result__isnull=True)
                       & ~models.Q(failure_code=""))
                ), name="run_lifecycle_metadata",
            ),
            models.CheckConstraint(
                condition=(models.Q(started_at__isnull=True) | models.Q(finished_at__isnull=True)
                           | models.Q(finished_at__gte=models.F("started_at"))),
                name="run_time_order",
            ),
        ]

    def clean_fields(self, exclude=None):
        for field in ("model_name", "runtime_version", "adapter_version", "failure_code"):
            value = getattr(self, field)
            if isinstance(value, str):
                setattr(self, field, value.strip())
        try:
            validate_parameters(self.parameters)
        except ValidationError as error:
            raise ValidationError({"parameters": error.messages}) from error
        super().clean_fields(exclude=exclude)

    def clean(self):
        super().clean()
        if self.raw_result is not None:
            if self.status != self.Status.SUCCEEDED:
                raise ValidationError({"raw_result": "Only succeeded runs contain detection results."})
            # Invalid FK values are reported by clean_fields(); never dereference them here.
            if isinstance(self.photo_id, int):
                photo = TargetPhoto.objects.using(self._state.db).filter(pk=self.photo_id).first()
                if photo is not None:
                    try:
                        validate_detection_result(
                            self.raw_result, width=photo.analysis_width_px, height=photo.analysis_height_px,
                        )
                    except ValidationError as error:
                        raise ValidationError({"raw_result": error.messages}) from error

    def prepare_save(self, original, using):
        if original is None:
            return
        try:
            validate_parameters(self.parameters)
        except ValidationError as error:
            raise ValidationError({"parameters": error.messages}) from error
        # Python considers True == 1; serialized JSON distinguishes their types.
        if json.dumps(self.parameters, sort_keys=True) != json.dumps(original.parameters, sort_keys=True):
            raise ValidationError({"parameters": "Run parameters cannot be changed."})
        for field in (
            "photo_id", "triggered_by_id", "model_name", "weights_sha256", "runtime_version",
            "adapter_version", "started_at",
        ):
            if getattr(self, field) != getattr(original, field):
                raise ValidationError({field: "Run provenance and its start time cannot be changed."})
        if self.status == original.status:
            return
        allowed = {
            self.Status.PENDING: (self.Status.RUNNING, self.Status.FAILED),
            self.Status.RUNNING: (self.Status.SUCCEEDED, self.Status.FAILED),
        }
        if self.status not in allowed[original.status]:
            raise ValidationError({"status": "Invalid detection run transition."})
        if self.status == self.Status.RUNNING:
            self.started_at = timezone.now()
        else:
            self.finished_at = timezone.now()


class Calibration(EvidenceRecord):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACCEPTED = "accepted", "Accepted"
        REJECTED = "rejected", "Rejected"

    mutable_states = (Status.DRAFT,)
    initial_status = Status.DRAFT

    photo = models.ForeignKey(TargetPhoto, on_delete=models.PROTECT, related_name="calibrations")
    target_version = models.ForeignKey(
        TargetVersion, on_delete=models.PROTECT, related_name="calibrations",
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    transform_matrix = models.JSONField(null=True, blank=True, validators=[validate_transform_matrix])
    method_version = models.CharField(max_length=128)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_calibrations",
    )
    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
        related_name="accepted_calibrations",
    )
    accepted_at = models.DateTimeField(null=True, blank=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta(EvidenceRecord.Meta):
        abstract = False
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=("draft", "accepted", "rejected")),
                name="calibration_status_valid",
            ),
            models.CheckConstraint(
                condition=~models.Q(method_version=""), name="calibration_method_present",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status="accepted", accepted_by__isnull=False, accepted_at__isnull=False,
                             transform_matrix__isnull=False)
                    | models.Q(status__in=("draft", "rejected"), accepted_by__isnull=True,
                               accepted_at__isnull=True)
                ), name="calibration_acceptance_metadata",
            ),
        ]

    def clean_fields(self, exclude=None):
        if isinstance(self.method_version, str):
            self.method_version = self.method_version.strip()
        # JSONField skips validators for []/{}; a supplied matrix is never an empty value.
        if self.transform_matrix is not None:
            try:
                validate_transform_matrix(self.transform_matrix)
            except ValidationError as error:
                raise ValidationError({"transform_matrix": error.messages}) from error
        super().clean_fields(exclude=exclude)

    def prepare_save(self, original, using):
        if original is None:
            return
        if self.created_by_id != original.created_by_id:
            raise ValidationError({"created_by": "Calibration authorship cannot be changed."})
        if self.status == self.Status.ACCEPTED:
            try:
                target_id = TargetVersion._meta.pk.clean(self.target_version_id, self)
            except ValidationError as error:
                raise ValidationError({"target_version": error.messages}) from error
            target = (
                TargetVersion.objects.using(using).select_for_update()
                .filter(pk=target_id).first()
            )
            if target is None or target.status != ConfigurationStatus.PUBLISHED:
                raise ValidationError({"target_version": "Acceptance requires a published target version."})
            self.accepted_at = timezone.now()
