"""Synthetic approvals only; production settings remain default-deny."""

from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group, Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from accounts.models import Participant
from assessments.models import AuditEvent
from .models import Activity, ActivityParticipation, ActivityPermissionGrant, TrainingSession
from .read_access import readable_sessions
from .session_creation import create_training_session
from .session_creation_policy import creation_contexts

User = get_user_model()


class SessionCreationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.actor = User.objects.create_user("creator@example.com")
        cls.other = User.objects.create_user("other@example.com")
        cls.activity = Activity.objects.create(
            name="Kegiatan Disetujui", training_date=timezone.localdate(), status="active", created_by=cls.other,
        )
        cls.foreign = Activity.objects.create(
            name="Kegiatan Rahasia", training_date=timezone.localdate(), status="active", created_by=cls.actor,
        )
        cls.person = Participant.objects.create(
            full_name="Peserta Disetujui", identity_status="verified", verified_by=cls.other,
            verified_at=timezone.now(), identity_number="PRIVATE-IDENTITY",
        )
        cls.participation = ActivityParticipation.objects.create(
            activity=cls.activity, participant=cls.person, registration_status="approved",
            participant_identifier="A-001", decided_by=cls.other, decided_at=timezone.now(),
        )
        cls.foreign_participation = ActivityParticipation.objects.create(
            activity=cls.foreign, participant=cls.person, registration_status="approved",
            participant_identifier="SECRET-B", decided_by=cls.other, decided_at=timezone.now(),
        )
        cls.add_permission = Permission.objects.get(content_type__app_label="activities",
                                                   content_type__model="trainingsession", codename="add_trainingsession")
        cls.change_permission = Permission.objects.get(content_type__app_label="assessments",
                                                      content_type__model="assessment", codename="change_assessment")
        cls.actor.user_permissions.add(cls.add_permission, cls.change_permission)
        cls.grant = ActivityPermissionGrant.objects.create(
            user=cls.actor, activity=cls.activity, permission=cls.change_permission, created_by=cls.other,
        )

    def setUp(self):
        self.approval = {"user_id": self.actor.pk, "activity_id": self.activity.pk,
                         "participation_id": self.participation.pk, "approval_reference": "SYNTHETIC-TEST-ONLY"}
        setting = override_settings(TRAINING_SESSION_CREATION_APPROVALS=(self.approval,))
        setting.enable()
        self.addCleanup(setting.disable)
        self.client.force_login(self.actor)
        self.url = reverse("activities:session_create")

    def command(self, **overrides):
        args = {"actor": self.actor, "participation_id": self.participation.pk,
                "data": {"weapon_type": "pistol", "distance_m": 15},
                "operation_id": uuid4(), "reason": "Synthetic creation reason"}
        args.update(overrides)
        return create_training_session(**args)

    def payload(self, **overrides):
        values = {"participation": self.participation.pk, "weapon_type": "pistol",
                  "distance_m": "15.00", "shooting_position": "", "reason": "Synthetic UI reason",
                  "operation_id": str(uuid4())}
        values.update(overrides)
        return values

    def assert_no_writes(self):
        self.assertEqual(TrainingSession.objects.count(), 0)
        self.assertEqual(AuditEvent.objects.count(), 0)

    def test_default_deny_even_for_staff_superuser_creator(self):
        with override_settings(TRAINING_SESSION_CREATION_APPROVALS=()):
            for staff, superuser in ((False, False), (True, False), (True, True)):
                self.actor.is_staff, self.actor.is_superuser = staff, superuser
                self.actor.save(update_fields=("is_staff", "is_superuser"))
                with self.subTest(staff=staff, superuser=superuser), self.assertRaises(PermissionDenied):
                    self.command()
                self.assertFalse(creation_contexts(actor=self.actor).exists())
            self.assert_no_writes()

    def test_first_session_has_separate_context_permission_and_becomes_readable(self):
        self.assertFalse(readable_sessions(actor=self.actor).exists())
        self.assertEqual(list(creation_contexts(actor=self.actor)), [self.participation])
        receipt = self.command()
        self.assertEqual(receipt.session_number, 1)
        self.assertEqual(list(readable_sessions(actor=self.actor).values_list("pk", flat=True)), [receipt.session_id])
        row = TrainingSession.objects.get(pk=receipt.session_id)
        self.assertEqual((row.assigned_assessor_id, row.created_by_id), (self.actor.pk, self.actor.pk))

    def test_requires_both_explicit_global_permissions_and_active_matching_grant(self):
        self.actor.is_superuser = True
        self.actor.save(update_fields=("is_superuser",))
        for permission in (self.add_permission, self.change_permission):
            self.actor.user_permissions.remove(permission)
            with self.subTest(permission=permission.codename), self.assertRaises(PermissionDenied):
                self.command()
            self.actor.user_permissions.add(permission)
        self.grant.is_active = False
        self.grant.save()
        with self.assertRaises(PermissionDenied):
            self.command()
        self.assert_no_writes()

    def test_group_permissions_and_revocation_ignore_actor_permission_cache(self):
        self.actor.user_permissions.clear()
        group = Group.objects.create(name="Synthetic creation authority")
        group.permissions.add(self.add_permission, self.change_permission)
        self.actor.groups.add(group)
        self.assertTrue(self.actor.has_perm("activities.add_trainingsession"))
        receipt = self.command()
        group.permissions.clear()
        with self.assertRaises(PermissionDenied):
            self.command(operation_id=receipt.operation_id)
        self.assertEqual(TrainingSession.objects.count(), 1)

    def test_cross_activity_participation_and_other_actor_are_denied(self):
        for kwargs in ({"participation_id": self.foreign_participation.pk}, {"actor": self.other},
                       {"participation_id": 999999}, {"participation_id": True},
                       {"actor": AnonymousUser()}, {"actor": self.actor.pk}):
            with self.subTest(kwargs=kwargs), self.assertRaises(PermissionDenied):
                self.command(**kwargs)
        self.assert_no_writes()

    def test_approval_activity_must_match_persisted_participation(self):
        wrong = {**self.approval, "activity_id": self.foreign.pk}
        ActivityPermissionGrant.objects.create(user=self.actor, activity=self.foreign,
                                               permission=self.change_permission, created_by=self.other)
        with override_settings(TRAINING_SESSION_CREATION_APPROVALS=(wrong,)):
            self.assertFalse(creation_contexts(actor=self.actor).exists())
            with self.assertRaises(PermissionDenied):
                self.command()
        self.assert_no_writes()

    def test_malformed_duplicate_or_incomplete_approvals_fail_closed(self):
        for config in (None, {}, ({},), (self.approval, self.approval),
                       ({**self.approval, "user_id": True},),
                       ({**self.approval, "approval_reference": " "},),
                       ({**self.approval, "allow_all": True},)):
            with self.subTest(config=config), override_settings(TRAINING_SESSION_CREATION_APPROVALS=config):
                with self.assertRaises(PermissionDenied):
                    self.command()
                self.assertFalse(creation_contexts(actor=self.actor).exists())
        self.assert_no_writes()

    def test_inactive_user_and_revoked_approval_are_denied(self):
        self.actor.is_active = False
        self.actor.save(update_fields=("is_active",))
        with self.assertRaises(PermissionDenied):
            self.command()
        self.assert_no_writes()

    def test_eligibility_is_checked_from_fresh_database_rows(self):
        for activity_status, registration, identity in (
            ("closed", "approved", "verified"), ("draft", "approved", "verified"),
            ("active", "pending", "verified"), ("active", "approved", "pending"),
        ):
            self.activity.status = activity_status
            self.activity.save()
            self.participation.registration_status = registration
            self.participation.save()
            self.person.identity_status = identity
            self.person.save()
            with self.subTest(activity=activity_status, registration=registration, identity=identity):
                self.assertFalse(creation_contexts(actor=self.actor).exists())
                with self.assertRaises(ValidationError):
                    self.command()
                self.assert_no_writes()

    def test_pistol_parameters_for_all_supported_distances(self):
        for distance in (15, 20, 25):
            receipt = self.command(data={"weapon_type": "pistol", "distance_m": distance})
            row = TrainingSession.objects.get(pk=receipt.session_id)
            self.assertEqual((row.shooting_position, row.distance_m, row.caliber_mm),
                             ("berdiri", Decimal(distance), Decimal("9.00")))

    def test_long_gun_derives_distance_caliber_and_requires_explicit_stance(self):
        for position in ("berdiri", "duduk", "tiarap"):
            receipt = self.command(data={"weapon_type": "laras_panjang", "shooting_position": position})
            row = TrainingSession.objects.get(pk=receipt.session_id)
            self.assertEqual((row.shooting_position, row.distance_m, row.caliber_mm),
                             (position, Decimal("100.00"), Decimal("5.56")))

    def test_invalid_conflicting_nonfinite_or_overprecision_parameters_write_nothing(self):
        for data in ({}, None, [], {"weapon_type": "invalid"}, {"weapon_type": "laras_panjang"},
                     {"weapon_type": "pistol"}, {"weapon_type": "pistol", "distance_m": True},
                     {"weapon_type": "pistol", "distance_m": "15.000"},
                     {"weapon_type": "pistol", "distance_m": "NaN"},
                     {"weapon_type": "pistol", "distance_m": "Infinity"},
                     {"weapon_type": "pistol", "distance_m": 15, "caliber_mm": "5.56"},
                     {"weapon_type": "pistol", "distance_m": 15, "shooting_position": "duduk"},
                     {"weapon_type": "laras_panjang", "shooting_position": "berdiri", "distance_m": 15},
                     {"weapon_type": "pistol", "distance_m": 15, "session_number": 99}):
            with self.subTest(data=data), self.assertRaises(ValidationError):
                self.command(data=data)
            self.assert_no_writes()

    def test_invalid_operation_or_reason_write_nothing(self):
        for kwargs in ({"operation_id": "invalid"}, {"operation_id": True}, {"reason": " "},
                       {"reason": None}, {"reason": "x" * 2001}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValidationError):
                self.command(**kwargs)
        self.assert_no_writes()

    def test_numbering_uses_max_across_all_assignees_and_preserves_existing_rows(self):
        previous = TrainingSession.objects.create(
            participation=self.participation, session_number=7, assigned_assessor=self.other,
            created_by=self.other, weapon_type="pistol", shooting_position="berdiri", distance_m=20, caliber_mm=9,
        )
        before = list(TrainingSession.objects.filter(pk=previous.pk).values())
        self.assertEqual(self.command().session_number, 8)
        self.assertEqual(self.command().session_number, 9)
        self.assertEqual(list(TrainingSession.objects.filter(pk=previous.pk).values()), before)

    def test_audit_scope_actor_snapshot_and_versions_are_server_owned(self):
        receipt = self.command()
        row = TrainingSession.objects.get(pk=receipt.session_id)
        event = AuditEvent.objects.get(pk=receipt.audit_event_id)
        self.assertEqual(event.action, "training_session.created")
        self.assertEqual(event.actor_user_id, self.actor.pk)
        self.assertEqual(event.session_id, row.pk)
        self.assertIsNone(event.activity_id)
        self.assertIsNone(event.participation_id)
        self.assertIsNone(event.assessment_id)
        self.assertEqual((event.version_before, event.version_after), (None, 1))
        self.assertEqual(event.before_state, {"schema_version": 1, "session": None})
        self.assertEqual(event.after_state["session"]["distance_m"], "15.00")
        self.assertEqual(event.after_state["creation_approval"], self.approval)
        self.assertEqual(event.after_state["eligibility"]["participation_edit_version"], 1)
        self.assertNotIn("PRIVATE-IDENTITY", str(event.after_state))

    def test_audit_failure_rolls_back_session_and_all_parents(self):
        before = list(ActivityParticipation.objects.values())
        for error in (ValidationError("Synthetic audit failure"), IntegrityError("Synthetic integrity failure")):
            with self.subTest(error=type(error).__name__), patch.object(AuditEvent, "save", side_effect=error):
                with self.assertRaises(ValidationError):
                    self.command()
            self.assert_no_writes()
            self.assertEqual(list(ActivityParticipation.objects.values()), before)

    def test_identical_replay_is_read_only_and_numeric_inputs_are_canonical(self):
        operation_id = uuid4()
        first = self.command(operation_id=operation_id)
        session_before = list(TrainingSession.objects.values())
        audit_before = list(AuditEvent.objects.values())
        replay = self.command(operation_id=str(operation_id), data={
            "weapon_type": "pistol", "distance_m": "15.00", "shooting_position": "berdiri", "caliber_mm": "9.00",
        })
        self.assertEqual(first, replay)
        self.assertEqual(list(TrainingSession.objects.values()), session_before)
        self.assertEqual(list(AuditEvent.objects.values()), audit_before)

    def test_changed_request_uuid_collision_and_replay_after_revocation_are_denied(self):
        receipt = self.command()
        for kwargs in ({"reason": "Other reason"}, {"data": {"weapon_type": "pistol", "distance_m": 20}}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValidationError):
                self.command(operation_id=receipt.operation_id, **kwargs)
        with override_settings(TRAINING_SESSION_CREATION_APPROVALS=()):
            with self.assertRaises(PermissionDenied):
                self.command(operation_id=receipt.operation_id)
        self.grant.is_active = False
        self.grant.save()
        with self.assertRaises(PermissionDenied):
            self.command(operation_id=receipt.operation_id)
        self.assertEqual(TrainingSession.objects.count(), 1)
        self.assertEqual(AuditEvent.objects.count(), 1)

    def test_operation_collision_rolls_back_new_row_before_bounded_replay(self):
        from . import session_creation
        receipt = self.command()
        original_replay = session_creation._replay
        calls = 0

        def miss_once(*args):
            nonlocal calls
            calls += 1
            return None if calls == 1 else original_replay(*args)

        before = list(TrainingSession.objects.values())
        with patch.object(session_creation, "_replay", side_effect=miss_once):
            self.assertEqual(self.command(operation_id=receipt.operation_id), receipt)
        self.assertEqual(calls, 2)
        self.assertEqual(list(TrainingSession.objects.values()), before)
        self.assertEqual(AuditEvent.objects.count(), 1)

    def test_authority_rechecked_under_lock_before_insert(self):
        from . import session_creation
        original = session_creation.authorize_creation

        def revoke_after_precheck(**kwargs):
            result = original(**kwargs)
            if not kwargs.get("lock"):
                self.grant.is_active = False
                self.grant.save()
            return result

        with patch.object(session_creation, "authorize_creation", side_effect=revoke_after_precheck):
            with self.assertRaises(PermissionDenied):
                self.command()
        self.assert_no_writes()

    def test_creation_audit_freezes_context_and_audit_remains_append_only(self):
        receipt = self.command()
        row = TrainingSession.objects.get(pk=receipt.session_id)
        row.assigned_assessor = self.other
        with self.assertRaises(ValidationError):
            row.save()
        event = AuditEvent.objects.get(pk=receipt.audit_event_id)
        event.reason = "Changed"
        with self.assertRaises(ValidationError):
            event.save()
        with self.assertRaises(ValidationError):
            TrainingSession.objects.update(session_number=99)
        with self.assertRaises(ValidationError):
            event.delete()

    def test_default_ui_does_not_query_operational_data_and_post_is_denied(self):
        with override_settings(TRAINING_SESSION_CREATION_APPROVALS=()):
            with CaptureQueriesContext(connection) as queries:
                response = self.client.get(self.url)
            self.assertContains(response, "Akses pembuatan sesi belum tersedia")
            self.assertNotContains(response, self.person.full_name)
            for query in queries:
                self.assertNotIn('"activities_', query["sql"].lower())
                self.assertNotIn('"accounts_participant"', query["sql"].lower())
            self.assertEqual(self.client.post(self.url, self.payload()).status_code, 403)
        self.assert_no_writes()

    def test_ui_anonymous_and_csrf_protection(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).url, reverse("login") + "?next=" + self.url)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.actor)
        self.assertEqual(client.post(self.url, self.payload()).status_code, 403)
        client.get(self.url)
        data = self.payload(csrfmiddlewaretoken=client.cookies["csrftoken"].value)
        self.assertEqual(client.post(self.url, data).status_code, 302)

    def test_ui_context_is_scoped_readonly_parameters_have_no_input_fields(self):
        response = self.client.get(self.url)
        self.assertTemplateUsed(response, "activities/session_create.html")
        self.assertContains(response, self.person.full_name)
        self.assertContains(response, self.activity.name)
        self.assertNotContains(response, self.foreign.name)
        self.assertNotContains(response, "SECRET-B")
        self.assertNotContains(response, "PRIVATE-IDENTITY")
        self.assertNotContains(response, 'name="caliber_mm"')
        self.assertNotContains(response, 'name="assigned_assessor"')
        self.assertContains(response, "Parameter otomatis")
        self.assertContains(response, '/static/ui/session_form.js')
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertEqual(self.client.put(self.url).status_code, 405)

    def test_ui_post_redirect_get_and_duplicate_post_create_once(self):
        payload = self.payload()
        response = self.client.post(self.url, payload)
        destination = reverse("activities:participant_sessions", args=[self.activity.pk, self.participation.pk])
        self.assertRedirects(response, destination, fetch_redirect_response=False)
        self.assertContains(self.client.get(destination), "Sesi 1 berhasil disimpan.")
        self.assertRedirects(self.client.post(self.url, payload), destination)
        self.assertEqual(TrainingSession.objects.count(), 1)
        self.assertEqual(AuditEvent.objects.count(), 1)

    def test_ui_invalid_parameters_or_foreign_context_create_nothing(self):
        for overrides in ({"weapon_type": ""}, {"distance_m": ""}, {"distance_m": "999"},
                          {"shooting_position": "duduk"}, {"operation_id": "bad"},
                          {"participation": self.foreign_participation.pk}, {"reason": " "},
                          {"assigned_assessor": self.other.pk}, {"session_number": 9}, {"caliber_mm": "9.00"}):
            with self.subTest(overrides=overrides):
                response = self.client.post(self.url, self.payload(**overrides))
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["form"].errors)
                self.assertNotContains(response, self.foreign.name)
                self.assert_no_writes()

    def test_ui_long_gun_works_without_javascript_and_has_no_default_selections(self):
        response = self.client.get(self.url)
        for field in ("weapon_type", "shooting_position", "distance_m"):
            self.assertIsNone(response.context["form"][field].value())
        response = self.client.post(self.url, self.payload(
            weapon_type="laras_panjang", shooting_position="tiarap", distance_m="",
        ))
        self.assertEqual(response.status_code, 302)
        row = TrainingSession.objects.get()
        self.assertEqual((row.shooting_position, row.distance_m), ("tiarap", Decimal("100.00")))

    def test_ui_escapes_context_text_and_preserves_uuid_on_errors(self):
        self.activity.name = '<script>alert("context")</script>'
        self.activity.save()
        payload = self.payload(distance_m="")
        response = self.client.post(self.url, payload)
        self.assertNotContains(response, "<script>")
        self.assertContains(response, "&lt;script&gt;")
        self.assertContains(response, payload["operation_id"])
        self.assert_no_writes()
