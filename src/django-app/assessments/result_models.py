"""04A model boundary only. Official workflow/authentication/scoring await 04B."""

from decimal import Decimal
from uuid import uuid4

from asgiref.sync import sync_to_async
from django.apps import apps
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, router, transaction

from .querysets import EvidenceQuerySet, ResultQuerySet
from .result_validators import (
    same_json, validate_authentication_context, validate_historical_context, validate_snapshot,
)
from .validators import validate_sha256


def related(instance, field, using=None):
    """Read persisted relationships without trusting cached objects or malformed IDs."""
    descriptor = instance._meta.get_field(field)
    value = getattr(instance, descriptor.attname)
    if value is None:
        return None
    try:
        value = descriptor.target_field.to_python(value)
    except ValidationError as error:
        raise ValidationError({field: error.messages}) from error
    return descriptor.remote_field.model.objects.using(using or instance._state.db).filter(pk=value).first()


def context_for(session, using):
    participation = related(session, "participation", using)
    if participation is None:
        raise ValidationError({"session": "The session has no valid participation."})
    return {
        "schema_version": 1, "session_id": session.pk, "participation_id": participation.pk,
        "activity_id": participation.activity_id, "participant_id": participation.participant_id,
        "session_number": session.session_number, "session_edit_version": session.edit_version,
        "weapon_type": session.weapon_type, "shooting_position": session.shooting_position,
        "distance_m": str(session.distance_m), "caliber_mm": str(session.caliber_mm),
    }


def lock_session(session_id, using):
    model = apps.get_model("activities", "TrainingSession")
    try:
        session_id = model._meta.pk.to_python(session_id)
    except ValidationError as error:
        raise ValidationError({"session": error.messages}) from error
    session = model.objects.using(using).select_for_update().filter(pk=session_id).first()
    if session is None:
        raise ValidationError({"session": "A saved session is required."})
    return session


def check_version(assessment, expected):
    if type(expected) is not int or expected != assessment.edit_version:
        raise ValidationError({"edit_version": "A current expected_edit_version is required; reload stale data."})


