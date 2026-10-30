from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, models, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.utils import timezone

from accounts.models import Participant
from activities.models import Activity, ActivityParticipation, TrainingSession

User = get_user_model()


class DomainTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.operator = User.objects.create_user("domain-operator@example.com")
        cls.person = Participant.objects.create(
            full_name="Verified participant", identity_status="verified",
            verified_by=cls.operator, verified_at=timezone.now(),
        )
        cls.activity = Activity.objects.create(
            name="Training activity", training_date=timezone.localdate(),
            status="active", created_by=cls.operator,
        )
        cls.participation = ActivityParticipation.objects.create(
            activity=cls.activity, participant=cls.person, registration_status="approved",
            participant_identifier="A-001", decided_by=cls.operator, decided_at=timezone.now(),
        )

    def make_activity(self, **overrides):
        values = {
            "name": "Another activity", "training_date": timezone.localdate(),
            "status": "active", "created_by": self.operator,
        }
        values.update(overrides)
        return Activity.objects.create(**values)

    def make_participation(self, **overrides):
        person = Participant.objects.create(
            full_name="Another participant", identity_status="verified",
            verified_by=self.operator, verified_at=timezone.now(),
        )
        values = {
            "activity": self.activity, "participant": person,
            "registration_status": "approved", "participant_identifier": "A-002",
            "decided_by": self.operator, "decided_at": timezone.now(),
        }
        values.update(overrides)
        return ActivityParticipation.objects.create(**values)

    def make_session(self, **overrides):
        values = {
            "participation": self.participation, "session_number": 1,
            "assigned_assessor": self.operator, "created_by": self.operator,
            "weapon_type": "laras_panjang", "shooting_position": "tiarap",
            "distance_m": Decimal("100.00"), "caliber_mm": Decimal("5.56"),
        }
        values.update(overrides)
        return TrainingSession.objects.create(**values)

    def assert_database_rejects(self, instance, **changes):
        with self.assertRaises(IntegrityError), transaction.atomic():
            # Deliberate trusted bypass to test SQL constraints independently of guards.
            models.QuerySet(model=type(instance), using="default").filter(pk=instance.pk).update(**changes)


class ActivityTests(DomainTestCase):
    def test_draft_is_default_and_name_is_not_unique(self):
        values = {
            "name": self.activity.name, "training_date": timezone.localdate(),
            "created_by": self.operator,
        }
        draft = Activity.objects.create(**values)
        other = Activity.objects.create(**values)
        self.assertEqual(draft.status, "draft")
        self.assertNotEqual(draft.pk, other.pk)
        self.assertIsNone(draft.entry_code)
        self.assertIsNone(other.entry_code)

    def test_required_name_and_date_are_validated(self):
        for changes, field in (({"name": " \t "}, "name"), ({"training_date": None}, "training_date")):
            with self.subTest(field=field), self.assertRaises(ValidationError) as error:
                self.make_activity(**changes)
            self.assertIn(field, error.exception.message_dict)

    def test_empty_entry_codes_are_normalized_to_null(self):
        for code in ("", " \t "):
            with self.subTest(code=code):
                activity = self.make_activity(entry_code=code)
                activity.refresh_from_db()
                self.assertIsNone(activity.entry_code)

    def test_entry_code_requires_both_access_times(self):
        now = timezone.now()
        for changes, field in (
            ({}, "participant_access_opens_at"),
            ({"participant_access_opens_at": now}, "participant_access_closes_at"),
            ({"participant_access_closes_at": now}, "participant_access_opens_at"),
        ):
            with self.subTest(field=field), self.assertRaises(ValidationError) as error:
                self.make_activity(entry_code="fixture-code", **changes)
            self.assertIn(field, error.exception.message_dict)

    def test_access_window_must_be_strictly_increasing(self):
        now = timezone.now()
        for closes_at in (now, now - timedelta(seconds=1)):
            with self.subTest(closes_at=closes_at), self.assertRaises(ValidationError):
                self.make_activity(
                    participant_access_opens_at=now,
                    participant_access_closes_at=closes_at,
                )

    def test_valid_code_is_trimmed_and_unique_in_database(self):
        now = timezone.now()
        first = self.make_activity(
            entry_code=" fixture-code ", participant_access_opens_at=now,
            participant_access_closes_at=now + timedelta(hours=1),
        )
        first.refresh_from_db()
        self.assertEqual(first.entry_code, "fixture-code")
        second = self.make_activity(
            participant_access_opens_at=now,
            participant_access_closes_at=now + timedelta(hours=1),
        )
        self.assert_database_rejects(second, entry_code=first.entry_code)

    def test_database_rejects_unknown_status_or_empty_code_or_name(self):
        for changes in ({"status": "unknown"}, {"name": ""}, {"entry_code": ""}):
            with self.subTest(changes=changes):
                self.assert_database_rejects(self.activity, **changes)

    def test_database_rejects_code_without_window(self):
        self.assert_database_rejects(self.activity, entry_code="fixture-code")

    def test_database_rejects_backwards_or_equal_window(self):
        now = timezone.now()
        for closes_at in (now, now - timedelta(seconds=1)):
            with self.subTest(closes_at=closes_at):
                self.assert_database_rejects(
                    self.activity,
                    participant_access_opens_at=now,
                    participant_access_closes_at=closes_at,
                )

    def test_creator_is_protected(self):
        actor = User.objects.create_user("activity-creator@example.com")
        activity = self.make_activity(created_by=actor)
        with self.assertRaises(ProtectedError) as error:
            actor.delete()
        self.assertIn(activity, error.exception.protected_objects)


