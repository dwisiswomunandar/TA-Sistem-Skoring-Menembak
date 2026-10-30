from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.utils import timezone

from accounts.models import Participant

User = get_user_model()


class ParticipantTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.operator = User.objects.create_user("identity-verifier@example.com")

    def test_participants_can_exist_without_accounts(self):
        first = Participant.objects.create(full_name="First participant")
        second = Participant.objects.create(full_name="Second participant")
        self.assertIsNone(first.account_id)
        self.assertIsNone(second.account_id)
        self.assertEqual(first.identity_status, Participant.IdentityStatus.PENDING)

    def test_optional_account_is_one_to_one_in_database(self):
        account = User.objects.create_user("participant@example.com")
        first = Participant.objects.create(full_name="First participant", account=account)
        second = Participant.objects.create(full_name="Second participant")
        self.assertEqual(account.participant, first)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Participant.objects.filter(pk=second.pk).update(account=account)

    def test_names_and_identity_numbers_are_not_globally_unique(self):
        first = Participant.objects.create(full_name=" Same name ", identity_number=" ID-001 ")
        second = Participant.objects.create(full_name="Same name", identity_number="ID-001")
        self.assertNotEqual(first.pk, second.pk)
        first.refresh_from_db()
        self.assertEqual(first.full_name, "Same name")
        self.assertEqual(first.identity_number, "ID-001")

    def test_whitespace_name_is_rejected_before_saving(self):
        with self.assertRaises(ValidationError) as error:
            Participant.objects.create(full_name=" \t ")
        self.assertIn("full_name", error.exception.message_dict)

    def test_verified_identity_preserves_verifier_and_time(self):
        verified_at = timezone.now()
        participant = Participant.objects.create(
            full_name="Verified participant",
            identity_status=Participant.IdentityStatus.VERIFIED,
            verified_by=self.operator,
            verified_at=verified_at,
        )
        participant.refresh_from_db()
        self.assertEqual(participant.verified_by, self.operator)
        self.assertEqual(participant.verified_at, verified_at)
        self.assertIsNotNone(participant.created_at)

    def test_verified_identity_requires_both_verifier_and_time(self):
        for missing in ("verified_by", "verified_at"):
            values = {
                "full_name": "Incomplete verification",
                "identity_status": Participant.IdentityStatus.VERIFIED,
                "verified_by": self.operator,
                "verified_at": timezone.now(),
            }
            values[missing] = None
            with self.subTest(missing=missing), self.assertRaises(ValidationError) as error:
                Participant.objects.create(**values)
            self.assertIn(missing, error.exception.message_dict)

    def test_database_rejects_incomplete_verification(self):
        participant = Participant.objects.create(
            full_name="Verified participant", identity_status="verified",
            verified_by=self.operator, verified_at=timezone.now(),
        )
        for field in ("verified_by", "verified_at"):
            with self.subTest(field=field):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    Participant.objects.filter(pk=participant.pk).update(**{field: None})

    def test_database_rejects_unknown_status_and_empty_name(self):
        participant = Participant.objects.create(full_name="Participant")
        for values in ({"identity_status": "unknown"}, {"full_name": ""}):
            with self.subTest(values=values):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    Participant.objects.filter(pk=participant.pk).update(**values)

    def test_linked_account_is_protected(self):
        account = User.objects.create_user("linked-participant@example.com")
        participant = Participant.objects.create(full_name="Participant", account=account)
        with self.assertRaises(ProtectedError) as error:
            account.delete()
        self.assertIn(participant, error.exception.protected_objects)

    def test_identity_verifier_is_protected(self):
        participant = Participant.objects.create(
            full_name="Verified participant", identity_status="verified",
            verified_by=self.operator, verified_at=timezone.now(),
        )
        with self.assertRaises(ProtectedError) as error:
            self.operator.delete()
        self.assertIn(participant, error.exception.protected_objects)
