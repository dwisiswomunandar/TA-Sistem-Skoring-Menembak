from django.core.exceptions import ValidationError
from django.db import models


class ConfigurationQuerySet(models.QuerySet):
    """Public write paths must validate and lock individual configurations."""

    def update(self, **kwargs):
        raise ValidationError("QuerySet.update() is disabled; use instance save()/publish()/retire().")

    def delete(self):
        raise ValidationError("QuerySet.delete() is disabled; delete individual draft instances.")

    def bulk_create(
        self, objs, batch_size=None, ignore_conflicts=False, update_conflicts=False,
        update_fields=None, unique_fields=None,
    ):
        raise ValidationError("bulk_create() is disabled for versioned configurations and rings.")

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValidationError("bulk_update() is disabled for versioned configurations and rings.")

    update.alters_data = True
    delete.alters_data = True
    delete.queryset_only = True
    bulk_create.alters_data = True
    bulk_update.alters_data = True


class EvidenceQuerySet(ConfigurationQuerySet):
    """Apply the iteration 02 write boundary to photographic evidence."""

    def update(self, **kwargs):
        raise ValidationError("QuerySet.update() is disabled; save a complete evidence instance.")

    def delete(self):
        raise ValidationError("QuerySet.delete() is disabled; use an eligible instance delete().")

    def bulk_create(self, *args, **kwargs):
        raise ValidationError("bulk_create() is disabled for evidence records.")

    def bulk_update(self, *args, **kwargs):
        raise ValidationError("bulk_update() is disabled for evidence records.")

    update.alters_data = True
    delete.alters_data = True
    delete.queryset_only = True
    bulk_create.alters_data = True
    bulk_update.alters_data = True


class ResultQuerySet(EvidenceQuerySet):
    """Result writes need instance guards, including on base/related/async managers."""

    def latest_working(self, *, session_id):
        return self.filter(session_id=session_id).order_by("-revision_number").first()

    def latest_official(self, *, session_id):
        return self.filter(session_id=session_id, status="locked").order_by("-revision_number").first()