class ParticipationTests(DomainTestCase):
    def test_registration_allows_no_django_actor_and_an_explicit_actor(self):
        self_registered = self.make_participation(
            registration_status="pending", participant_identifier=None,
            decided_by=None, decided_at=None,
        )
        operator_registered = self.make_participation(
            registration_status="pending", participant_identifier=None,
            registered_by=self.operator, decided_by=None, decided_at=None,
        )
        self.assertIsNone(self_registered.registered_by_id)
        self.assertIsNone(self_registered.participant.account_id)
        self.assertEqual(operator_registered.registered_by, self.operator)
        self.assertIsNotNone(self_registered.registered_at)

    def test_activity_participant_pair_is_unique_in_database(self):
        other = self.make_participation()
        self.assert_database_rejects(other, participant=self.person)

    def test_duplicate_participation_is_rejected_by_validation(self):
        with self.assertRaises(ValidationError):
            ActivityParticipation.objects.create(
                activity=self.activity, participant=self.person,
            )

    def test_identifiers_trim_whitespace_preserve_case_and_allow_multiple_nulls(self):
        participation = self.make_participation(participant_identifier=" Mixed-02 ")
        self.assertEqual(participation.participant_identifier, "Mixed-02")
        for value in ("", " \t ", None):
            with self.subTest(value=value):
                pending = self.make_participation(
                    participant_identifier=value, registration_status="pending",
                    decided_by=None, decided_at=None,
                )
                pending.refresh_from_db()
                self.assertIsNone(pending.participant_identifier)

    def test_identifier_uniqueness_is_scoped_to_activity(self):
        other = self.make_participation()
        self.assert_database_rejects(other, participant_identifier="A-001")
        elsewhere = self.make_participation(
            activity=self.make_activity(), participant_identifier="A-001",
        )
        self.assertEqual(elsewhere.participant_identifier, self.participation.participant_identifier)

    def test_approval_requires_identifier_decision_maker_and_time(self):
        for field in ("participant_identifier", "decided_by", "decided_at"):
            with self.subTest(field=field), self.assertRaises(ValidationError) as error:
                self.make_participation(**{field: None})
            self.assertIn(field, error.exception.message_dict)

    def test_rejection_and_withdrawal_require_nonblank_reason(self):
        for state in ("rejected", "withdrawn"):
            for reason in ("", " \t "):
                with self.subTest(state=state, reason=reason):
                    with self.assertRaises(ValidationError) as error:
                        self.make_participation(registration_status=state, status_reason=reason)
                    self.assertIn("status_reason", error.exception.message_dict)

    def test_rejection_requires_decision_maker_and_time(self):
        for field in ("decided_by", "decided_at"):
            with self.subTest(field=field), self.assertRaises(ValidationError) as error:
                self.make_participation(
                    registration_status="rejected", status_reason="Not approved", **{field: None},
                )
            self.assertIn(field, error.exception.message_dict)

    def test_database_enforces_status_decision_identifier_and_version_checks(self):
        for changes in (
            {"registration_status": "unknown"},
            {"edit_version": 0},
            {"participant_identifier": ""},
            {"participant_identifier": None},
            {"decided_by": None},
            {"decided_at": None},
            {"registration_status": "rejected", "status_reason": ""},
            {"registration_status": "withdrawn", "status_reason": ""},
        ):
            with self.subTest(changes=changes):
                self.assert_database_rejects(self.participation, **changes)

    def test_withdrawal_preserves_sessions_and_blocks_new_sessions(self):
        session = self.make_session()
        self.participation.registration_status = "withdrawn"
        self.participation.status_reason = "Participant withdrew"
        self.participation.save()
        session.save()
        self.assertTrue(TrainingSession.objects.filter(pk=session.pk).exists())
        with self.assertRaises(ValidationError):
            self.make_session(session_number=2)

    def test_participation_context_can_change_before_sessions_exist(self):
        activity = self.make_activity()
        person = Participant.objects.create(full_name="Replacement participant")
        self.participation.activity = activity
        self.participation.participant = person
        self.participation.save()
        self.participation.refresh_from_db()
        self.assertEqual(self.participation.activity, activity)
        self.assertEqual(self.participation.participant, person)

    def test_participation_context_cannot_change_after_sessions_exist(self):
        session = self.make_session()
        for field, value in (
            ("activity", self.make_activity()),
            ("participant", Participant.objects.create(full_name="Another participant")),
        ):
            self.participation.refresh_from_db()
            setattr(self.participation, field, value)
            with self.subTest(field=field), self.assertRaises(ValidationError) as error:
                self.participation.save()
            self.assertIn(field, error.exception.message_dict)
        self.participation.refresh_from_db()
        self.assertEqual(self.participation.activity, self.activity)
        self.assertEqual(self.participation.participant, self.person)
        self.assertEqual(session.participation_id, self.participation.pk)

    def test_activity_participant_and_registration_actors_are_protected(self):
        registrar = User.objects.create_user("participation-registrar@example.com")
        decider = User.objects.create_user("participation-decider@example.com")
        participation = self.make_participation(registered_by=registrar, decided_by=decider)
        for referenced in (self.activity, participation.participant, registrar, decider):
            with self.subTest(model=type(referenced).__name__, pk=referenced.pk):
                with self.assertRaises(ProtectedError) as error:
                    referenced.delete()
                self.assertIn(participation, error.exception.protected_objects)

    def test_activity_status_index_exists_in_migrated_database(self):
        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(
                cursor, ActivityParticipation._meta.db_table,
            )
        self.assertTrue(any(
            constraint["index"]
            and tuple(constraint["columns"]) == ("activity_id", "registration_status")
            for constraint in constraints.values()
        ))


