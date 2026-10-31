"""Synthetic files/results/transforms only; no AI, calibration fit, or real database."""

import copy
import hashlib
from datetime import timedelta
from decimal import Decimal
from io import BytesIO
from tempfile import TemporaryDirectory
from uuid import uuid4
from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.db import IntegrityError, connection, models, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.models.deletion import ProtectedError
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from PIL import Image

from accounts.models import Participant
from activities.models import Activity, ActivityParticipation, TrainingSession
from assessments.models import Calibration, DetectionRun, TargetPhoto, TargetRing, TargetVersion
from assessments.photo_metadata import inspect_original

User = get_user_model()
MATRIX = [[2, 0, -80], [0, -2, 60], [0, 0, 1]]
EMPTY_RESULT = {"schema_version": 1, "detections": []}
RESULT = {
    "schema_version": 1,
    "detections": [{"id": "synthetic-1", "class_id": 0, "confidence": 0.8,
                    "bbox_xyxy": [1, 2, 8, 9]}],
    "adapter_extra": {"note": "Synthetic fixture only"},
}


class EvidenceTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.actor = User.objects.create_user("evidence-author@example.com")
        person = Participant.objects.create(
            full_name="Synthetic participant", identity_status="verified",
            verified_by=cls.actor, verified_at=timezone.now(),
        )
        activity = Activity.objects.create(
            name="Synthetic activity", training_date=timezone.localdate(),
            status="active", created_by=cls.actor,
        )
        participation = ActivityParticipation.objects.create(
            activity=activity, participant=person, registration_status="approved",
            participant_identifier="fixture-001", decided_by=cls.actor, decided_at=timezone.now(),
        )
        cls.session = TrainingSession.objects.create(
            participation=participation, session_number=1, assigned_assessor=cls.actor,
            created_by=cls.actor, weapon_type="pistol", shooting_position="berdiri",
            distance_m=Decimal("10"), caliber_mm=Decimal("9"),
        )
        cls.target = TargetVersion.objects.create(
            code="synthetic-target", version=1, name="Synthetic target", width_mm=Decimal("200"),
            height_mm=Decimal("300"), created_by=cls.actor,
        )
        TargetRing.objects.create(
            target_version=cls.target, ring_number=1, outer_radius_mm=Decimal("10"), score=1,
        )
        cls.target.refresh_from_db()
        cls.target.publish()

    def setUp(self):
        directory = TemporaryDirectory(prefix="django-evidence-tests-")
        self.addCleanup(directory.cleanup)
        override = override_settings(
            PRIVATE_TARGET_PHOTO_ROOT=directory.name, STORAGES=copy.deepcopy(settings.STORAGES),
        )
        override.enable()
        self.addCleanup(override.disable)
        self.storage = storages["target_photos"]

    def image_bytes(self, image_format="PNG"):
        stream = BytesIO()
        Image.new("RGB", (80, 60), color="white").save(stream, format=image_format)
        return stream.getvalue()

    def store_original(self, data=None):
        key = f"originals/{uuid4().hex}.bin"
        self.storage.save(key, ContentFile(self.image_bytes() if data is None else data))
        return key

    def make_photo(self, **overrides):
        values = {
            "session": self.session, "uploaded_by": self.actor,
            "storage_key": self.store_original(), "preprocessing_version": "synthetic-explicit-v1",
            "analysis_width_px": 80, "analysis_height_px": 60,
        }
        values.update(overrides)
        return TargetPhoto.objects.create(**values)

    def make_run(self, **overrides):
        values = {
            "photo": overrides.pop("photo", None) or self.make_photo(),
            "triggered_by": self.actor, "model_name": "synthetic-detector",
            "weights_sha256": "a" * 64, "runtime_version": "synthetic-runtime-v1",
            "adapter_version": "synthetic-adapter-v1", "parameters": {},
        }
        values.update(overrides)
        return DetectionRun.objects.create(**values)

    def finish_run(self, *, succeeded=True, result=None):
        run = self.make_run()
        run.status = "running"
        run.save()
        run.status = "succeeded" if succeeded else "failed"
        run.raw_result = copy.deepcopy(EMPTY_RESULT if result is None else result) if succeeded else None
        run.failure_code = "" if succeeded else "synthetic_failure"
        run.save()
        return run

    def make_calibration(self, **overrides):
        values = {
            "photo": overrides.pop("photo", None) or self.make_photo(), "target_version": self.target,
            "method_version": "synthetic-method-v1", "created_by": self.actor,
            "transform_matrix": copy.deepcopy(MATRIX),
        }
        values.update(overrides)
        return Calibration.objects.create(**values)

    def accept(self, calibration=None):
        calibration = calibration or self.make_calibration()
        calibration.status, calibration.accepted_by = "accepted", self.actor
        calibration.save()
        return calibration

    def raw(self, model):
        return models.QuerySet(model=model, using="default")

    def assert_database_rejects(self, instance, **changes):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.raw(type(instance)).filter(pk=instance.pk).update(**changes)


