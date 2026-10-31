"""Original storage API regression tests using disposable synthetic files only."""

import copy
import hashlib
from io import BytesIO, UnsupportedOperation
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage, storages
from django.test import SimpleTestCase, override_settings
from PIL import Image

from assessments.photo_metadata import inspect_original


# Include both spellings of binary/text update modes accepted by Python open().
WRITE_MODES = (
    "w", "wb", "wt", "w+", "wb+", "w+b", "wt+", "w+t",
    "a", "ab", "at", "a+", "ab+", "a+b", "at+", "a+t",
    "x", "xb", "xt", "x+", "xb+", "x+b", "xt+", "x+t",
    "r+", "rb+", "r+b", "rt+", "r+t",
)


class PrivateTargetPhotoStorageTests(SimpleTestCase):
    def setUp(self):
        directory = TemporaryDirectory(prefix="django-original-storage-tests-")
        self.addCleanup(directory.cleanup)
        override = override_settings(
            PRIVATE_TARGET_PHOTO_ROOT=directory.name,
            STORAGES=copy.deepcopy(settings.STORAGES),
        )
        override.enable()
        self.addCleanup(override.disable)
        self.storage = storages["target_photos"]

    def image_bytes(self, image_format="PNG"):
        stream = BytesIO()
        Image.new("RGB", (80, 60), color="white").save(stream, format=image_format)
        return stream.getvalue()

    def assert_original_unchanged(self, key, original):
        # Independent filesystem read, so the storage guard cannot mask corruption.
        actual = Path(self.storage.path(key)).read_bytes()
        self.assertEqual(actual, original)
        self.assertEqual(hashlib.sha256(actual).hexdigest(), hashlib.sha256(original).hexdigest())

    def test_default_and_explicit_binary_reads_return_exact_bytes(self):
        original = self.image_bytes()
        key = self.storage.save("originals/binary.png", ContentFile(original))
        for options in ({}, {"mode": "rb"}):
            with self.subTest(options=options), self.storage.open(key, **options) as source:
                self.assertTrue(source.readable())
                self.assertFalse(source.writable())
                self.assertEqual(source.read(), original)
                source.seek(0)
                self.assertEqual(b"".join(source.chunks(chunk_size=7)), original)
            self.assertTrue(source.closed)

    def test_text_read_modes_are_read_only(self):
        # ASCII fixture tests text I/O only, not image format validation.
        key = self.storage.save("originals/text-fixture.bin", ContentFile(b"synthetic"))
        for mode in ("r", "rt"):
            with self.subTest(mode=mode), self.storage.open(key, mode) as source:
                self.assertTrue(source.readable())
                self.assertFalse(source.writable())
                self.assertEqual(source.read(), "synthetic")
            self.assertTrue(source.closed)

    def test_write_modes_are_rejected_before_filesystem_access(self):
        for method in ("open", "_open"):
            for mode in WRITE_MODES:
                with self.subTest(method=method, mode=mode):
                    with patch.object(FileSystemStorage, "_open") as filesystem_open:
                        with self.assertRaises(ValidationError):
                            getattr(self.storage, method)("originals/absent.png", mode)
                        filesystem_open.assert_not_called()

    def test_unlisted_modes_are_rejected_before_filesystem_access(self):
        for mode in ("", "br", "tr", "rU", "rb ", " rb", "RB", "rr", "rb\x00", None, 1, b"rb", [], {}):
            with self.subTest(mode=mode), patch.object(FileSystemStorage, "_open") as filesystem_open:
                with self.assertRaises(ValidationError):
                    self.storage.open("originals/absent.png", mode)
                filesystem_open.assert_not_called()

    def test_rejected_access_preserves_original_bytes_and_verified_metadata(self):
        for image_format in ("PNG", "JPEG"):
            original = self.image_bytes(image_format)
            # Deliberately unrelated extension: MIME still comes from verified bytes.
            key = self.storage.save(f"originals/{image_format}.bin", ContentFile(original))
            before = inspect_original(key)
            self.assertEqual(before, {
                "sha256": hashlib.sha256(original).hexdigest(), "byte_size": len(original),
                "width_px": 80, "height_px": 60,
                "verified_mime_type": {"PNG": "image/png", "JPEG": "image/jpeg"}[image_format],
            })
            for mode in WRITE_MODES:
                with self.subTest(image_format=image_format, mode=mode):
                    with self.assertRaises(ValidationError):
                        with self.storage.open(key, mode) as source:
                            source.write(b"replacement")
                    self.assert_original_unchanged(key, original)
                    self.assertEqual(inspect_original(key), before)

    def test_rejected_access_does_not_create_missing_originals(self):
        key = "originals/missing.png"
        for mode in WRITE_MODES:
            with self.subTest(mode=mode):
                with self.assertRaises(ValidationError):
                    with self.storage.open(key, mode):
                        pass
                self.assertFalse(Path(self.storage.path(key)).exists())
        for mode in ("r", "rb", "rt"):
            with self.subTest(mode=mode), self.assertRaises(FileNotFoundError):
                self.storage.open(key, mode)
        self.assertFalse(Path(self.storage.path(key)).exists())

    def test_read_handles_reject_writes_and_truncation(self):
        original = b"synthetic"
        key = self.storage.save("originals/read-handle.bin", ContentFile(original))
        for mode in ("r", "rb", "rt"):
            with self.subTest(mode=mode), self.storage.open(key, mode) as source:
                replacement = b"replacement" if mode == "rb" else "replacement"
                with self.assertRaises(UnsupportedOperation):
                    source.write(replacement)
                with self.assertRaises(UnsupportedOperation):
                    source.writelines([replacement])
                with self.assertRaises(UnsupportedOperation):
                    source.truncate(0)
            self.assert_original_unchanged(key, original)

    def test_read_keys_are_validated_before_filesystem_access(self):
        for key in (
            "https://example.com/photo.jpg", "/originals/a.png", "originals/../a.png",
            "originals/./a.png", "originals//a.png", "originals/a.png?token=x",
            "originals\\a.png", "media/photo.png", "originals/%2e%2e/a.png", "", None,
        ):
            with self.subTest(key=key), patch.object(FileSystemStorage, "_open") as filesystem_open:
                with self.assertRaises(ValidationError):
                    self.storage.open(key)
                filesystem_open.assert_not_called()

    def test_save_paths_preserve_creation_and_overwrite_guards(self):
        original = self.image_bytes()
        for save in (self.storage.save, self.storage._save):
            key = f"originals/nested/{save.__name__}.png"
            with self.subTest(method=save.__name__):
                self.assertEqual(save(key, ContentFile(original)), key)
                with self.assertRaises(ValidationError):
                    save(key, ContentFile(b"replacement"))
                self.assert_original_unchanged(key, original)
                self.assertEqual(inspect_original(key)["sha256"], hashlib.sha256(original).hexdigest())
                with self.assertRaises(ValidationError):
                    save("outside-originals.png", ContentFile(original))
                with self.assertRaises(ValidationError):
                    self.storage.delete(key)
                with self.assertRaises(NotImplementedError):
                    self.storage.url(key)
                self.assert_original_unchanged(key, original)
