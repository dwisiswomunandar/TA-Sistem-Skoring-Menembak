from django.core.exceptions import ValidationError
from django.db import models


class HistoricalParentQuerySet(models.QuerySet):
    """Public writes must validate the complete parent against persisted history."""

    def update(self, **kwargs):
        raise ValidationError("QuerySet.update() is disabled for historical domain parents.")

    def delete(self):
        raise ValidationError("Delete an individual unreferenced parent through its guarded model.")

    def bulk_create(self, *args, **kwargs):
        raise ValidationError("bulk_create() is disabled for historical domain parents.")

    def bulk_update(self, *args, **kwargs):
        raise ValidationError("bulk_update() is disabled for historical domain parents.")

    update.alters_data = True
    delete.alters_data = True
    delete.queryset_only = True
    bulk_create.alters_data = True
    bulk_update.alters_data = True