class TargetPhotoTests(EvidenceTestCase):
    def test_metadata_is_computed_from_exact_original_bytes(self):
        original = self.image_bytes()
        photo = self.make_photo(storage_key=self.store_original(original))
        photo.refresh_from_db()
        self.assertEqual(photo.sha256, hashlib.sha256(original).hexdigest())
        self.assertEqual(photo.byte_size, len(original))
        self.assertEqual((photo.width_px, photo.height_px), (80, 60))
        self.assertEqual(photo.verified_mime_type, "image/png")
        with self.storage.open(photo.storage_key, "rb") as stored:
            self.assertEqual(stored.read(), original)

    def test_jpeg_mime_is_detected_without_trusting_extension(self):
        photo = self.make_photo(storage_key=self.store_original(self.image_bytes("JPEG")))
        self.assertEqual(photo.verified_mime_type, "image/jpeg")

    def test_supplied_metadata_must_match_verified_bytes(self):
        for values in (
            {"sha256": "f" * 64}, {"byte_size": 1}, {"width_px": 1}, {"height_px": 1},
            {"verified_mime_type": "image/jpeg"},
        ):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                self.make_photo(**values)
        self.assertEqual(TargetPhoto.objects.count(), 0)

    def test_matching_supplied_metadata_is_accepted(self):
        key = self.store_original()
        photo = self.make_photo(storage_key=key, **inspect_original(key))
        self.assertEqual(photo.storage_key, key)

    def test_missing_or_invalid_original_is_rejected(self):
        for key in (
            "originals/missing.png", self.store_original(b"not an image"),
            self.store_original(self.image_bytes()[:30]),
        ):
            with self.subTest(key=key), self.assertRaises(ValidationError):
                self.make_photo(storage_key=key)

    def test_unsupported_and_animated_images_are_rejected(self):
        frames = [Image.new("RGB", (80, 60), color=color) for color in ("red", "blue")]
        stream = BytesIO()
        frames[0].save(stream, format="PNG", save_all=True, append_images=frames[1:])
        for data in (self.image_bytes("GIF"), stream.getvalue()):
            with self.subTest(size=len(data)), self.assertRaises(ValidationError):
                self.make_photo(storage_key=self.store_original(data))

    def test_unsafe_storage_keys_are_rejected_before_reading(self):
        for key in (
            "https://example.com/photo.jpg", "/originals/a.png", "originals/../a.png",
            "originals/./a.png", "originals//a.png", "originals/a.png?token=x",
            "originals\\a.png", "media/photo.png", "originals/%2e%2e/a.png", "",
        ):
            with self.subTest(key=key), self.assertRaises(ValidationError):
                self.make_photo(storage_key=key)

    def test_required_coordinate_contract_has_no_silent_defaults(self):
        for values in (
            {"preprocessing_version": " \t "}, {"analysis_width_px": 0},
            {"analysis_height_px": 0}, {"analysis_width_px": None},
            {"analysis_height_px": -1},
        ):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                self.make_photo(**values)

    def test_original_and_analysis_dimensions_are_distinct(self):
        photo = self.make_photo(analysis_width_px=40, analysis_height_px=30)
        self.assertEqual((photo.width_px, photo.height_px), (80, 60))
        self.assertEqual((photo.analysis_width_px, photo.analysis_height_px), (40, 30))

    def test_storage_is_private_and_no_public_url_is_provided(self):
        photo = self.make_photo()
        with self.assertRaises(NotImplementedError):
            self.storage.url(photo.storage_key)
        self.assertFalse(hasattr(photo, "url"))
        self.assertFalse(hasattr(photo, "file"))
        self.assertNotEqual(self.storage.location, str(settings.MEDIA_ROOT))
        self.assertEqual(self.client.get("/media/" + photo.storage_key).status_code, 404)

    def test_storage_never_overwrites_or_renames_an_existing_original(self):
        key = self.store_original()
        with self.assertRaises(ValidationError):
            self.storage.save(key, ContentFile(b"replacement"))
        with self.storage.open(key) as stored:
            self.assertEqual(stored.read(), self.image_bytes())

    def test_storage_does_not_delete_originals_or_shorten_keys(self):
        key = self.store_original()
        with self.assertRaises(ValidationError):
            self.storage.delete(key)
        with self.assertRaises(ValidationError):
            self.storage.save("originals/too-long.png", ContentFile(self.image_bytes()), max_length=12)
        self.assertTrue(self.storage.exists(key))

    def test_image_decompression_bombs_are_rejected(self):
        key = self.store_original()
        with patch("assessments.photo_metadata.Image.MAX_IMAGE_PIXELS", 1):
            with self.assertRaises(ValidationError):
                self.make_photo(storage_key=key)

    def test_original_metadata_and_session_are_immutable_and_not_deletable(self):
        photo = self.make_photo()
        for field, value in (
            ("sha256", "f" * 64), ("session_id", 9999), ("storage_key", "originals/replaced.png"),
            ("preprocessing_version", "v2"), ("analysis_width_px", 40),
        ):
            photo.refresh_from_db()
            setattr(photo, field, value)
            with self.subTest(field=field), self.assertRaises(ValidationError):
                photo.save()
        with self.assertRaises(ValidationError):
            photo.delete()

    def test_storage_key_is_unique_but_identical_bytes_may_have_distinct_keys(self):
        first, second = self.make_photo(), self.make_photo()
        self.assertEqual(first.sha256, second.sha256)
        self.assertNotEqual(first.storage_key, second.storage_key)
        with self.assertRaises(ValidationError):
            self.make_photo(storage_key=first.storage_key)
        self.assert_database_rejects(second, storage_key=first.storage_key)

    def test_photo_database_constraints(self):
        photo = self.make_photo()
        for changes in (
            {"storage_key": ""}, {"sha256": ""}, {"sha256": "z" * 64}, {"sha256": "A" * 64},
            {"sha256": "a" * 64 + "\n"},
            {"byte_size": 0}, {"width_px": 0}, {"height_px": 0}, {"analysis_width_px": 0},
            {"analysis_height_px": 0}, {"verified_mime_type": "text/html"},
            {"preprocessing_version": ""},
        ):
            with self.subTest(changes=changes):
                self.assert_database_rejects(photo, **changes)

    def test_session_and_uploader_use_protect(self):
        actor = User.objects.create_user("uploader@example.com")
        photo = self.make_photo(uploaded_by=actor)
        for parent in (self.session, actor):
            with self.subTest(parent=type(parent).__name__), self.assertRaises(ProtectedError) as error:
                parent.delete()
            self.assertIn(photo, error.exception.protected_objects)


