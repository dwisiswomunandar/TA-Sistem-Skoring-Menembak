"""Test-only approvals and synthetic context data; never operational approvals."""

import copy
import json
from datetime import date, datetime
from io import StringIO
from unittest.mock import mock_open, patch
from uuid import UUID, uuid4

from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, connection, models, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from accounts.models import Participant
from assessments.models import AuditEvent
from . import context_provisioning as provisioning
from .grant_services import OperationConflict
from .management.commands import provision_operational_context as cli
from .models import Activity, ActivityParticipation, ActivityPermissionGrant, TrainingSession
from .read_access import readable_sessions
from .session_creation import create_training_session

User = get_user_model()


class ContextProvisioningTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.password = "Synthetic-Provisioning-Password-728!"
        cls.actor = User.objects.create_user("provisioner@example.com", cls.password)
        cls.other = User.objects.create_user("other@example.com", cls.password, is_staff=True, is_superuser=True)
        cls.existing_activity = Activity.objects.create(
            name="Existing foreign activity", training_date=timezone.localdate(), status="active", created_by=cls.other,
        )
        cls.existing_person = Participant.objects.create(
            full_name="Existing foreign participant", identity_status="verified", verified_by=cls.other,
            verified_at=timezone.now(),
        )
        cls.existing_participation = ActivityParticipation.objects.create(
            activity=cls.existing_activity, participant=cls.existing_person, registration_status="approved",
            participant_identifier="EXISTING-001", decided_by=cls.other, decided_at=timezone.now(),
        )

    def setUp(self):
        self.request = {
            "operation_id": str(uuid4()), "purpose": "development",
            "activity": {"name": "Synthetic new training", "training_date": "2026-11-12"},
            "participant": {"full_name": "Synthetic new person", "identity_number": "SYNTHETIC-ID"},
            "participation": {"participant_identifier": "NEW-001"},
            "reason": "Synthetic test request only", "authorization_reference": "TEST-ONLY-DATA-AUTHORIZATION",
        }
        self.plan = {"actor_id": self.actor.pk, **copy.deepcopy(self.request)}
        self.enterContext(self.approved())

    def approved(self, *plans):
        return override_settings(OPERATIONAL_CONTEXT_PROVISIONING_ENABLED=True,
                                 OPERATIONAL_CONTEXT_PROVISIONING_PLANS=plans or (self.plan,))

    def call(self, *, request=None, **kwargs):
        values = {"actor_email": self.actor.email, "actor_password": self.password,
                  "data": copy.deepcopy(self.request if request is None else request)}
        values.update(kwargs)
        return provisioning.provision_operational_context(**values)

    def state(self):
        domain = {model.__name__: list(model.objects.order_by("pk").values()) for model in (
            Activity, Participant, ActivityParticipation, TrainingSession, ActivityPermissionGrant, AuditEvent, User,
        )}
        domain["user_permissions"] = list(User.user_permissions.through.objects.order_by("pk").values())
        domain["user_groups"] = list(User.groups.through.objects.order_by("pk").values())
        return domain

    def test_plain_authenticated_actor_with_exact_plan_creates_only_pending_context(self):
        receipt = self.call()
        activity = Activity.objects.get(pk=receipt.activity_id)
        person = Participant.objects.get(pk=receipt.participant_id)
        part = ActivityParticipation.objects.get(pk=receipt.participation_id)
        self.assertEqual((activity.status, person.identity_status, part.registration_status), ("draft", "pending", "pending"))
        self.assertEqual((activity.created_by_id, part.registered_by_id), (self.actor.pk, self.actor.pk))
        self.assertEqual((part.activity_id, part.participant_id), (activity.pk, person.pk))
        self.assertIsNone(person.account_id)
        self.assertIsNone(person.verified_by_id)
        self.assertIsNone(person.verified_at)
        self.assertIsNone(part.decided_by_id)
        self.assertIsNone(part.decided_at)
        self.assertFalse(TrainingSession.objects.exists())
        self.assertFalse(ActivityPermissionGrant.objects.exists())
        self.assertFalse(self.actor.user_permissions.exists())
        self.assertFalse(self.actor.groups.exists())
        self.assertFalse(self.actor.is_staff)
        self.assertFalse(self.actor.is_superuser)

    def test_default_deny_precedes_authentication_or_domain_queries(self):
        before = self.state()
        with override_settings(OPERATIONAL_CONTEXT_PROVISIONING_ENABLED=False), patch.object(provisioning, "authenticate") as auth:
            with self.assertNumQueries(0), self.assertRaises(PermissionDenied):
                self.call()
            auth.assert_not_called()
        self.assertEqual(self.state(), before)

    def test_enabled_flag_without_plan_does_not_grant_authority(self):
        with override_settings(OPERATIONAL_CONTEXT_PROVISIONING_PLANS=()):
            with self.assertNumQueries(0), self.assertRaises(ImproperlyConfigured):
                self.call()

    def test_staff_superuser_creator_and_group_membership_are_not_authority(self):
        group = Group.objects.create(name="Synthetic unsupported role")
        self.other.groups.add(group)
        before = self.state()
        with self.assertRaises(PermissionDenied):
            self.call(actor_email=self.other.email)
        self.assertEqual(self.state(), before)

    def test_wrong_password_inactive_user_and_wrong_database_are_denied(self):
        before = self.state()
        for kwargs in ({"actor_password": "wrong"}, {"actor_password": None}, {"using": "other"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(PermissionDenied):
                self.call(**kwargs)
        self.assertEqual(self.state(), before)
        self.actor.is_active = False
        self.actor.save(update_fields=("is_active",))
        with self.assertRaises(PermissionDenied):
            self.call()
        self.assertEqual(Activity.objects.count(), 1)

    def test_permission_reference_or_request_alone_is_not_authority(self):
        changed = copy.deepcopy(self.request)
        changed["authorization_reference"] = "CLIENT-CLAIM"
        with self.assertRaises(PermissionDenied):
            self.call(request=changed)
        with self.assertRaises(TypeError):
            self.call(actor=self.actor)
        self.assertEqual(Activity.objects.count(), 1)

    def test_invalid_protected_configuration_fails_closed_without_queries(self):
        cases = (
            ("true", (self.plan,)), (True, {}), (True, ()), (True, ({},)),
            (True, (self.plan, self.plan)), (True, ({**self.plan, "actor_id": True},)),
            (True, ({**self.plan, "actor_id": 0},)),
            (True, ({**self.plan, "permissions": ["all"]},)),
            (True, ({**self.plan, "authorization_reference": " "},)),
        )
        for enabled, plans in cases:
            with self.subTest(enabled=enabled, plans=plans), override_settings(
                OPERATIONAL_CONTEXT_PROVISIONING_ENABLED=enabled, OPERATIONAL_CONTEXT_PROVISIONING_PLANS=plans,
            ):
                with self.assertNumQueries(0), self.assertRaises(ImproperlyConfigured):
                    self.call()

    def test_invalid_or_mass_assigned_fields_write_nothing(self):
        invalid = []
        for key, value in (("actor_id", self.other.pk), ("activity_id", self.existing_activity.pk),
                           ("allow_all", True), ("approval", True), ("operation_id", True),
                           ("operation_id", "bad"), ("purpose", []), ("purpose", "test"),
                           ("reason", " "), ("authorization_reference", " ")):
            invalid.append({**copy.deepcopy(self.request), key: value})
        for section, key, value in (
            ("activity", "status", "active"), ("activity", "created_by", self.other.pk),
            ("activity", "name", " "), ("activity", "name", "x" * 256),
            ("activity", "name", "bad\x00name"), ("activity", "name", "bad\ud800name"),
            ("activity", "training_date", "2026-02-30"), ("activity", "training_date", "20261112"),
            ("activity", "training_date", datetime(2026, 11, 12)),
            ("participant", "identity_status", "verified"), ("participant", "account", self.other.pk),
            ("participant", "verified_by", self.other.pk), ("participant", "full_name", False),
            ("participant", "identity_number", "x" * 65),
            ("participation", "registration_status", "approved"),
            ("participation", "participant_id", self.existing_person.pk),
            ("participation", "decided_by", self.other.pk), ("participation", "participant_identifier", "x" * 65),
        ):
            data = copy.deepcopy(self.request)
            data[section][key] = value
            invalid.append(data)
        before = self.state()
        for data in (None, [], {}, *invalid):
            with self.subTest(data=data), self.assertRaises(ValidationError):
                self.call(data=data)
            self.assertEqual(self.state(), before)

    def test_valid_but_unapproved_payload_differences_are_denied(self):
        for section, key, value in (("activity", "name", "Different activity"),
                                    ("participant", "full_name", "Different person"),
                                    ("participation", "participant_identifier", "OTHER")):
            data = copy.deepcopy(self.request)
            data[section][key] = value
            with self.subTest(section=section), self.assertRaises(PermissionDenied):
                self.call(request=data)
        self.assertEqual(Activity.objects.count(), 1)

    def test_whitespace_uuid_and_date_normalization_keep_same_operation(self):
        data = copy.deepcopy(self.request)
        data["operation_id"] = UUID(data["operation_id"])
        data["activity"]["name"] = "  " + data["activity"]["name"] + "  "
        data["activity"]["training_date"] = date(2026, 11, 12)
        data["participant"]["full_name"] += " "
        data["reason"] += " "
        self.assertEqual(self.call(request=data), self.call())

    def test_blank_optional_identifiers_use_existing_model_normalization(self):
        data = copy.deepcopy(self.request)
        data["participant"]["identity_number"] = None
        data["participation"]["participant_identifier"] = " "
        with self.approved({"actor_id": self.actor.pk, **data}):
            receipt = self.call(request=data)
        self.assertEqual(Participant.objects.get(pk=receipt.participant_id).identity_number, "")
        self.assertIsNone(ActivityParticipation.objects.get(pk=receipt.participation_id).participant_identifier)

    def test_purpose_never_authorizes_sensitive_decisions(self):
        for purpose in ("development", "simulation", "operational"):
            data = {**copy.deepcopy(self.request), "purpose": purpose, "operation_id": str(uuid4())}
            with self.approved({"actor_id": self.actor.pk, **data}):
                receipt = self.call(request=data)
            self.assertEqual(receipt.purpose, purpose)
            self.assertEqual(receipt.status, "prepared_pending_decisions")
            self.assertIn("verify_identity", receipt.not_granted_by_operation)
            self.assertEqual(Participant.objects.get(pk=receipt.participant_id).identity_status, "pending")
            self.assertEqual(Activity.objects.get(pk=receipt.activity_id).status, "draft")

    def test_existing_foreign_context_and_security_state_are_untouched(self):
        before = self.state()
        receipt = self.call()
        after = self.state()
        for name in ("Activity", "Participant", "ActivityParticipation"):
            self.assertEqual(after[name][0], before[name][0])
        for name in ("User", "user_permissions", "user_groups", "TrainingSession", "ActivityPermissionGrant"):
            self.assertEqual(after[name], before[name])
        self.assertNotEqual(receipt.activity_id, self.existing_activity.pk)
        self.assertNotEqual(receipt.participant_id, self.existing_person.pk)

    def test_created_context_does_not_open_worklists_or_session_creation(self):
        receipt = self.call()
        self.assertFalse(readable_sessions(actor=self.actor).exists())
        with self.assertRaises(PermissionDenied):
            create_training_session(actor=self.actor, participation_id=receipt.participation_id,
                                    data={"weapon_type": "pistol", "distance_m": 15},
                                    operation_id=uuid4(), reason="Not authorized")

    def test_audit_scope_actor_snapshots_and_receipt_decisions(self):
        receipt = self.call()
        event = AuditEvent.objects.get(pk=receipt.audit_event_id)
        self.assertEqual(event.action, "training_context.provisioned")
        self.assertEqual(event.actor_user_id, self.actor.pk)
        self.assertEqual(event.participation_id, receipt.participation_id)
        self.assertIsNone(event.activity_id)
        self.assertIsNone(event.session_id)
        self.assertEqual((event.version_before, event.version_after), (None, 1))
        self.assertIsNone(event.before_state["participant"])
        self.assertEqual(event.after_state["participant"]["identity_status"], "pending")
        self.assertEqual(event.after_state["provisioning_result"]["purpose"], "development")
        self.assertEqual(event.after_state["authorization_reference"], self.request["authorization_reference"])
        for private in (self.password, self.request["participant"]["full_name"],
                        self.request["participant"]["identity_number"], self.request["activity"]["name"]):
            self.assertNotIn(private, json.dumps(event.after_state))
            self.assertNotIn(private, json.dumps(receipt.as_dict()))
        self.assertEqual(receipt.as_dict()["operation_id"], self.request["operation_id"])
        self.assertIn("grant_permissions", receipt.not_granted_by_operation)

    def test_partial_failures_roll_back_all_new_objects_and_audit(self):
        before = self.state()
        for target, error in (
            (Participant, ValidationError("Synthetic participant failure")),
            (ActivityParticipation, ValidationError("Synthetic participation failure")),
            (AuditEvent, ValidationError("Synthetic audit failure")),
            (AuditEvent, IntegrityError("Synthetic unique failure")),
        ):
            with self.subTest(target=target.__name__, error=type(error).__name__), patch.object(target, "save", side_effect=error):
                with self.assertRaises(ValidationError):
                    self.call()
            self.assertEqual(self.state(), before)

    def test_identical_replay_is_read_only_and_preserves_full_history(self):
        receipt = self.call()
        before = self.state()
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(self.call(), receipt)
        self.assertEqual(self.state(), before)
        for query in queries:
            self.assertFalse(query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")), query["sql"])

    def test_uuid_cannot_be_rebound_to_another_payload_or_actor(self):
        self.call()
        before = self.state()
        changed = copy.deepcopy(self.request)
        changed["activity"]["name"] = "Changed approved request"
        with self.approved({"actor_id": self.actor.pk, **changed}), self.assertRaises(OperationConflict):
            self.call(request=changed)
        with self.approved({"actor_id": self.other.pk, **self.request}), self.assertRaises(OperationConflict):
            self.call(actor_email=self.other.email)
        self.assertEqual(self.state(), before)

    def test_replay_requires_current_plan_and_actor_authentication(self):
        self.call()
        before = self.state()
        with override_settings(OPERATIONAL_CONTEXT_PROVISIONING_ENABLED=False), self.assertRaises(PermissionDenied):
            self.call()
        with self.assertRaises(PermissionDenied):
            self.call(actor_password="wrong")
        self.assertEqual(self.state(), before)

    def test_password_change_after_authentication_denies_under_lock(self):
        def rotated(**kwargs):
            actor = authenticate(**kwargs)
            User.objects.filter(pk=self.actor.pk).update(password="changed-by-concurrent-security-workflow")
            return actor
        with patch.object(provisioning, "authenticate", side_effect=rotated), self.assertRaises(PermissionDenied):
            self.call()
        self.assertEqual(Activity.objects.count(), 1)
        self.assertFalse(AuditEvent.objects.exists())

    def test_authorization_is_rechecked_under_lock(self):
        original = provisioning._approved
        calls = 0

        def revoked(plan):
            nonlocal calls
            calls += 1
            original(plan)
            if calls == 1:
                from django.conf import settings
                settings.OPERATIONAL_CONTEXT_PROVISIONING_ENABLED = False

        before = self.state()
        with patch.object(provisioning, "_approved", side_effect=revoked), self.assertRaises(PermissionDenied):
            self.call()
        self.assertEqual(self.state(), before)

    def test_missed_replay_collision_rolls_back_before_one_bounded_retry(self):
        receipt = self.call()
        before = self.state()
        original = provisioning._replay
        calls = 0

        def missed_once(*args):
            nonlocal calls
            calls += 1
            return None if calls == 1 else original(*args)

        with patch.object(provisioning, "_replay", side_effect=missed_once):
            self.assertEqual(self.call(), receipt)
        self.assertEqual(calls, 2)
        self.assertEqual(self.state(), before)

    def test_historical_references_and_audit_cannot_be_rewritten_or_deleted(self):
        receipt = self.call()
        part = ActivityParticipation.objects.get(pk=receipt.participation_id)
        part.activity = self.existing_activity
        with self.assertRaises(ValidationError):
            part.save()
        event = AuditEvent.objects.get(pk=receipt.audit_event_id)
        event.reason = "Changed"
        with self.assertRaises(ValidationError):
            event.save()
        with self.assertRaises(ValidationError):
            event.delete()
        with self.assertRaises(ValidationError):
            ActivityParticipation.objects.update(activity=self.existing_activity)
        with self.assertRaises(ProtectedError):
            ActivityParticipation.objects.get(pk=receipt.participation_id).delete()

    def test_real_database_constraints_reject_invalid_status_duplicate_membership_and_uuid(self):
        receipt = self.call()
        activity = Activity.objects.get(pk=receipt.activity_id)
        person = Participant.objects.get(pk=receipt.participant_id)
        bad = Activity(name="Invalid", training_date=timezone.localdate(), status="invalid", created_by=self.actor)
        duplicate = ActivityParticipation(activity=activity, participant=person)
        event = AuditEvent.objects.get(pk=receipt.audit_event_id)
        duplicate_event = AuditEvent(
            operation_id=event.operation_id, actor_kind="user", actor_user=self.actor,
            participation_id=receipt.participation_id, action=provisioning.ACTION,
            before_state=event.before_state, after_state=event.after_state,
        )
        before = self.state()
        for row in (bad, duplicate, duplicate_event):
            # Deliberately bypass Python validation ONLY in this test to exercise
            # the actual SQLite CHECK/UNIQUE constraints, never provisioning code.
            with self.subTest(model=type(row).__name__), self.assertRaises(IntegrityError), transaction.atomic():
                models.Model.save(row, force_insert=True)
        self.assertEqual(self.state(), before)

    def test_cli_is_interactive_and_has_no_password_argument(self):
        with patch.object(cli.sys.stdin, "isatty", return_value=False), self.assertRaises(CommandError):
            call_command("provision_operational_context", actor_email=self.actor.email, request_file="request.json")
        with self.assertRaises(TypeError):
            call_command("provision_operational_context", actor_email=self.actor.email,
                         request_file="request.json", password=self.password)
        self.assertEqual(Activity.objects.count(), 1)

    def test_cli_bounded_json_input_and_receipt_without_personal_data(self):
        output = StringIO()
        request_bytes = json.dumps(self.request).encode()
        with patch.object(cli.sys.stdin, "isatty", return_value=True), patch.object(cli, "getpass", return_value=self.password), \
                patch("builtins.open", mock_open(read_data=request_bytes)):
            call_command("provision_operational_context", actor_email=self.actor.email,
                         request_file="request.json", stdout=output)
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "prepared_pending_decisions")
        self.assertEqual(result["purpose"], "development")
        self.assertGreater(result["activity_id"], 0)
        self.assertNotIn(self.request["participant"]["full_name"], output.getvalue())
        self.assertNotIn(self.password, output.getvalue())

    def test_cli_rejects_duplicate_nonfinite_large_or_malformed_requests_before_password_prompt(self):
        for raw in (b'{"operation_id":"one","operation_id":"two"}', b'{"value":NaN}', b'[]',
                    b'not-json', b'\xff', b'{"x":' * 1500, b'x' * (cli.MAX_REQUEST_BYTES + 1)):
            before = self.state()
            with self.subTest(size=len(raw)), patch.object(cli.sys.stdin, "isatty", return_value=True), \
                    patch.object(cli, "getpass") as prompt, patch("builtins.open", mock_open(read_data=raw)):
                with self.assertRaises(CommandError):
                    call_command("provision_operational_context", actor_email=self.actor.email, request_file="request.json")
                prompt.assert_not_called()
            self.assertEqual(self.state(), before)

    def test_cli_rejection_does_not_expose_input_password_or_protected_plan(self):
        with patch.object(cli.sys.stdin, "isatty", return_value=True), patch.object(cli, "getpass", return_value="wrong"), \
                patch("builtins.open", mock_open(read_data=json.dumps(self.request).encode())):
            with self.assertRaises(CommandError) as error:
                call_command("provision_operational_context", actor_email=self.actor.email, request_file="request.json")
        for private in (self.request["participant"]["full_name"], self.request["authorization_reference"], "wrong"):
            self.assertNotIn(private, str(error.exception))