class Assessment(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        VERIFIED = "verified", "Verified"
        LOCKED = "locked", "Locked"

    session = models.ForeignKey(
        "activities.TrainingSession", on_delete=models.PROTECT, related_name="assessments", db_index=False,
    )
    revision_number = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    previous_assessment = models.OneToOneField(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="next_assessment",
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    edit_version = models.PositiveBigIntegerField(default=1, validators=[MinValueValidator(1)])
    photo = models.ForeignKey(
        "assessments.TargetPhoto", null=True, blank=True, on_delete=models.PROTECT, related_name="assessments",
    )
    detection_run = models.ForeignKey(
        "assessments.DetectionRun", null=True, blank=True, on_delete=models.PROTECT, related_name="assessments",
    )
    calibration = models.ForeignKey(
        "assessments.Calibration", null=True, blank=True, on_delete=models.PROTECT, related_name="assessments",
    )
    scoring_rule_version = models.ForeignKey(
        "assessments.ScoringRuleVersion", null=True, blank=True, on_delete=models.PROTECT, related_name="assessments",
    )
    total_score = models.PositiveBigIntegerField(null=True, blank=True, editable=False)
    notes = models.TextField(blank=True)
    revision_reason = models.TextField(blank=True)
    historical_context = models.JSONField(editable=False, validators=[validate_historical_context])
    scoring_context = models.JSONField(null=True, blank=True, editable=False, validators=[validate_snapshot])
    scoring_engine_version = models.CharField(max_length=128, blank=True, editable=False)
    scoring_input_sha256 = models.CharField(max_length=64, blank=True, editable=False, validators=[validate_sha256])
    scored_at = models.DateTimeField(null=True, blank=True, editable=False)
    scored_edit_version = models.PositiveBigIntegerField(null=True, blank=True, editable=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_assessments",
    )
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="verified_assessments",
    )
    locked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="locked_assessments",
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    locked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ResultQuerySet.as_manager()

    class Meta:
        base_manager_name = "objects"
        default_manager_name = "objects"
        indexes = [models.Index(fields=("status", "locked_at"), name="assessment_status_locked_idx")]
        constraints = [
            models.UniqueConstraint(fields=("session", "revision_number"), name="assessment_session_revision_uniq"),
            models.UniqueConstraint(fields=("session",), condition=models.Q(status__in=("draft", "verified")),
                                    name="assessment_one_working_revision"),
            models.CheckConstraint(condition=models.Q(revision_number__gte=1), name="assessment_revision_positive"),
            models.CheckConstraint(condition=models.Q(edit_version__gte=1), name="assessment_edit_version_positive"),
            models.CheckConstraint(condition=models.Q(status__in=("draft", "verified", "locked")), name="assessment_status_valid"),
            models.CheckConstraint(
                condition=(models.Q(revision_number=1, previous_assessment__isnull=True)
                           | models.Q(revision_number__gt=1, previous_assessment__isnull=False)),
                name="assessment_predecessor_presence",
            ),
            models.CheckConstraint(condition=~models.Q(previous_assessment=models.F("pk")), name="assessment_not_own_predecessor"),
            models.CheckConstraint(condition=models.Q(total_score__isnull=True) | models.Q(total_score__gte=0), name="assessment_total_nonnegative"),
            models.CheckConstraint(
                condition=(
                    models.Q(status="draft", verified_by__isnull=True, verified_at__isnull=True, locked_by__isnull=True, locked_at__isnull=True)
                    | models.Q(status="verified", verified_by__isnull=False, verified_at__isnull=False, locked_by__isnull=True, locked_at__isnull=True)
                    | models.Q(status="locked", verified_by__isnull=False, verified_at__isnull=False, locked_by__isnull=False, locked_at__isnull=False)
                ), name="assessment_lifecycle_metadata",
            ),
            models.CheckConstraint(
                condition=models.Q(status="draft") | models.Q(photo__isnull=False, calibration__isnull=False, scoring_rule_version__isnull=False, total_score__isnull=False),
                name="assessment_official_inputs_present",
            ),
            models.CheckConstraint(
                condition=(models.Q(detection_run__isnull=True, calibration__isnull=True) | models.Q(photo__isnull=False)),
                name="assessment_evidence_requires_photo",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(total_score__isnull=True, scored_at__isnull=True, scored_edit_version__isnull=True,
                             scoring_context__isnull=True, scoring_engine_version="", scoring_input_sha256="")
                    | (models.Q(total_score__isnull=False, scored_at__isnull=False, scoring_context__isnull=False,
                                scored_edit_version=models.F("edit_version"), scored_edit_version__isnull=False)
                       & ~models.Q(scoring_engine_version="") & models.Q(scoring_input_sha256__regex=r"^[0-9a-f]{64}\Z"))
                ), name="assessment_scoring_provenance",
            ),
            models.CheckConstraint(condition=models.Q(verified_at__isnull=True) | models.Q(verified_at__gte=models.F("created_at")), name="assessment_verification_time"),
            models.CheckConstraint(condition=models.Q(locked_at__isnull=True) | models.Q(locked_at__gte=models.F("verified_at")), name="assessment_lock_time"),
        ]

    def clean_fields(self, exclude=None):
        for field in ("notes", "revision_reason", "scoring_engine_version", "scoring_input_sha256"):
            value = getattr(self, field)
            if value is None:
                setattr(self, field, "")
            elif isinstance(value, str):
                setattr(self, field, value.strip())
        for field, validator in (("historical_context", validate_historical_context), ("scoring_context", validate_snapshot)):
            if field == "scoring_context" and getattr(self, field) is None:
                continue
            try:
                validator(getattr(self, field))
            except ValidationError as error:
                raise ValidationError({field: error.messages}) from error
        super().clean_fields(exclude=exclude)

    def validate_evidence(self, using=None):
        photo = related(self, "photo", using)
        run = related(self, "detection_run", using)
        calibration = related(self, "calibration", using)
        rule = related(self, "scoring_rule_version", using)
        errors = {}
        if photo is not None and photo.session_id != self.session_id:
            errors["photo"] = "Photo and assessment must belong to the same session."
        if run is not None and (run.photo_id != self.photo_id or run.status != "succeeded"):
            errors["detection_run"] = "Select a succeeded run of the assessment photo."
        if calibration is not None and (calibration.photo_id != self.photo_id or calibration.status != "accepted"):
            errors["calibration"] = "Select an accepted calibration of the assessment photo."
        if rule is not None and rule.status not in ("published", "retired"):
            errors["scoring_rule_version"] = "A scoring rule must have been published."
        if errors:
            raise ValidationError(errors)
        return photo

    def clean(self):
        super().clean()
        self.validate_evidence()
        previous = related(self, "previous_assessment")
        if previous is not None:
            if (previous.session_id != self.session_id or previous.status != self.Status.LOCKED
                    or previous.revision_number + 1 != self.revision_number):
                raise ValidationError({"previous_assessment": "Predecessor must be the preceding locked revision in this session."})
            if not self.revision_reason:
                raise ValidationError({"revision_reason": "A new revision requires an explicit reason."})
        if self.status != self.Status.DRAFT and self.pk is not None:
            points = list(self.shot_points.all())
            if any(point.is_included and point.score is None for point in points):
                raise ValidationError({"total_score": "Every included point requires a server calculation."})
            if any(not point.is_included and point.score is not None for point in points):
                raise ValidationError({"total_score": "Excluded points must have NULL scores."})
            if self.total_score != sum(point.score for point in points if point.is_included and point.score is not None):
                raise ValidationError({"total_score": "Total must equal the included point scores."})

    def assert_unscored(self):
        if (
            self.total_score is not None or self.scoring_context is not None
            or self.scoring_engine_version or self.scoring_input_sha256
            or self.scored_at is not None or self.scored_edit_version is not None
        ):
            raise ValidationError("Scoring writes are disabled until the trusted scoring workflow is implemented.")

    def save(self, *, force_insert=False, force_update=False, using=None, update_fields=None, expected_edit_version=None):
        if update_fields is not None:
            raise ValidationError("Partial assessment writes are disabled.")
        using = using or router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=using):
            session = lock_session(self.session_id, using)
            original = type(self).objects.using(using).select_for_update().filter(pk=self.pk).first() if self.pk is not None else None
            if original is None and not self._state.adding:
                raise ValidationError("A stale assessment cannot recreate a deleted record.")
            if self.status != self.Status.DRAFT or (original is not None and original.status != self.Status.DRAFT):
                raise ValidationError("Verification, locking and reopening require the unavailable trusted workflow.")
            self.assert_unscored()
            snapshot = context_for(session, using)
            if original is None:
                if force_update:
                    raise ValidationError("An unsaved assessment cannot be force-updated.")
                if self.revision_number != 1 or self.previous_assessment_id is not None or self.edit_version != 1:
                    raise ValidationError("04A permits only initial unscored drafts; revisions require the audited 04B workflow.")
                if self.historical_context is not None and not same_json(self.historical_context, snapshot):
                    raise ValidationError({"historical_context": "Historical context must be generated from the persisted session."})
                self.historical_context = snapshot
            else:
                check_version(original, expected_edit_version)
                if self.edit_version != original.edit_version:
                    raise ValidationError({"edit_version": "The model version is server-managed."})
                for field in ("session_id", "revision_number", "previous_assessment_id", "created_by_id", "created_at"):
                    if getattr(self, field) != getattr(original, field):
                        raise ValidationError({field: "Assessment identity and authorship are immutable."})
                if not same_json(self.historical_context, original.historical_context) or not same_json(snapshot, original.historical_context):
                    raise ValidationError({"historical_context": "Historical context changed; session protection requires iteration 04B."})
                if self.shot_points.using(using).exists() and any(
                    getattr(self, field) != getattr(original, field) for field in ("photo_id", "detection_run_id", "calibration_id", "scoring_rule_version_id")
                ):
                    raise ValidationError("Evidence cannot be switched while points exist; an audited draft reset is required.")
            old_version = self.edit_version
            if original is not None:
                self.edit_version = original.edit_version + 1
            try:
                self.full_clean()
                return super().save(force_insert=force_insert or original is None, force_update=force_update, using=using)
            except Exception:
                self.edit_version = old_version
                raise

    def delete(self, using=None, keep_parents=False):
        raise ValidationError("Assessment deletion requires a separately specified audited retention workflow.")

    async def asave(self, **kwargs):
        return await sync_to_async(self.save)(**kwargs)