class DetectionRunTests(EvidenceTestCase):
    def test_pending_running_succeeded_lifecycle_preserves_raw_output(self):
        run = self.make_run()
        self.assertIsNone(run.raw_result)
        self.assertIsNone(run.started_at)
        self.assertIsNone(run.finished_at)
        run.status = "running"
        run.save()
        self.assertIsNotNone(run.started_at)
        run.status, run.raw_result = "succeeded", copy.deepcopy(RESULT)
        run.save()
        run.refresh_from_db()
        self.assertEqual(run.raw_result, RESULT)
        self.assertGreaterEqual(run.finished_at, run.started_at)
        self.assertEqual(run.failure_code, "")

    def test_succeeded_zero_detections_is_distinct_from_failed(self):
        succeeded, failed = self.finish_run(), self.finish_run(succeeded=False)
        self.assertEqual(succeeded.status, "succeeded")
        self.assertEqual(succeeded.raw_result, EMPTY_RESULT)
        self.assertEqual(failed.status, "failed")
        self.assertIsNone(failed.raw_result)
        self.assertTrue(failed.failure_code)

    def test_pending_may_fail_before_execution_without_a_start_time(self):
        run = self.make_run()
        run.status, run.failure_code = "failed", "synthetic_model_load_failure"
        run.save()
        self.assertIsNone(run.started_at)
        self.assertIsNotNone(run.finished_at)

    def test_run_creation_cannot_skip_pending(self):
        for status in ("running", "succeeded", "failed", "invalid"):
            with self.subTest(status=status), self.assertRaises(ValidationError):
                self.make_run(status=status)

    def test_pending_cannot_succeed_and_running_cannot_return_to_pending(self):
        run = self.make_run()
        run.status, run.raw_result = "succeeded", copy.deepcopy(EMPTY_RESULT)
        with self.assertRaises(ValidationError):
            run.save()
        run.refresh_from_db()
        run.status = "running"
        run.save()
        run.status = "pending"
        with self.assertRaises(ValidationError):
            run.save()

    def test_incomplete_success_and_failure_are_rejected(self):
        run = self.make_run()
        run.status = "running"
        run.save()
        for status, result, code in (
            ("succeeded", None, ""), ("succeeded", {}, ""), ("succeeded", EMPTY_RESULT, "error"),
            ("failed", None, ""), ("failed", None, " \t "), ("failed", EMPTY_RESULT, "error"),
        ):
            run.refresh_from_db()
            run.status, run.raw_result, run.failure_code = status, copy.deepcopy(result), code
            with self.subTest(status=status, result=result, code=code), self.assertRaises(ValidationError):
                run.save()

    def test_pending_and_running_cannot_carry_results_or_failure_codes(self):
        for status in ("pending", "running"):
            run = self.make_run()
            if status == "running":
                run.status = status
                run.save()
            for changes in ({"raw_result": EMPTY_RESULT}, {"failure_code": "premature"}):
                run.refresh_from_db()
                for field, value in changes.items():
                    setattr(run, field, value)
                with self.subTest(status=status, changes=changes), self.assertRaises(ValidationError):
                    run.save()

    def test_explicit_model_identity_and_parameters_are_required(self):
        for values in (
            {"model_name": " \t "}, {"weights_sha256": "g" * 64}, {"weights_sha256": "a" * 63},
            {"weights_sha256": "A" * 64}, {"runtime_version": ""}, {"adapter_version": ""},
            {"parameters": None}, {"parameters": []}, {"parameters": "{}"},
            {"parameters": {"threshold": float("nan")}}, {"parameters": {1: "bad key"}},
            {"parameters": {"tuple": (1, 2)}},
        ):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                self.make_run(**values)

    def test_all_run_provenance_is_frozen_even_while_pending(self):
        run = self.make_run()
        other_photo = self.make_photo()
        for field, value in (
            ("photo_id", other_photo.pk), ("model_name", "another-detector"),
            ("weights_sha256", "b" * 64), ("runtime_version", "v2"), ("adapter_version", "v2"),
            ("parameters", {"changed": True}), ("triggered_by_id", 9999),
            ("created_at", timezone.now()), ("started_at", timezone.now()),
        ):
            run.refresh_from_db()
            setattr(run, field, value)
            with self.subTest(field=field), self.assertRaises(ValidationError):
                run.save()

    def test_run_parameters_cannot_change_json_type_while_comparing_equal_in_python(self):
        run = self.make_run(parameters={"synthetic": 1})
        run.parameters = {"synthetic": True}
        with self.assertRaises(ValidationError):
            run.save()

    def test_invalid_result_schema_and_detection_fields_are_rejected(self):
        invalid = [None, [], {}, {"schema_version": True, "detections": []},
                   {"schema_version": 2, "detections": []}, {"schema_version": 1},
                   {"schema_version": 1, "detections": [None]}]
        for changes in (
            {"id": ""}, {"id": 1}, {"class_id": -1}, {"class_id": True},
            {"confidence": True}, {"confidence": -0.1}, {"confidence": 1.1},
            {"confidence": float("inf")}, {"bbox_xyxy": [1, 2, 8]},
            {"bbox_xyxy": [1, 2, 1, 9]}, {"bbox_xyxy": [-1, 2, 8, 9]},
            {"bbox_xyxy": [1, 2, 81, 9]}, {"bbox_xyxy": [1, 2, 8, 61]},
            {"bbox_xyxy": [True, 2, 8, 9]}, {"bbox_xyxy": [1, 2, float("nan"), 9]},
        ):
            result = copy.deepcopy(RESULT)
            result["detections"][0].update(changes)
            invalid.append(result)
        duplicate = copy.deepcopy(RESULT)
        duplicate["detections"] *= 2
        invalid.append(duplicate)
        nonfinite_extra = copy.deepcopy(RESULT)
        nonfinite_extra["extra"] = {"nested": [float("nan")]}
        invalid.append(nonfinite_extra)
        run = self.make_run()
        run.status = "running"
        run.save()
        for result in invalid:
            run.refresh_from_db()
            run.status, run.raw_result = "succeeded", result
            with self.subTest(result=result), self.assertRaises(ValidationError):
                run.save()

    def test_boxes_use_analysis_dimensions_instead_of_original_dimensions(self):
        photo = self.make_photo(analysis_width_px=4, analysis_height_px=4)
        run = self.make_run(photo=photo)
        run.status = "running"
        run.save()
        run.status, run.raw_result = "succeeded", copy.deepcopy(RESULT)
        with self.assertRaises(ValidationError):
            run.save()

    def test_finished_run_cannot_be_changed_deleted_or_overwritten_by_stale_copy(self):
        for succeeded in (True, False):
            run = self.make_run()
            stale = DetectionRun.objects.get(pk=run.pk)
            run.status, run.failure_code = "failed", "synthetic_failure"
            if succeeded:
                run.status, run.failure_code = "running", ""
                run.save()
                run.status, run.raw_result = "succeeded", copy.deepcopy(EMPTY_RESULT)
            run.save()
            for instance in (run, stale):
                instance.status = "pending"
                with self.subTest(succeeded=succeeded), self.assertRaises(ValidationError):
                    instance.save()
                with self.assertRaises(ValidationError):
                    instance.delete()

    def test_unfinished_run_can_be_deleted_without_deleting_original(self):
        run = self.make_run()
        photo, pk = run.photo, run.pk
        run.delete()
        self.assertFalse(DetectionRun.objects.filter(pk=pk).exists())
        self.assertTrue(TargetPhoto.objects.filter(pk=photo.pk).exists())

    def test_database_enforces_run_lifecycle_and_provenance_shape(self):
        run = self.make_run()
        now = timezone.now()
        for changes in (
            {"status": "invalid"}, {"status": "running"}, {"status": "succeeded"},
            {"status": "failed", "finished_at": now}, {"failure_code": "premature"},
            {"raw_result": EMPTY_RESULT}, {"started_at": now}, {"finished_at": now},
            {"weights_sha256": "invalid"}, {"weights_sha256": "a" * 64 + "\n"},
            {"model_name": ""}, {"runtime_version": ""},
            {"adapter_version": ""},
            {"status": "failed", "failure_code": "error", "started_at": now,
             "finished_at": now - timedelta(seconds=1)},
        ):
            with self.subTest(changes=changes):
                self.assert_database_rejects(run, **changes)

    def test_run_photo_and_trigger_actor_are_protected(self):
        actor = User.objects.create_user("run-trigger@example.com")
        run = self.make_run(triggered_by=actor)
        with self.assertRaises(ProtectedError) as error:
            actor.delete()
        self.assertIn(run, error.exception.protected_objects)
        # Deliberately bypass the photo instance freeze to exercise the FK collector.
        with self.assertRaises(ProtectedError) as error:
            models.Model.delete(run.photo)
        self.assertIn(run, error.exception.protected_objects)


