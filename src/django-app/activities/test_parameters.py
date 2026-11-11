"""PROVISIONAL parameter rules, preserved historical context and write guards."""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.test import SimpleTestCase

from activities.models import TrainingSession
from activities.parameters import resolve_parameters
from activities.tests import DomainTestCase
from activities.test_history import HistoryTestCase
from assessments.models import Assessment, AuditEvent, ScoringRuleVersion, TargetPhoto, TargetRing, TargetVersion


class ParameterPolicyTests(SimpleTestCase):
    def test_six_combinations_resolve_from_only_operator_selections(self):
        for position in ("berdiri", "duduk", "tiarap"):
            with self.subTest(position=position):
                self.assertEqual(resolve_parameters(weapon_type="laras_panjang", shooting_position=position),
                    dict(weapon_type="laras_panjang", shooting_position=position,
                         distance_m=Decimal("100.00"), caliber_mm=Decimal("5.56")))
        for distance in (15, "20", Decimal("25")):
            with self.subTest(distance=distance):
                self.assertEqual(resolve_parameters(weapon_type="pistol", distance_m=distance),
                    dict(weapon_type="pistol", shooting_position="berdiri",
                         distance_m=Decimal(str(distance)), caliber_mm=Decimal("9.00")))

    def test_explicit_matching_values_are_accepted_and_conflicts_rejected(self):
        self.assertEqual(resolve_parameters(weapon_type="pistol", distance_m=15,
            shooting_position="berdiri", caliber_mm="9")["caliber_mm"], Decimal("9"))
        for changes in ({"shooting_position": "duduk"}, {"shooting_position": "tiarap"},
                        {"distance_m": 10}, {"distance_m": 17.25}, {"caliber_mm": "5.56"}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                resolve_parameters(**dict(dict(weapon_type="pistol", distance_m=15), **changes))
        with self.assertRaises(ValidationError):
            resolve_parameters(weapon_type="laras_panjang", shooting_position="tiarap", distance_m=25)

    def test_missing_invalid_and_nonfinite_inputs_are_rejected(self):
        for values in ({}, {"weapon_type": "unknown"}, {"weapon_type": []},
                       {"weapon_type": "laras_panjang"}, {"weapon_type": "pistol"},
                       {"weapon_type": "pistol", "distance_m": ""},
                       {"weapon_type": "laras_panjang", "shooting_position": "jongkok"}):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                resolve_parameters(**values)
        for field in ("distance_m", "caliber_mm"):
            for value in (True, False, [], {}, "invalid", "NaN", "Infinity", Decimal("-Infinity"), float("nan")):
                with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                    resolve_parameters(**dict(dict(weapon_type="pistol", distance_m=15), **{field: value}))


class TrainingSessionParameterTests(DomainTestCase):
    def test_model_derives_server_values_for_all_six_combinations(self):
        selections = [dict(weapon_type="laras_panjang", shooting_position=p)
                      for p in ("berdiri", "duduk", "tiarap")]
        selections += [dict(weapon_type="pistol", distance_m=d) for d in (15, 20, 25)]
        for number, values in enumerate(selections, 1):
            with self.subTest(values=values):
                session = TrainingSession.objects.create(participation=self.participation, session_number=number,
                    assigned_assessor=self.operator, created_by=self.operator, **values)
                session.refresh_from_db()
                expected = resolve_parameters(**values)
                self.assertEqual({name: getattr(session, name) for name in expected}, expected)
                self.assertEqual(session.participation.activity_id, self.activity.pk)
                self.assertEqual(session.edit_version, 1)

    def test_explicit_conflicts_are_not_silently_repaired(self):
        for changes in ({"distance_m": 25}, {"caliber_mm": 9}, {"shooting_position": "jongkok"},
                        {"distance_m": "100.001"}, {"caliber_mm": "5.560"},
                        {"distance_m": True}, {"caliber_mm": True}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                self.make_session(**changes)
        session = TrainingSession(participation=self.participation, session_number=1,
            assigned_assessor=self.operator, created_by=self.operator, weapon_type="pistol",
            shooting_position="duduk", distance_m=Decimal("17.25"), caliber_mm=Decimal("9"))
        with self.assertRaises(ValidationError):
            session.save()
        self.assertEqual((session.shooting_position, session.distance_m), ("duduk", Decimal("17.25")))
        self.assertFalse(TrainingSession.objects.exists())

    def test_missing_operator_selections_and_nonfinite_values_fail_without_insert(self):
        for changes in ({"weapon_type": None}, {"weapon_type": ""}, {"weapon_type": "unknown"},
                        {"shooting_position": None}, {"shooting_position": ""}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                self.make_session(**changes)
        for value in (None, "", "bad", "NaN", Decimal("Infinity"), float("-inf"), True):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                self.make_session(weapon_type="pistol", shooting_position=None, distance_m=value, caliber_mm=None)
        self.assertFalse(TrainingSession.objects.exists())

    def test_clean_directly_checks_new_context_and_allowed_edits_use_current_policy(self):
        session = self.make_session()
        session.distance_m = Decimal("25")
        with self.assertRaises(ValidationError):
            session.clean()
        with self.assertRaises(ValidationError):
            session.save()
        session.refresh_from_db()
        self.assertEqual((session.distance_m, session.edit_version), (Decimal("100"), 1))
        session.weapon_type, session.shooting_position = "pistol", "berdiri"
        session.distance_m, session.caliber_mm = Decimal("20"), Decimal("9")
        session.save()
        session.refresh_from_db()
        self.assertEqual((session.distance_m, session.edit_version), (Decimal("20"), 2))

    def test_legacy_context_is_preserved_but_changes_cannot_bypass_new_policy(self):
        session = self.make_session(weapon_type="pistol", shooting_position="berdiri",
                                    distance_m=15, caliber_mm=9)
        # Deliberate trusted fixture represents a row created before this policy.
        models.QuerySet(model=TrainingSession, using="default").filter(pk=session.pk).update(
            distance_m=Decimal("17.25"), shooting_position="duduk")
        session.refresh_from_db()
        before = TrainingSession.objects.filter(pk=session.pk).values().get()
        session.full_clean()
        session.save()
        self.assertEqual(TrainingSession.objects.filter(pk=session.pk).values().get()["edit_version"], 1)
        self.assertEqual((session.distance_m, session.shooting_position), (Decimal("17.25"), "duduk"))
        # Equivalent decimal input is not a context change or a policy bypass.
        session.distance_m = "17.25"
        session.full_clean()
        session.session_number = 2
        with self.assertRaises(ValidationError):
            session.save()
        self.assertEqual(TrainingSession.objects.filter(pk=session.pk).values().get()["session_number"], before["session_number"])
        session.refresh_from_db()
        session.distance_m, session.shooting_position = Decimal("25"), "berdiri"
        session.save()
        self.assertEqual(session.edit_version, 2)


class HistoricalParameterTests(HistoryTestCase):
    def legacy_context(self):
        # Fixture only: do this before creating snapshots, never repair snapshots.
        self.raw(TrainingSession).filter(pk=self.session.pk).update(
            distance_m=Decimal("17.25"), shooting_position="duduk")
        self.session.refresh_from_db()

    def assert_legacy_reference_preserved(self, reference):
        self.legacy_context()
        reference()
        models_to_check = (TrainingSession, TargetPhoto, Assessment, AuditEvent,
                           TargetVersion, TargetRing, ScoringRuleVersion)
        before = {model: list(model.objects.order_by("pk").values()) for model in models_to_check}
        self.session.full_clean()
        self.assertEqual({model: list(model.objects.order_by("pk").values()) for model in models_to_check}, before)
        self.session.save()
        after = {model: list(model.objects.order_by("pk").values()) for model in models_to_check}
        # Existing auto_now behavior is allowed; every other stored value, including
        # edit_version and all evidence/audit/configuration timestamps, must match.
        old_row = next(row for row in before[TrainingSession] if row["id"] == self.session.pk)
        new_row = next(row for row in after[TrainingSession] if row["id"] == self.session.pk)
        self.assertGreaterEqual(new_row["updated_at"], old_row["updated_at"])
        old_row["updated_at"] = new_row["updated_at"]
        self.assertEqual(after, before)
        self.session.shooting_position, self.session.distance_m = "berdiri", Decimal("20")
        with self.assertRaises(ValidationError):
            self.session.save()
        self.assertEqual({model: list(model.objects.order_by("pk").values()) for model in models_to_check}, before)

    def test_legacy_photo_freezes_context_without_revalidation_failure(self):
        self.assert_legacy_reference_preserved(self.make_photo)

    def test_legacy_assessment_snapshot_is_not_rewritten(self):
        self.assert_legacy_reference_preserved(self.make_assessment)

    def test_legacy_audit_and_scoring_configuration_are_not_rewritten(self):
        self.assert_legacy_reference_preserved(lambda: AuditEvent.objects.create(**self.audit_values()))

    def test_legacy_status_does_not_disable_baseline_integrity_validation(self):
        self.legacy_context()
        self.session.caliber_mm = Decimal("NaN")
        with self.assertRaises(ValidationError):
            self.session.full_clean()