class ShotPoint(models.Model):
    class Source(models.TextChoices):
        AI = "ai", "AI"
        MANUAL = "manual", "Manual"

    assessment = models.ForeignKey(Assessment, on_delete=models.PROTECT, related_name="shot_points", db_index=False)
    point_number = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    x_px = models.DecimalField(max_digits=16, decimal_places=6, validators=[MinValueValidator(0), MaxValueValidator(Decimal("9999999999.999999"))])
    y_px = models.DecimalField(max_digits=16, decimal_places=6, validators=[MinValueValidator(0), MaxValueValidator(Decimal("9999999999.999999"))])
    source = models.CharField(max_length=16, choices=Source.choices)
    origin_detection_id = models.TextField(blank=True)
    is_included = models.BooleanField(default=True)
    exclusion_reason = models.TextField(blank=True)
    score = models.PositiveIntegerField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = EvidenceQuerySet.as_manager()

    class Meta:
        base_manager_name = "objects"
        default_manager_name = "objects"
        constraints = [
            models.UniqueConstraint(fields=("assessment", "point_number"), name="point_assessment_number_uniq"),
            models.UniqueConstraint(fields=("assessment", "origin_detection_id"), condition=models.Q(source="ai"), name="point_assessment_detection_uniq"),
            models.CheckConstraint(condition=models.Q(point_number__gte=1), name="point_number_positive"),
            models.CheckConstraint(condition=models.Q(x_px__gte=0, x_px__lte=Decimal("9999999999.999999")), name="point_x_finite_range"),
            models.CheckConstraint(condition=models.Q(y_px__gte=0, y_px__lte=Decimal("9999999999.999999")), name="point_y_finite_range"),
            models.CheckConstraint(condition=models.Q(source__in=("ai", "manual")), name="point_source_valid"),
            models.CheckConstraint(condition=(models.Q(source="manual", origin_detection_id="") | (models.Q(source="ai") & ~models.Q(origin_detection_id=""))), name="point_source_provenance"),
            models.CheckConstraint(condition=models.Q(is_included=True) | ~models.Q(exclusion_reason=""), name="point_exclusion_reason"),
            models.CheckConstraint(condition=models.Q(score__isnull=True) | models.Q(score__gte=0), name="point_score_nonnegative"),
            models.CheckConstraint(condition=models.Q(is_included=True) | models.Q(score__isnull=True), name="point_excluded_score_null"),
        ]

    def clean_fields(self, exclude=None):
        if self.origin_detection_id is None:
            self.origin_detection_id = ""
        if self.exclusion_reason is None:
            self.exclusion_reason = ""
        elif isinstance(self.exclusion_reason, str):
            self.exclusion_reason = self.exclusion_reason.strip()
        for field in ("x_px", "y_px"):
            if isinstance(getattr(self, field), bool):
                raise ValidationError({field: "A coordinate must be a number, not a boolean."})
        super().clean_fields(exclude=exclude)

    def clean(self):
        super().clean()
        assessment = related(self, "assessment")
        if assessment is None:
            return
        photo = assessment.validate_evidence()
        if photo is None:
            raise ValidationError({"assessment": "A point requires an assessment photo."})
        for field, dimension in (("x_px", photo.analysis_width_px), ("y_px", photo.analysis_height_px)):
            value = getattr(self, field)
            if isinstance(value, Decimal) and (not value.is_finite() or not 0 <= value < dimension):
                raise ValidationError({field: "Coordinates must be finite and inside the analysis image."})
        if self.source == self.Source.AI:
            run = related(assessment, "detection_run")
            if run is None or run.status != "succeeded":
                raise ValidationError({"origin_detection_id": "AI points require a succeeded selected run."})
            detections = run.raw_result.get("detections", []) if isinstance(run.raw_result, dict) else []
            if not any(isinstance(item, dict) and item.get("id") == self.origin_detection_id for item in detections):
                raise ValidationError({"origin_detection_id": "The detection does not exist in the selected run."})

    def _locked_parent(self, using, expected):
        parent = related(self, "assessment", using)
        if parent is None:
            raise ValidationError({"assessment": "A saved assessment is required."})
        session = lock_session(parent.session_id, using)
        parent = Assessment.objects.using(using).select_for_update().get(pk=parent.pk)
        if parent.status != Assessment.Status.DRAFT:
            raise ValidationError("Points belonging to verified or locked assessments are immutable.")
        check_version(parent, expected)
        if not same_json(context_for(session, using), parent.historical_context):
            raise ValidationError("Session context drift must be resolved through the future audited workflow.")
        parent.validate_evidence(using)
        return parent

    @staticmethod
    def _bump_parent(parent, using):
        parent.edit_version += 1
        parent.total_score, parent.scoring_context = None, None
        parent.scoring_engine_version = parent.scoring_input_sha256 = ""
        parent.scored_at = parent.scored_edit_version = None
        parent.full_clean()
        # Internal model write only: no official state or score is introduced.
        models.Model.save(parent, using=using)

    def save(self, *, force_insert=False, force_update=False, using=None, update_fields=None, expected_edit_version=None):
        if update_fields is not None:
            raise ValidationError("Partial point writes are disabled.")
        if self.score is not None:
            raise ValidationError("Point scores require the unavailable trusted scoring workflow.")
        using = using or router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=using):
            parent = self._locked_parent(using, expected_edit_version)
            original = type(self).objects.using(using).select_for_update().filter(pk=self.pk).first() if self.pk is not None else None
            if original is None and not self._state.adding:
                raise ValidationError("A stale point cannot recreate a deleted record.")
            if original is None and force_update:
                raise ValidationError("An unsaved point cannot be force-updated.")
            if original is not None:
                for field in ("assessment_id", "point_number", "source", "origin_detection_id", "created_at"):
                    if getattr(self, field) != getattr(original, field):
                        raise ValidationError({field: "Point parent, numbering and original provenance cannot be changed."})
            self.full_clean()
            result = super().save(force_insert=force_insert or original is None, force_update=force_update, using=using)
            self._bump_parent(parent, using)
            return result

    async def asave(self, **kwargs):
        return await sync_to_async(self.save)(**kwargs)

    async def adelete(self, **kwargs):
        return await sync_to_async(self.delete)(**kwargs)

    def delete(self, using=None, keep_parents=False, *, expected_edit_version=None):
        using = using or router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=using):
            parent = self._locked_parent(using, expected_edit_version)
            original = type(self).objects.using(using).select_for_update().get(pk=self.pk)
            if original.assessment_id != parent.pk:
                raise ValidationError("The original point parent cannot be changed before deletion.")
            result = super().delete(using=using, keep_parents=keep_parents)
            self._bump_parent(parent, using)
            return result


