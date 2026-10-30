"""Private original storage. No serving view or public URL is provided."""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.storage import FileSystemStorage

from .validators import validate_private_key


class PrivateTargetPhotoStorage(FileSystemStorage):
    def __init__(self):
        super().__init__(location=settings.PRIVATE_TARGET_PHOTO_ROOT, allow_overwrite=False)

    def url(self, name):
        raise NotImplementedError("Target originals have no public URL.")

    def get_available_name(self, name, max_length=None):
        validate_private_key(name)
        if self.exists(name):
            raise ValidationError("Original keys cannot be reused or overwritten.")
        if max_length is not None and len(name) > max_length:
            raise ValidationError("Original keys cannot be truncated or renamed.")
        return name

    def delete(self, name):
        raise ValidationError("Originals require a separately reviewed retention/cleanup process.")

    def _save(self, name, content):
        # FileSystemStorage retries get_available_name() after a concurrent key collision.
        validate_private_key(name)
        return super()._save(name, content)