class CalibrationTests(EvidenceTestCase):
    def test_draft_can_be_incomplete_but_acceptance_requires_a_matrix_and_actor(self):
        draft = self.make_calibration(transform_matrix=None)
        self.assertIsNone(draft.transform_matrix)
        draft.status, draft.accepted_by = "accepted", self.actor
        with self.assertRaises(ValidationError):
            draft.save()
        draft.refresh_from_db()
        draft.status, draft.transform_matrix = "accepted", copy.deepcopy(MATRIX)
        with self.assertRaises(ValidationError):
            draft.save()
        draft.refresh_from_db()
        self.assertEqual(draft.status, "draft")
        self.assertIsNone(draft.accepted_at)

    def test_acceptance_records_server_time_matrix_and_published_target(self):
        draft = self.make_calibration()
        self.assertIsNone(draft.accepted_at)
        accepted = self.accept(draft)
        accepted.refresh_from_db()
        self.assertEqual(accepted.status, "accepted")
        self.assertEqual(accepted.transform_matrix, MATRIX)
        self.assertEqual(accepted.accepted_by, self.actor)
        self.assertIsNotNone(accepted.accepted_at)
        self.assertEqual(accepted.target_version, self.target)

    def test_initial_accepted_rejected_and_unknown_statuses_are_rejected(self):
        for status in ("accepted", "rejected", "unknown"):
            with self.subTest(status=status), self.assertRaises(ValidationError):
                self.make_calibration(status=status)

    def test_matrix_rejects_wrong_shape_types_nonfinite_and_singular_values(self):
        for matrix in (
            [], {}, "matrix", [1, 2, 3], [[1, 0], [0, 1]],
            [[1, 0, 0], [0, 1, 0], [0, 0]],
            [[True, 0, 0], [0, 1, 0], [0, 0, 1]],
            [["1", 0, 0], [0, 1, 0], [0, 0, 1]],
            [[float("nan"), 0, 0], [0, 1, 0], [0, 0, 1]],
            [[float("inf"), 0, 0], [0, 1, 0], [0, 0, 1]],
            [[1, 2, 3], [2, 4, 6], [0, 0, 1]],
            [[0, 0, 0], [0, 0, 0], [0, 0, 0]],
        ):
            with self.subTest(matrix=matrix), self.assertRaises(ValidationError):
                self.make_calibration(transform_matrix=matrix)

    def test_invertible_projective_and_small_scale_matrices_are_allowed(self):
        for matrix in (
            [[2, 0, -80], [0, -2, 60], [0.001, 0.002, 1]],
            [[1e-30, 0, 0], [0, 1e-30, 0], [0, 0, 1]],
        ):
            with self.subTest(matrix=matrix):
                calibration = self.make_calibration(transform_matrix=matrix)
                self.accept(calibration)
                self.assertEqual(calibration.transform_matrix, matrix)

    def test_empty_method_version_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.make_calibration(method_version=" \t ")

    def test_draft_can_edit_matrix_and_links_before_acceptance(self):
        draft = self.make_calibration()
        draft.photo = self.make_photo()
        draft.transform_matrix = [[1, 0, -40], [0, -1, 30], [0, 0, 1]]
        draft.method_version = "synthetic-v2"
        draft.save()
        draft.refresh_from_db()
        self.assertEqual(draft.method_version, "synthetic-v2")

    def test_acceptance_uses_stored_target_status_instead_of_cached_object(self):
        draft = self.make_calibration()
        self.target.retire()
        with self.assertRaises(ValidationError):
            self.accept(draft)

    def test_new_acceptance_rejects_draft_target(self):
        target = TargetVersion.objects.create(
            code="synthetic-draft", version=1, name="Synthetic draft", width_mm=Decimal("100"),
            height_mm=Decimal("100"), created_by=self.actor,
        )
        draft = self.make_calibration(target_version=target)
        with self.assertRaises(ValidationError):
            self.accept(draft)

    def test_acceptance_with_invalid_target_fk_raises_validation_error(self):
        draft = self.make_calibration()
        for value in (None, "not-an-id", 999999):
            draft.refresh_from_db()
            draft.target_version_id = value
            with self.subTest(value=value), self.assertRaises(ValidationError):
                self.accept(draft)

    def test_retirement_does_not_modify_previously_accepted_calibration(self):
        accepted = self.accept()
        self.target.retire()
        accepted.refresh_from_db()
        self.assertEqual(accepted.status, "accepted")
        self.assertEqual(accepted.transform_matrix, MATRIX)

    def test_accepted_payload_links_metadata_and_deletion_are_frozen(self):
        accepted = self.accept()
        for field, value in (
            ("transform_matrix", [[1, 0, 0], [0, 1, 0], [0, 0, 1]]),
            ("status", "draft"), ("photo_id", 9999), ("target_version_id", 9999),
            ("method_version", "changed"), ("accepted_by_id", None), ("accepted_at", None),
        ):
            accepted.refresh_from_db()
            setattr(accepted, field, value)
            with self.subTest(field=field), self.assertRaises(ValidationError):
                accepted.save()
        with self.assertRaises(ValidationError):
            accepted.delete()

    def test_stale_draft_cannot_overwrite_or_delete_an_accepted_calibration(self):
        draft = self.make_calibration()
        stale = Calibration.objects.get(pk=draft.pk)
        self.accept(draft)
        stale.method_version = "stale overwrite"
        with self.assertRaises(ValidationError):
            stale.save()
        with self.assertRaises(ValidationError):
            stale.delete()

    def test_rejected_decision_is_terminal_and_does_not_fake_acceptance_metadata(self):
        draft = self.make_calibration(transform_matrix=None)
        draft.status = "rejected"
        draft.save()
        self.assertIsNone(draft.accepted_by_id)
        self.assertIsNone(draft.accepted_at)
        draft.status = "draft"
        with self.assertRaises(ValidationError):
            draft.save()
        with self.assertRaises(ValidationError):
            draft.delete()

    def test_draft_deletion_and_new_calibration_leave_accepted_history_intact(self):
        accepted = self.accept()
        other = self.make_calibration(photo=accepted.photo)
        pk = other.pk
        other.delete()
        self.assertFalse(Calibration.objects.filter(pk=pk).exists())
        self.assertTrue(Calibration.objects.filter(pk=accepted.pk, status="accepted").exists())

    def test_draft_cannot_fake_acceptance_or_change_authorship(self):
        draft = self.make_calibration()
        for field, value in (
            ("accepted_by_id", self.actor.pk), ("accepted_at", timezone.now()),
            ("created_by_id", 9999), ("created_at", timezone.now()),
        ):
            draft.refresh_from_db()
            setattr(draft, field, value)
            with self.subTest(field=field), self.assertRaises(ValidationError):
                draft.save()

    def test_calibration_database_lifecycle_constraints(self):
        draft = self.make_calibration()
        for changes in (
            {"status": "unknown"}, {"method_version": ""}, {"status": "accepted"},
            {"accepted_at": timezone.now()}, {"accepted_by_id": self.actor.pk},
            {"status": "accepted", "accepted_by_id": self.actor.pk,
             "accepted_at": timezone.now(), "transform_matrix": None},
            {"status": "rejected", "accepted_at": timezone.now()},
        ):
            with self.subTest(changes=changes):
                self.assert_database_rejects(draft, **changes)

    def test_all_calibration_foreign_keys_are_protected(self):
        creator = User.objects.create_user("calibration-creator@example.com")
        accepter = User.objects.create_user("calibration-accepter@example.com")
        draft = self.make_calibration(created_by=creator)
        draft.status, draft.accepted_by = "accepted", accepter
        draft.save()
        for parent in (creator, accepter, draft.photo, self.target):
            with self.subTest(parent=type(parent).__name__), self.assertRaises(ProtectedError) as error:
                # Exercise FK protection independently of the parent's historical save/delete guard.
                models.Model.delete(parent)
            self.assertIn(draft, error.exception.protected_objects)