class AuditEvent(models.Model):
    class ActorKind(models.TextChoices):
        USER = "user", "User"
        PARTICIPANT = "participant", "Authenticated participant principal"
        SYSTEM = "system", "System"

    operation_id = models.UUIDField(default=uuid4, unique=True, editable=False)
    actor_kind = models.CharField(max_length=16, choices=ActorKind.choices)
    actor_user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="audit_events")
    actor_participation = models.ForeignKey("activities.ActivityParticipation", null=True, blank=True, on_delete=models.PROTECT, related_name="acted_audit_events")
    system_component = models.CharField(max_length=128, blank=True)
    system_version = models.CharField(max_length=128, blank=True)
    authentication_context = models.JSONField(null=True, blank=True, validators=[validate_authentication_context])
    activity = models.ForeignKey("activities.Activity", null=True, blank=True, on_delete=models.PROTECT, related_name="audit_events", db_index=False)
    participation = models.ForeignKey("activities.ActivityParticipation", null=True, blank=True, on_delete=models.PROTECT, related_name="audit_events", db_index=False)
    session = models.ForeignKey("activities.TrainingSession", null=True, blank=True, on_delete=models.PROTECT, related_name="audit_events", db_index=False)
    assessment = models.ForeignKey(Assessment, null=True, blank=True, on_delete=models.PROTECT, related_name="audit_events", db_index=False)
    caused_by_event = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="caused_events")
    action = models.CharField(max_length=128)
    reason = models.TextField(blank=True)
    before_state = models.JSONField(validators=[validate_snapshot])
    after_state = models.JSONField(validators=[validate_snapshot])
    version_before = models.PositiveBigIntegerField(null=True, blank=True)
    version_after = models.PositiveBigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = EvidenceQuerySet.as_manager()

    class Meta:
        base_manager_name = "objects"
        default_manager_name = "objects"
        indexes = [models.Index(fields=(scope, "created_at", "id"), name=f"audit_{scope}_time_idx") for scope in ("activity", "participation", "session", "assessment")]
        constraints = [
            models.CheckConstraint(condition=models.Q(actor_kind__in=("user", "participant", "system")), name="audit_actor_kind_valid"),
            models.CheckConstraint(
                condition=(
                    models.Q(actor_kind="user", actor_user__isnull=False, actor_participation__isnull=True, system_component="", system_version="", authentication_context__isnull=True)
                    | models.Q(actor_kind="participant", actor_user__isnull=True, actor_participation__isnull=False, system_component="", system_version="", authentication_context__isnull=False)
                    | (models.Q(actor_kind="system", actor_user__isnull=True, actor_participation__isnull=True, authentication_context__isnull=True) & ~models.Q(system_component="") & ~models.Q(system_version=""))
                ), name="audit_actor_identity_xor",
            ),
            models.CheckConstraint(
                condition=(models.Q(activity__isnull=False, participation__isnull=True, session__isnull=True)
                           | models.Q(activity__isnull=True, participation__isnull=False, session__isnull=True)
                           | models.Q(activity__isnull=True, participation__isnull=True, session__isnull=False)),
                name="audit_scope_xor",
            ),
            models.CheckConstraint(condition=models.Q(assessment__isnull=True) | models.Q(session__isnull=False), name="audit_assessment_session_scope"),
            models.CheckConstraint(condition=~models.Q(action=""), name="audit_action_present"),
            models.CheckConstraint(condition=~models.Q(caused_by_event=models.F("pk")), name="audit_not_own_cause"),
            models.CheckConstraint(
                condition=(models.Q(version_before__isnull=True, version_after__isnull=True)
                           | models.Q(version_before__isnull=True, version_after=1)
                           | models.Q(version_before__gte=1, version_before__isnull=False, version_after__isnull=False, version_after=models.F("version_before") + 1)),
                name="audit_version_step",
            ),
        ]

    def clean_fields(self, exclude=None):
        for field in ("action", "reason", "system_component", "system_version"):
            value = getattr(self, field)
            if value is None and field != "action":
                setattr(self, field, "")
            elif isinstance(value, str):
                setattr(self, field, value.strip())
        for field in ("before_state", "after_state", "authentication_context"):
            if field == "authentication_context" and getattr(self, field) is None:
                continue
            try:
                validator = validate_authentication_context if field == "authentication_context" else validate_snapshot
                validator(getattr(self, field))
            except ValidationError as error:
                raise ValidationError({field: error.messages}) from error
        super().clean_fields(exclude=exclude)

    def clean(self):
        super().clean()
        assessment = related(self, "assessment")
        session = related(self, "session")
        if assessment is not None and assessment.session_id != self.session_id:
            raise ValidationError({"assessment": "Assessment and audit scope must reference the same session."})
        actor = related(self, "actor_participation")
        if actor is not None:
            if isinstance(self.authentication_context, dict) and self.authentication_context.get("participation_id") != actor.pk:
                raise ValidationError({"authentication_context": "Principal context and actor participation do not match."})
            if self.participation_id is not None and self.participation_id != actor.pk:
                raise ValidationError({"actor_participation": "Participant principals cannot act for another participation."})
            if session is not None and session.participation_id != actor.pk:
                raise ValidationError({"actor_participation": "Participant principal and session scope do not match."})
            if self.activity_id is not None and self.activity_id != actor.activity_id:
                raise ValidationError({"actor_participation": "Participant principal and activity scope do not match."})

    def save(self, *, force_insert=False, force_update=False, using=None, update_fields=None):
        if update_fields is not None:
            raise ValidationError("Partial audit writes are disabled.")
        if force_update:
            raise ValidationError("Audit events permit INSERT only.")
        using = using or router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=using):
            if not self._state.adding or (self.pk is not None and type(self).objects.using(using).filter(pk=self.pk).exists()):
                raise ValidationError("Audit events are append-only.")
            if self.actor_kind == self.ActorKind.PARTICIPANT:
                raise ValidationError("Participant audit insertion requires the unavailable trusted authentication adapter; a supplied ID/context is insufficient.")
            self.full_clean()
            return super().save(force_insert=True, using=using)

    def delete(self, using=None, keep_parents=False):
        raise ValidationError("Audit events cannot be deleted.")
