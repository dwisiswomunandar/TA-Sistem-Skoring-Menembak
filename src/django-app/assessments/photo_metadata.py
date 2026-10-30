"""Read and verify originals without rewriting bytes or applying preprocessing."""

import hashlib
import warnings
from tempfile import SpooledTemporaryFile

from django.core.exceptions import ValidationError
from django.core.files.storage import storages
from PIL import Image, UnidentifiedImageError

from .validators import validate_private_key


def inspect_original(storage_key):
    validate_private_key(storage_key)
    digest, size = hashlib.sha256(), 0
    try:
        with storages["target_photos"].open(storage_key, "rb") as source:
            with SpooledTemporaryFile(max_size=1024 * 1024) as snapshot:
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
                    size += len(chunk)
                    snapshot.write(chunk)
                snapshot.seek(0)
                with warnings.catch_warnings():
                    warnings.simplefilter("error", Image.DecompressionBombWarning)
                    with Image.open(snapshot, formats=("JPEG", "PNG")) as image:
                        width, height = image.size
                        mime = {"JPEG": "image/jpeg", "PNG": "image/png"}[image.format]
                        if getattr(image, "n_frames", 1) != 1:
                            raise ValidationError("An original must be a single still image.")
                        image.verify()
                    snapshot.seek(0)
                    with Image.open(snapshot, formats=("JPEG", "PNG")) as image:
                        image.load()
    except (
        OSError, UnidentifiedImageError, ValueError, SyntaxError,
        Image.DecompressionBombError, Image.DecompressionBombWarning,
    ) as error:
        raise ValidationError({"storage_key": "Original must be a readable, valid JPEG or PNG image."}) from error
    return {
        "sha256": digest.hexdigest(), "byte_size": size, "width_px": width,
        "height_px": height, "verified_mime_type": mime,
    }