class EvidenceWriteBoundaryTests(EvidenceTestCase):
    def test_bulk_and_partial_writes_are_disabled_for_mutable_and_frozen_records(self):
        for instance in (
            self.make_photo(), self.make_run(), self.finish_run(), self.finish_run(succeeded=False),
            self.make_calibration(), self.accept(),
        ):
            manager = type(instance).objects
            for operation in (
                lambda: manager.filter(pk=instance.pk).update(created_at=timezone.now()),
                lambda: manager.filter(pk=instance.pk).delete(),
                lambda: manager.bulk_create([instance]),
                lambda: manager.bulk_update([instance], ["created_at"]),
                lambda: instance.save(update_fields=["created_at"]),
                lambda: manager.update_or_create(pk=instance.pk, defaults={"created_at": timezone.now()}),
            ):
                with self.subTest(model=type(instance).__name__), self.assertRaises(ValidationError):
                    operation()

    def test_base_and_related_managers_retain_guards(self):
        photo = self.make_photo()
        run = self.make_run(photo=photo)
        calibration = self.make_calibration(photo=photo)
        for instance in (photo, run, calibration):
            with self.subTest(model=type(instance).__name__), self.assertRaises(ValidationError):
                type(instance)._base_manager.filter(pk=instance.pk).update(created_at=timezone.now())
        for manager in (self.session.target_photos, photo.detection_runs, photo.calibrations,
                        self.target.calibrations):
            with self.assertRaises(ValidationError):
                manager.update(created_at=timezone.now())
        destination = self.make_photo()
        for bulk in (True, False):
            with self.subTest(bulk=bulk), self.assertRaises(ValidationError), transaction.atomic():
                destination.detection_runs.add(run, bulk=bulk)
        run.refresh_from_db()
        self.assertEqual(run.photo_id, photo.pk)

    def test_async_operations_preserve_guards(self):
        async def bulk_create(instance):
            await type(instance).objects.abulk_create([instance])

        for instance in (self.make_photo(), self.finish_run(), self.accept()):
            for operation in (
                lambda: async_to_sync(instance.asave)(),
                lambda: async_to_sync(instance.adelete)(),
                lambda: async_to_sync(type(instance).objects.filter(pk=instance.pk).aupdate)(
                    created_at=timezone.now(),
                ),
                lambda: async_to_sync(bulk_create)(instance),
            ):
                with self.subTest(model=type(instance).__name__), self.assertRaises(ValidationError):
                    operation()

    def test_deleted_mutable_records_cannot_be_resurrected_by_stale_objects(self):
        for instance in (self.make_run(), self.make_calibration()):
            stale = type(instance).objects.get(pk=instance.pk)
            instance.delete()
            with self.subTest(model=type(instance).__name__), self.assertRaises(ValidationError):
                stale.save()

    def test_invalid_or_missing_foreign_keys_raise_validation_errors(self):
        photo = self.make_photo()
        for field in ("session_id", "uploaded_by_id"):
            for value in (None, "not-an-id", 999999):
                with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                    self.make_photo(**{field: value})
        for model, base, fields in (
            (DetectionRun, {"photo_id": photo.pk, "triggered_by_id": self.actor.pk,
                            "model_name": "fixture", "weights_sha256": "a" * 64,
                            "runtime_version": "fixture", "adapter_version": "fixture", "parameters": {}},
             ("photo_id", "triggered_by_id")),
            (Calibration, {"photo_id": photo.pk, "target_version_id": self.target.pk,
                           "created_by_id": self.actor.pk, "method_version": "fixture"},
             ("photo_id", "target_version_id", "created_by_id")),
        ):
            for field in fields:
                for value in (None, "not-an-id", 999999):
                    with self.subTest(model=model.__name__, field=field, value=value):
                        with self.assertRaises(ValidationError):
                            model.objects.create(**{**base, field: value})

    def test_raw_sql_boundary_is_explicit_and_not_claimed_to_be_immutable(self):
        photo = self.make_photo()
        run, calibration = self.finish_run(), self.accept()
        for instance, field, value in (
            (photo, "preprocessing_version", "privileged-bypass"),
            (run, "model_name", "privileged-bypass"),
            (calibration, "method_version", "privileged-bypass"),
        ):
            self.raw(type(instance)).filter(pk=instance.pk).update(**{field: value})
            instance.refresh_from_db()
            self.assertEqual(getattr(instance, field), value)


