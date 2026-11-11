"""Synthetic originals and HTTPX MockTransport; never contact an inference server."""

import copy
import json
import logging
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
from uuid import uuid4, uuid5

import httpx
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.db import connection, models, transaction
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.utils import timezone
from PIL import Image

from accounts.models import Participant
from activities.models import Activity, ActivityParticipation, ActivityPermissionGrant, TrainingSession
from activities.test_history import traced_locks
from assessments import detection_client as client, detection_services as commands
from assessments.models import AuditEvent, DetectionRun, TargetPhoto
from assessments.services import OperationConflict

User = get_user_model()
PROVENANCE = {"model_name": "ultralytics.SyntheticNetwork", "weights_sha256": "a" * 64,
    "runtime_version": "ultralytics/synthetic", "adapter_version": client.ADAPTER_VERSION,
    "parameters": {"conf": 0.2, "iou": 0.7, "imgsz": 640, "max_det": 300, "device": "cpu",
                   "half": False, "augment": False, "rect": False, "batch": 1,
                   "stream": False, "save": False, "verbose": False}}
CONFIG = {"endpoint": "http://127.0.0.1:8001/api/evaluate", "provenance": PROVENANCE,
          "timeouts": {"connect": 3, "read": 30, "write": 10, "pool": 1, "total": 60}}


class ConfigurationTests(SimpleTestCase):
    def test_only_explicit_internal_literal_addresses_and_bounded_configuration(self):
        for endpoint in ("http://127.0.0.1:8001/api/evaluate", "http://[::1]:8001/api/evaluate",
                         "https://10.1.2.3/api/evaluate", "https://[fd00::1]/api/evaluate"):
            with self.subTest(endpoint=endpoint), override_settings(DETECTION_SERVICE={**CONFIG, "endpoint": endpoint}):
                self.assertEqual(client.Configuration.current().endpoint, endpoint)
        for endpoint in ("https://8.8.8.8/api/evaluate", "http://169.254.169.254/api/evaluate",
                "http://10.1.2.3/api/evaluate", "http://localhost:8001/api/evaluate",
                "http://ai-engine/api/evaluate", "file:///api/evaluate", "http://127.0.0.1:0/api/evaluate",
                "http://user:password@127.0.0.1/api/evaluate", "http://127.0.0.1/api/evaluate?target=other",
                "http://127.0.0.1/api/evaluate#secret", "http://127.0.0.1/other"):
            with self.subTest(endpoint=endpoint), override_settings(DETECTION_SERVICE={**CONFIG, "endpoint": endpoint}):
                with self.assertRaises(ImproperlyConfigured):
                    client.Configuration.current()

    def test_missing_bad_provenance_secret_parameters_and_invalid_timeouts_fail_closed(self):
        bad = [None, {}, {**CONFIG, "unexpected": True}]
        for field, value in (("weights_sha256", "unknown"), ("adapter_version", "unapproved"), ("model_name", "")):
            bad.append({**CONFIG, "provenance": {**PROVENANCE, field: value}})
        bad.append({**CONFIG, "provenance": {**PROVENANCE, "parameters": {**PROVENANCE["parameters"], "token": "must-not-be-stored"}}})
        for value in (0, True, float("inf"), 999):
            bad.append({**CONFIG, "timeouts": {**CONFIG["timeouts"], "read": value}})
        for value in bad:
            with self.subTest(value=value), override_settings(DETECTION_SERVICE=value), self.assertRaises(ImproperlyConfigured):
                client.Configuration.current()


