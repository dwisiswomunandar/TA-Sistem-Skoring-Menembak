"""Optimistic tokens checked against locked rows; not authorization or auditing."""

from asgiref.sync import sync_to_async
from django.core.exceptions import ValidationError
from django.db import models


class LifecycleWrites:
    async def asave(self, **options):
        return await sync_to_async(self.save)(**options)

    async def adelete(self, **options):
        return await sync_to_async(self.delete)(**options)


def check_write(instance, original, expected=None):
    if instance.get_deferred_fields():
        raise ValidationError("Load a complete lifecycle object before writing.")
    if original is None:
        if not instance._state.adding:
            raise ValidationError("A deleted lifecycle record cannot be recreated.")
        version = 1
    else:
        if instance._state.adding:
            raise ValidationError("A new instance cannot overwrite an existing primary key.")
        version = original.edit_version
    if type(instance.edit_version) is not int or instance.edit_version != version:
        raise ValidationError({"edit_version": "Stale or invalid lifecycle version; reload before editing."})
    if expected is not None and (type(expected) is not int or expected != version):
        raise ValidationError({"edit_version": "The expected lifecycle version is stale or invalid."})


def persist(instance, original, using, *, force_insert=False, force_update=False):
    if original is None and force_update:
        raise ValidationError("A new lifecycle record cannot be force-updated.")
    version = instance.edit_version
    if original is not None:
        instance.edit_version += 1
    try:
        instance.full_clean()
        return models.Model.save(instance, using=using, force_insert=force_insert or original is None,
                                 force_update=force_update)
    except Exception:
        instance.edit_version = version
        raise


def bump_target(target, using):
    """Only called with the draft target already locked, in the ring transaction."""
    target.edit_version += 1
    target._meta.get_field("edit_version").clean(target.edit_version, target)
    models.Model.save(target, using=using, update_fields=("edit_version", "updated_at"))