class EvidenceMigrationTests(TransactionTestCase):
    def test_additive_migration_preserves_iteration_two_configuration_data(self):
        before = ("assessments", "0001_target_configuration")
        after = ("assessments", "0002_photo_detection_calibration")
        executor = MigrationExecutor(connection)
        executor.migrate([before])
        try:
            old_apps = executor.loader.project_state([before]).apps
            actor = old_apps.get_model("accounts", "User").objects.create(
                email="migration-fixture@example.com",
            )
            target = old_apps.get_model("assessments", "TargetVersion").objects.create(
                code="migration-fixture", version=1, name="Synthetic target", width_mm=200,
                height_mm=300, status="published", published_at=timezone.now(), created_by_id=actor.pk,
            )
            ring = old_apps.get_model("assessments", "TargetRing").objects.create(
                target_version_id=target.pk, ring_number=1, outer_radius_mm=10, score=1,
            )
            rule = old_apps.get_model("assessments", "ScoringRuleVersion").objects.create(
                code="migration-fixture", version=1, created_by_id=actor.pk,
                algorithm_version="synthetic", boundary_policy="synthetic", numeric_precision="synthetic",
            )
            executor = MigrationExecutor(connection)
            executor.migrate([after])
            new_apps = executor.loader.project_state([after]).apps
            self.assertEqual(new_apps.get_model("assessments", "TargetVersion").objects.get(
                pk=target.pk,
            ).name, "Synthetic target")
            self.assertEqual(new_apps.get_model("assessments", "TargetRing").objects.get(pk=ring.pk).score, 1)
            self.assertEqual(new_apps.get_model("assessments", "ScoringRuleVersion").objects.get(
                pk=rule.pk,
            ).algorithm_version, "synthetic")
            for name in ("TargetPhoto", "DetectionRun", "Calibration"):
                self.assertEqual(new_apps.get_model("assessments", name).objects.count(), 0)
        finally:
            # Restore the full schema even if an assertion fails.
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