class TrainingSessionTests(DomainTestCase):
    def test_session_derives_activity_and_participant_through_participation(self):
        session = self.make_session()
        stored = TrainingSession.objects.select_related(
            "participation__activity", "participation__participant",
        ).get(pk=session.pk)
        self.assertEqual(stored.participation.activity, self.activity)
        self.assertEqual(stored.participation.participant, self.person)
        self.assertEqual(stored.assigned_assessor, self.operator)
        self.assertEqual(stored.distance_m, Decimal("100.00"))
        self.assertIsNotNone(stored.created_at)

    def test_shooting_context_has_no_hidden_defaults(self):
        for field in ("weapon_type", "shooting_position", "distance_m", "caliber_mm"):
            with self.subTest(field=field), self.assertRaises(ValidationError) as error:
                self.make_session(**{field: None})
            self.assertIn(field, error.exception.message_dict)

    def test_positive_distance_does_not_force_a_field_distance_or_stance(self):
        session = self.make_session(
            weapon_type="pistol", caliber_mm=Decimal("9.00"),
            distance_m=Decimal("17.25"), shooting_position="duduk",
        )
        self.assertEqual(session.distance_m, Decimal("17.25"))
        self.assertEqual(session.shooting_position, "duduk")

    def test_baseline_weapon_caliber_mismatches_are_rejected(self):
        for weapon, caliber in (("pistol", "5.56"), ("laras_panjang", "9.00")):
            with self.subTest(weapon=weapon), self.assertRaises(ValidationError) as error:
                self.make_session(weapon_type=weapon, caliber_mm=Decimal(caliber))
            self.assertIn("caliber_mm", error.exception.message_dict)

    def test_invalid_or_nonfinite_decimal_values_are_rejected(self):
        for field in ("distance_m", "caliber_mm"):
            for value in ("0", "-1", "NaN", "Infinity", "1.001"):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValidationError) as error:
                        self.make_session(**{field: Decimal(value)})
                    self.assertIn(field, error.exception.message_dict)

    def test_new_session_requires_active_activity(self):
        for status in ("draft", "closed"):
            Activity.objects.filter(pk=self.activity.pk).update(status=status)
            with self.subTest(status=status), self.assertRaises(ValidationError) as error:
                self.make_session()
            self.assertIn("participation", error.exception.message_dict)

    def test_new_session_requires_approved_participation(self):
        for status in ("pending", "rejected", "withdrawn"):
            # Eligibility fixture, not a supported bulk workflow write.
            models.QuerySet(model=ActivityParticipation, using="default").filter(pk=self.participation.pk).update(
                registration_status=status, status_reason="Not eligible",
            )
            with self.subTest(status=status), self.assertRaises(ValidationError) as error:
                self.make_session()
            self.assertIn("participation", error.exception.message_dict)

    def test_new_session_requires_verified_identity(self):
        Participant.objects.filter(pk=self.person.pk).update(identity_status="pending")
        with self.assertRaises(ValidationError) as error:
            self.make_session()
        self.assertIn("participation", error.exception.message_dict)

    def test_closed_activity_does_not_invalidate_an_existing_session(self):
        session = self.make_session()
        Activity.objects.filter(pk=self.activity.pk).update(status="closed")
        session.save()
        self.assertTrue(TrainingSession.objects.filter(pk=session.pk).exists())
        with self.assertRaises(ValidationError):
            self.make_session(session_number=2)

    def test_session_numbers_are_unique_within_participation_in_database(self):
        first = self.make_session()
        second = self.make_session(session_number=2)
        self.assert_database_rejects(second, session_number=first.session_number)

    def test_multiple_sessions_and_reused_numbers_across_participations_are_allowed(self):
        first = self.make_session()
        second = self.make_session(session_number=2)
        elsewhere = self.make_session(participation=self.make_participation())
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(first.session_number, elsewhere.session_number)

    def test_database_enforces_positive_numbers_and_known_choices(self):
        session = self.make_session()
        for changes in (
            {"session_number": 0}, {"edit_version": 0},
            {"distance_m": Decimal("0")}, {"distance_m": Decimal("-1")},
            {"caliber_mm": Decimal("0")}, {"caliber_mm": Decimal("-1")},
            {"weapon_type": "unknown"}, {"shooting_position": "unknown"},
        ):
            with self.subTest(changes=changes):
                self.assert_database_rejects(session, **changes)

    def test_session_participation_assessor_and_creator_are_protected(self):
        assessor = User.objects.create_user("assigned-assessor@example.com")
        creator = User.objects.create_user("session-creator@example.com")
        session = self.make_session(assigned_assessor=assessor, created_by=creator)
        for referenced in (self.participation, assessor, creator):
            with self.subTest(model=type(referenced).__name__, pk=referenced.pk):
                with self.assertRaises(ProtectedError) as error:
                    referenced.delete()
                self.assertIn(session, error.exception.protected_objects)

    def test_assessor_lookup_index_exists_in_migrated_database(self):
        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(
                cursor, TrainingSession._meta.db_table,
            )
        self.assertTrue(any(
            constraint["index"]
            and tuple(constraint["columns"]) == ("assigned_assessor_id",)
            for constraint in constraints.values()
        ))