class DetectionServiceTests(TransactionTestCase):
    def setUp(self):
        http_log = logging.getLogger("httpx")
        old_level = http_log.level
        http_log.setLevel(logging.WARNING)
        self.addCleanup(http_log.setLevel, old_level)
        self.directory = self.enterContext(TemporaryDirectory(prefix="django-detection-integration-"))
        self.enterContext(override_settings(PRIVATE_TARGET_PHOTO_ROOT=self.directory,
            STORAGES=copy.deepcopy(settings.STORAGES), DETECTION_SERVICE=copy.deepcopy(CONFIG)))
        self.actor = User.objects.create_user("synthetic-inference@example.com")
        person = Participant.objects.create(full_name="Synthetic participant", identity_status="verified",
            verified_by=self.actor, verified_at=timezone.now())
        self.activity = Activity.objects.create(name="Synthetic activity", training_date=timezone.localdate(),
            status="active", created_by=self.actor)
        participation = ActivityParticipation.objects.create(activity=self.activity, participant=person,
            registration_status="approved", participant_identifier="synthetic", decided_by=self.actor, decided_at=timezone.now())
        self.session = TrainingSession.objects.create(participation=participation, session_number=1,
            assigned_assessor=self.actor, created_by=self.actor, weapon_type="pistol", shooting_position="berdiri",
            distance_m=Decimal("15"), caliber_mm=Decimal("9"))
        self.permission = Permission.objects.get(content_type__app_label="assessments", codename="change_assessment")
        self.actor.user_permissions.add(self.permission)
        self.grant = ActivityPermissionGrant.objects.create(user=self.actor, activity=self.activity,
            permission=self.permission, created_by=self.actor)
        stream = BytesIO()
        Image.new("RGB", (80, 60), "white").save(stream, format="PNG")
        self.contents = stream.getvalue()
        self.storage = storages["target_photos"]
        key = self.storage.save(f"originals/{uuid4().hex}.png", ContentFile(self.contents))
        self.photo = TargetPhoto.objects.create(session=self.session, uploaded_by=self.actor,
            storage_key=key, preprocessing_version=client.PREPROCESSING_VERSION, analysis_width_px=80, analysis_height_px=60)
        self.operation_id = uuid4()
        self.handler = Mock(side_effect=lambda request: self.response(self.payload()))
        self.transport = self.enterContext(patch.object(client.httpx, "HTTPTransport",
            side_effect=lambda **kwargs: httpx.MockTransport(self.handler)))

    def payload(self, **changes):
        return {**copy.deepcopy(PROVENANCE), "status": "succeeded", "schema_version": 1,
            "detections": [{"id": "synthetic-1", "class_id": 0, "confidence": 0.875, "bbox_xyxy": [1.125, 2.25, 8.875, 9.5]}],
            "analysis_width_px": 80, "analysis_height_px": 60, "preprocessing_version": client.PREPROCESSING_VERSION,
            "input_sha256": self.photo.sha256, "checkpoint_runtime_version": "synthetic", **changes}

    def response(self, payload=None, *, status=200, body=None, headers=None):
        return httpx.Response(status, headers={"content-type": "application/json", **(headers or {})},
            stream=httpx.ByteStream(body if body is not None else json.dumps(payload).encode()))

    def call(self, **changes):
        return commands.detect_photo(**{**dict(actor=self.actor, session_id=self.session.pk, photo_id=self.photo.pk,
            operation_id=self.operation_id, reason="Synthetic inference operation"), **changes})

    def state(self):
        return {model.__name__: list(model.objects.order_by("pk").values()) for model in (DetectionRun, AuditEvent, TargetPhoto, TrainingSession)}

    def test_complete_lifecycle_audits_and_no_database_transaction_during_http(self):
        observed = []
        save = DetectionRun.save
        def saved(run, *args, **kwargs):
            observed.append(str(run.status))
            return save(run, *args, **kwargs)
        def inspect_request(request):
            self.assertFalse(connection.in_atomic_block)
            self.assertTrue(transaction.get_autocommit())
            self.assertEqual(DetectionRun.objects.get().status, "running")
            self.assertEqual(AuditEvent.objects.count(), 1)
            self.assertIn(self.contents, request.content)
            self.assertNotIn(self.photo.storage_key.encode(), request.content)
            self.assertEqual(str(request.url), CONFIG["endpoint"])
            self.assertEqual(request.extensions["timeout"], {"connect": 3.0, "read": 30.0, "write": 10.0, "pool": 1.0})
            return self.response(self.payload())
        self.handler.side_effect = inspect_request
        with patch.object(DetectionRun, "save", autospec=True, side_effect=saved):
            receipt = self.call()
        self.assertEqual(observed, ["pending", "running", "succeeded"])
        run = DetectionRun.objects.get(pk=receipt.run_id)
        self.assertEqual(receipt.status, "succeeded")
        self.assertEqual(run.raw_result, self.payload())
        self.assertIsNotNone(run.started_at)
        self.assertGreaterEqual(run.finished_at, run.started_at)
        root = AuditEvent.objects.get(operation_id=self.operation_id)
        terminal = AuditEvent.objects.get(operation_id=uuid5(self.operation_id, commands.FINISH_ACTION))
        self.assertEqual(receipt.audit_event_id, terminal.pk)
        self.assertEqual(terminal.caused_by_event_id, root.pk)
        self.assertEqual(root.after_state["lifecycle"], ["pending", "running"])
        self.assertIsNone(root.before_state["run"])
        self.assertEqual(terminal.after_state["run"]["status"], "succeeded")
        for event in (root, terminal):
            self.assertEqual((event.actor_kind, event.actor_user_id, event.session_id), ("user", self.actor.pk, self.session.pk))
            for field in ("activity_id", "participation_id", "assessment_id", "version_before", "version_after"):
                self.assertIsNone(getattr(event, field))

    def test_legacy_session_remains_compatible_with_detection_full_clean(self):
        # Explicit pre-policy context; no existing snapshot/audit is rewritten.
        models.QuerySet(model=TrainingSession, using="default").filter(pk=self.session.pk).update(
            distance_m=Decimal("10"), shooting_position="duduk")
        before = {model: list(model.objects.order_by("pk").values()) for model in (TrainingSession, TargetPhoto)}
        receipt = self.call()
        self.assertEqual(receipt.status, "succeeded")
        self.assertEqual({model: list(model.objects.order_by("pk").values()) for model in before}, before)
        root = AuditEvent.objects.get(operation_id=self.operation_id)
        self.assertEqual(root.after_state["session_context"]["distance_m"], "10.00")
        self.assertEqual(root.after_state["session_context"]["shooting_position"], "duduk")

    def test_empty_success_is_not_failure(self):
        self.handler.side_effect = lambda request: self.response(self.payload(detections=[]))
        receipt = self.call()
        self.assertEqual((receipt.status, receipt.failure_code), ("succeeded", ""))
        self.assertEqual(DetectionRun.objects.get().raw_result["detections"], [])

    def test_connection_policy_disables_proxies_redirects_retries_and_keepalive(self):
        constructor = httpx.Client
        with patch.object(client.httpx, "Client", wraps=constructor) as factory:
            self.call()
        options = factory.call_args.kwargs
        self.assertFalse(options["trust_env"])
        self.assertFalse(options["follow_redirects"])
        self.assertEqual(options["limits"].max_connections, 1)
        self.assertEqual(options["limits"].max_keepalive_connections, 0)
        self.assertEqual(self.transport.call_args.kwargs["retries"], 0)
        self.assertFalse(self.transport.call_args.kwargs["trust_env"])
        self.assertTrue(self.transport.call_args.kwargs["verify"])

    def test_outer_transaction_is_rejected_before_creating_or_dispatching(self):
        with transaction.atomic(), self.assertRaises(ValidationError):
            self.call()
        self.assertFalse(DetectionRun.objects.exists())
        self.handler.assert_not_called()

    def test_caller_cannot_supply_provenance_urls_actor_ids_or_results(self):
        for name in ("endpoint", "parameters", "weights_sha256", "model_name", "raw_result", "actor_id"):
            with self.subTest(name=name), self.assertRaises(TypeError):
                self.call(**{name: "untrusted"})
        self.assertFalse(DetectionRun.objects.exists())

    def test_unauthorized_without_permission_scope_or_assignment_and_inactive(self):
        self.assertTrue(self.actor.has_perm("assessments.change_assessment"))
        self.actor.user_permissions.clear()
        with self.assertRaises(PermissionDenied):
            self.call()
        self.actor.user_permissions.add(self.permission)
        self.grant.is_active = False
        self.grant.save()
        self.actor.is_staff = self.actor.is_superuser = True
        self.actor.save()
        with self.assertRaises(PermissionDenied):
            self.call()
        self.grant.is_active = True
        self.grant.save()
        other = User.objects.create_user("other-synthetic-inference@example.com")
        other.user_permissions.add(self.permission)
        ActivityPermissionGrant.objects.create(user=other, activity=self.activity, permission=self.permission, created_by=self.actor)
        with self.assertRaises(PermissionDenied):
            self.call(actor=other)
        User.objects.filter(pk=self.actor.pk).update(is_active=False)
        with self.assertRaises(PermissionDenied):
            self.call()
        self.assertFalse(DetectionRun.objects.exists())
        self.handler.assert_not_called()

    def test_foreign_activity_and_photo_session_mismatch_do_not_leak_data(self):
        activity = Activity.objects.create(name="Other synthetic", training_date=timezone.localdate(), status="active", created_by=self.actor)
        person = self.session.participation.participant
        participation = ActivityParticipation.objects.create(activity=activity, participant=person, registration_status="approved",
            participant_identifier="other", decided_by=self.actor, decided_at=timezone.now())
        session = TrainingSession.objects.create(participation=participation, session_number=1, assigned_assessor=self.actor,
            created_by=self.actor, weapon_type="pistol", shooting_position="berdiri", distance_m=15, caliber_mm=9)
        with self.assertRaises(PermissionDenied):
            self.call(session_id=session.pk)
        ActivityPermissionGrant.objects.create(user=self.actor, activity=activity, permission=self.permission, created_by=self.actor)
        with self.assertRaises(ValidationError):
            self.call(session_id=session.pk)
        self.assertFalse(DetectionRun.objects.exists())
        self.handler.assert_not_called()

    def test_unconfigured_endpoint_and_unsupported_preprocessing_never_dispatch(self):
        with override_settings(DETECTION_SERVICE=None), self.assertRaises(ImproperlyConfigured):
            self.call()
        models.QuerySet(model=TargetPhoto).filter(pk=self.photo.pk).update(preprocessing_version="unmatched-old-contract")
        with self.assertRaises(ValidationError):
            self.call()
        self.assertFalse(DetectionRun.objects.exists())
        self.handler.assert_not_called()

    def test_identical_retry_returns_receipt_without_network_even_after_configuration_disabled(self):
        first = self.call()
        before = self.state()
        with override_settings(DETECTION_SERVICE=None):
            self.assertEqual(self.call(reason=" Synthetic inference operation "), first)
        self.assertEqual(self.state(), before)
        self.handler.assert_called_once()

    def test_duplicate_while_running_does_not_dispatch_again(self):
        def duplicate(request):
            nested = self.call()
            self.assertEqual(nested.status, "running")
            self.assertEqual(DetectionRun.objects.count(), 1)
            return self.response(self.payload())
        self.handler.side_effect = duplicate
        self.assertEqual(self.call().status, "succeeded")
        self.handler.assert_called_once()
        self.assertEqual(AuditEvent.objects.count(), 2)

    def test_uuid_reuse_with_other_reason_or_command_is_conflict(self):
        self.call()
        before = self.state()
        with self.assertRaises(OperationConflict):
            self.call(reason="Different operation")
        self.assertEqual(self.state(), before)
        self.handler.assert_called_once()
        operation = uuid4()
        AuditEvent.objects.create(operation_id=operation, actor_kind="user", actor_user=self.actor, session=self.session,
            action="synthetic.other", before_state={"schema_version": 1}, after_state={"schema_version": 1})
        with self.assertRaises(OperationConflict):
            self.call(operation_id=operation)
        self.assertEqual(DetectionRun.objects.count(), 1)

    def test_revoked_scope_prevents_historical_replay(self):
        self.call()
        self.grant.is_active = False
        self.grant.save()
        before = self.state()
        with self.assertRaises(PermissionDenied):
            self.call()
        self.assertEqual(self.state(), before)
        self.handler.assert_called_once()

    def test_http_timeout_and_connection_error_are_audited_failed_runs(self):
        for exception, code in ((httpx.ReadTimeout("synthetic timeout"), "INFERENCE_TIMEOUT"),
                                (httpx.ConnectError("synthetic connection failure"), "INFERENCE_UNAVAILABLE")):
            self.handler.side_effect = exception
            receipt = self.call(operation_id=uuid4())
            run = DetectionRun.objects.get(pk=receipt.run_id)
            self.assertEqual((run.status, run.failure_code, run.raw_result), ("failed", code, None))
        self.assertEqual(AuditEvent.objects.count(), 4)

    def test_redirect_and_remote_failure_are_never_successful_misses(self):
        for status in (302, 400, 500, 503):
            self.handler.side_effect = lambda request, status=status: self.response({"status": "failed"}, status=status)
            receipt = self.call(operation_id=uuid4())
            run = DetectionRun.objects.get(pk=receipt.run_id)
            self.assertEqual((run.status, run.failure_code, run.raw_result), ("failed", "REMOTE_HTTP_ERROR", None))
        self.assertEqual(self.handler.call_count, 4)

    def test_input_hash_dimensions_and_preprocessing_mismatch_reject_results(self):
        for changes in ({"input_sha256": "b" * 64}, {"analysis_width_px": 81}, {"analysis_height_px": True},
                        {"preprocessing_version": "unapproved"}):
            self.handler.side_effect = lambda request, changes=changes: self.response(self.payload(**changes))
            receipt = self.call(operation_id=uuid4())
            run = DetectionRun.objects.get(pk=receipt.run_id)
            self.assertEqual(run.status, "failed")
            self.assertIsNone(run.raw_result)

    def test_every_provenance_field_is_pinned_and_cannot_be_overwritten(self):
        for key, value in (("model_name", "unverified-model"), ("weights_sha256", "b" * 64),
                ("runtime_version", "other-runtime"), ("adapter_version", "other-adapter"),
                ("parameters", {**PROVENANCE["parameters"], "conf": 0.5})):
            self.handler.side_effect = lambda request, key=key, value=value: self.response(self.payload(**{key: value}))
            receipt = self.call(operation_id=uuid4())
            run = DetectionRun.objects.get(pk=receipt.run_id)
            self.assertEqual(run.failure_code, "PROVENANCE_MISMATCH")
            self.assertEqual({k: getattr(run, k) for k in client.PROVENANCE_FIELDS}, PROVENANCE)
            self.assertIsNone(run.raw_result)

    def test_malformed_duplicate_nonfinite_and_invalid_detection_payloads_are_not_stored(self):
        cases = [b"not json", b'{"status":"failed","status":"succeeded"}',
            json.dumps(self.payload(status="failed")).encode(), json.dumps(self.payload(schema_version=True)).encode(),
            json.dumps(self.payload(detections=[{"id": "x", "class_id": 0, "confidence": float("nan"), "bbox_xyxy": [0, 0, 1, 1]}])).encode(),
            json.dumps(self.payload(detections=[{"id": "x", "class_id": 0, "confidence": 0.8, "bbox_xyxy": [0, 0, 100, 100]}])).encode(),
            json.dumps({**self.payload(), "token": "must not persist"}).encode()]
        for body in cases:
            self.handler.side_effect = lambda request, body=body: self.response(body=body)
            receipt = self.call(operation_id=uuid4())
            run = DetectionRun.objects.get(pk=receipt.run_id)
            self.assertEqual(run.status, "failed")
            self.assertIsNone(run.raw_result)

    def test_oversized_compressed_and_wrong_content_type_responses_are_bounded(self):
        cases = ((b"x", {"content-length": str(client.MAX_RESPONSE + 1)}),
                 (b"x" * (client.MAX_RESPONSE + 1), {}), (b"x", {"content-encoding": "gzip"}),
                 (b"x", {"content-type": "text/html"}))
        for body, headers in cases:
            self.handler.side_effect = lambda request, body=body, headers=headers: self.response(body=body, headers=headers)
            receipt = self.call(operation_id=uuid4())
            self.assertEqual(receipt.failure_code, "INVALID_RESPONSE")
            self.assertIsNone(DetectionRun.objects.get(pk=receipt.run_id).raw_result)

    def test_tampered_original_is_not_sent_and_database_identity_is_preserved(self):
        Path(self.storage.path(self.photo.storage_key)).write_bytes(b"changed synthetic original")
        original = TargetPhoto.objects.values().get(pk=self.photo.pk)
        receipt = self.call()
        self.assertEqual(receipt.failure_code, "ORIGINAL_MISMATCH")
        self.assertEqual(TargetPhoto.objects.values().get(pk=self.photo.pk), original)
        self.handler.assert_not_called()

    def test_missing_original_is_audited_without_http_or_raw_result(self):
        Path(self.storage.path(self.photo.storage_key)).unlink()
        receipt = self.call()
        self.assertEqual(receipt.failure_code, "ORIGINAL_UNAVAILABLE")
        self.assertIsNone(DetectionRun.objects.get().raw_result)
        self.handler.assert_not_called()

    def test_original_mime_metadata_is_reverified_not_just_the_digest(self):
        models.QuerySet(model=TargetPhoto).filter(pk=self.photo.pk).update(verified_mime_type="image/jpeg")
        receipt = self.call()
        self.assertEqual(receipt.failure_code, "ORIGINAL_MISMATCH")
        self.assertIsNone(DetectionRun.objects.get().raw_result)
        self.handler.assert_not_called()

    def test_original_changed_during_inference_invalidates_success(self):
        def changed(request):
            Path(self.storage.path(self.photo.storage_key)).write_bytes(b"changed while inferring")
            return self.response(self.payload())
        self.handler.side_effect = changed
        receipt = self.call()
        self.assertEqual(receipt.failure_code, "ORIGINAL_MISMATCH")
        self.assertIsNone(DetectionRun.objects.get().raw_result)

    def test_private_storage_is_opened_read_only_and_no_public_url_is_used(self):
        with (patch.object(self.storage, "open", wraps=self.storage.open) as opened,
              patch.object(self.storage, "url", side_effect=AssertionError("No public URL"))):
            self.call()
        self.assertEqual([call.args[1] for call in opened.call_args_list], ["rb", "rb"])

    def test_client_admission_limit_fails_without_http_and_releases_slots(self):
        self.assertTrue(client.HTTP_SLOTS.acquire(blocking=False))
        self.assertTrue(client.HTTP_SLOTS.acquire(blocking=False))
        try:
            receipt = self.call()
            self.assertEqual(receipt.failure_code, "CLIENT_BUSY")
            self.handler.assert_not_called()
        finally:
            client.HTTP_SLOTS.release()
            client.HTTP_SLOTS.release()

    def test_start_audit_failure_rolls_back_both_initial_run_transitions(self):
        with patch.object(AuditEvent, "save", side_effect=ValidationError("Synthetic audit failure")), self.assertRaises(ValidationError):
            self.call()
        self.assertFalse(DetectionRun.objects.exists())
        self.assertFalse(AuditEvent.objects.exists())
        self.handler.assert_not_called()

    def test_terminal_audit_failure_rolls_back_only_terminal_phase_and_never_resends(self):
        save = AuditEvent.save
        def audited(event, *args, **kwargs):
            if event.action == commands.FINISH_ACTION:
                raise ValidationError("Synthetic terminal audit failure")
            return save(event, *args, **kwargs)
        with patch.object(AuditEvent, "save", autospec=True, side_effect=audited), self.assertRaises(ValidationError):
            self.call()
        run = DetectionRun.objects.get()
        self.assertEqual((run.status, run.raw_result, run.finished_at), ("running", None, None))
        self.assertEqual(AuditEvent.objects.count(), 1)
        self.assertEqual(self.call().status, "running")
        self.handler.assert_called_once()

    def test_derived_uuid_collision_rolls_back_result_instead_of_overwriting_audit(self):
        def collision(request):
            AuditEvent.objects.create(operation_id=uuid5(self.operation_id, commands.FINISH_ACTION),
                actor_kind="user", actor_user=self.actor, session=self.session, action="synthetic.other",
                before_state={"schema_version": 1}, after_state={"schema_version": 1})
            return self.response(self.payload())
        self.handler.side_effect = collision
        with self.assertRaises(OperationConflict):
            self.call()
        self.assertEqual(DetectionRun.objects.get().status, "running")
        self.assertIsNone(DetectionRun.objects.get().raw_result)

    def test_authority_revoked_during_http_prevents_final_writes(self):
        def revoked(request):
            grant = ActivityPermissionGrant.objects.get(pk=self.grant.pk)
            grant.is_active = False
            grant.save()
            return self.response(self.payload())
        self.handler.side_effect = revoked
        with self.assertRaises(PermissionDenied):
            self.call()
        self.assertEqual(DetectionRun.objects.get().status, "running")
        self.assertIsNone(DetectionRun.objects.get().raw_result)
        self.assertEqual(AuditEvent.objects.count(), 1)

    def test_session_or_photo_context_drift_prevents_terminal_write(self):
        for model, key, value in ((TrainingSession, "distance_m", Decimal("20")),
                                  (TargetPhoto, "preprocessing_version", "tampered")):
            old = getattr(self.session if model is TrainingSession else self.photo, key)
            def changed(request, model=model, key=key, value=value):
                models.QuerySet(model=model).filter(pk=self.session.pk if model is TrainingSession else self.photo.pk).update(**{key: value})
                return self.response(self.payload())
            self.handler.side_effect = changed
            with self.subTest(model=model.__name__), self.assertRaises(ValidationError):
                self.call(operation_id=uuid4())
            self.assertFalse(DetectionRun.objects.filter(raw_result__isnull=False).exists())
            models.QuerySet(model=model).filter(pk=self.session.pk if model is TrainingSession else self.photo.pk).update(**{key: old})

    def test_external_terminal_state_is_not_overwritten_or_claimed_as_audited_receipt(self):
        def changed(request):
            run = DetectionRun.objects.get()
            run.status, run.failure_code = "failed", "SYNTHETIC_EXTERNAL_TRANSITION"
            run.save()
            return self.response(self.payload())
        self.handler.side_effect = changed
        with self.assertRaises(ValidationError):
            self.call()
        self.assertEqual(DetectionRun.objects.get().failure_code, "SYNTHETIC_EXTERNAL_TRANSITION")
        with self.assertRaises(ValidationError):
            self.call()
        self.handler.assert_called_once()

    def test_noop_external_run_save_is_detected_as_stale(self):
        def changed(request):
            DetectionRun.objects.get().save()
            return self.response(self.payload())
        self.handler.side_effect = changed
        with self.assertRaises(ValidationError):
            self.call()
        self.assertEqual(DetectionRun.objects.get().status, "running")
        self.assertEqual(AuditEvent.objects.count(), 1)

    def test_audit_allowlist_excludes_private_locations_credentials_and_raw_results(self):
        self.call()
        def inspect(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    self.assertNotIn(key, {"storage_key", "endpoint", "password", "token", "authorization", "raw_result"})
                    inspect(child)
            elif isinstance(value, list):
                for child in value:
                    inspect(child)
        for event in AuditEvent.objects.all():
            inspect(event.before_state)
            inspect(event.after_state)

    def test_lock_order_across_both_phases_preserves_domain_then_actor_suffix(self):
        boundary = []
        def observed(request):
            boundary.append(len(trace))
            return self.response(self.payload())
        self.handler.side_effect = observed
        with traced_locks() as trace:
            self.call()
        phases = []
        for rows in (trace[:boundary[0]], trace[boundary[0]:]):
            acquired = []
            for name, ids, joined in rows:
                self.assertFalse(joined)
                if ids and name not in acquired:
                    acquired.append(name)
            phases.append(acquired)
        self.assertEqual(phases[0][:5], ["TrainingSession", "ActivityParticipation", "TargetPhoto", "User", "ActivityPermissionGrant"])
        self.assertEqual(phases[1], ["TrainingSession", "ActivityParticipation", "TargetPhoto", "DetectionRun", "User", "ActivityPermissionGrant"])
