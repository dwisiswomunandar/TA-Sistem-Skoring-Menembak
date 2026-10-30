"""Parent-domain guards, not an authorization or business-audit service.

Existing session -> participation(s, PK order) -> activity -> participant.
Never acquire an existing session lock while already holding participation locks.
New sessions have no persisted row to lock and start at their participation.
"""

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import models, router, transaction

from .querysets import HistoricalParentQuerySet


def lock_rows(model, identifiers, using, *, field_name):
    try:
        identifiers = {model._meta.pk.to_python(value) for value in identifiers if value is not None}
    except ValidationError as error:
        raise ValidationError({field_name: error.messages}) from error
    if not identifiers:
        return {}
    rows = model.objects.using(using).select_for_update().filter(pk__in=identifiers).order_by("pk")
    return {row.pk: row for row in rows}


def lock_session_reference(identifier, using):
    model = apps.get_model("activities", "TrainingSession")
    rows = lock_rows(model, [identifier], using, field_name="session")
    if not rows:
        raise ValidationError({"session": "A persisted training session is required."})
    return next(iter(rows.values()))


def lock_participation_references(identifiers, using):
    model = apps.get_model("activities", "ActivityParticipation")
    return lock_rows(model, identifiers, using, field_name="participation")


class HistoricalParent(models.Model):
    """Compare against locked rows; existing model versions are server-managed."""

    objects = HistoricalParentQuerySet.as_manager()

    class Meta:
        abstract = True
        base_manager_name = "objects"
        default_manager_name = "objects"

    def _stored(self, using, *, lock=False):
        if self.pk is None:
            return None
        try:
            identifier = self._meta.pk.to_python(self.pk)
        except ValidationError as error:
            raise ValidationError({"id": error.messages}) from error
        queryset = type(self).objects.using(using)
        if lock:
            queryset = queryset.select_for_update()
        return queryset.filter(pk=identifier).first()

    def _lock_context(self, original, using):
        pass

    def _frozen_fields(self, original, using):
        raise NotImplementedError

    def _validate_history(self, original, using):
        if original is None:
            return
        errors = {}
        for name in ("created_at", *self._frozen_fields(original, using)):
            field = self._meta.get_field(name)
            if getattr(self, field.attname) != getattr(original, field.attname):
                errors[field.name] = "Persisted historical context cannot change; an audited correction workflow is required."
        if errors:
            raise ValidationError(errors)

    def clean(self):
        super().clean()
        using = router.db_for_write(type(self), instance=self)
        self._validate_history(self._stored(using), using)

    def _check_version(self, original):
        expected = original.edit_version if original is not None else 1
        if type(self.edit_version) is not int or self.edit_version != expected:
            raise ValidationError({"edit_version": "Version is server-managed; reload a stale parent before editing."})

    def save(self, *, force_insert=False, force_update=False, using=None, update_fields=None):
        if update_fields is not None or self.get_deferred_fields():
            raise ValidationError("Partial/deferred saves are disabled; load and save the complete parent.")
        using = using or router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=using):
            original = self._stored(using, lock=True)
            if original is None and (not self._state.adding or force_update):
                raise ValidationError("A missing parent cannot be recreated or force-updated by a stale instance.")
            if original is not None and self._state.adding:
                raise ValidationError("A new parent must not overwrite an existing primary key.")
            self._check_version(original)
            self._lock_context(original, using)
            self.full_clean()
            # Explicit alias validation as well as the advisory clean() path.
            self._validate_history(original, using)
            changed = original is not None and any(
                getattr(self, field.attname) != getattr(original, field.attname)
                for field in self._meta.concrete_fields
                if not field.primary_key and field.name not in ("created_at", "updated_at", "edit_version")
            )
            version = self.edit_version
            if changed:
                self.edit_version += 1
            try:
                self._meta.get_field("edit_version").run_validators(self.edit_version)
                return super().save(
                    force_insert=force_insert or original is None, force_update=force_update, using=using,
                )
            except Exception:
                self.edit_version = version
                raise

    def delete(self, using=None, keep_parents=False):
        if self.pk is None:
            raise ValueError("An unsaved parent cannot be deleted.")
        if self.get_deferred_fields() or self._state.adding:
            raise ValidationError("Load the complete persisted parent before deletion.")
        using = using or router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=using):
            original = self._stored(using, lock=True)
            if original is None:
                raise ValidationError("The parent was already deleted.")
            self._check_version(original)
            # The existing PROTECT collector rejects every historical reference.
            return super().delete(using=using, keep_parents=keep_parents)
