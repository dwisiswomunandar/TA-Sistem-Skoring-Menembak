"""REM-001 regression cases; all dimensions and matrices are synthetic."""

from copy import deepcopy
from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, models, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone

from .models import Calibration, ScoringRuleVersion, TargetRing, TargetVersion
from .test_evidence import EvidenceTestCase, MATRIX
from .tests import ConfigurationTestCase


class ConfigurationLifecycleTests(ConfigurationTestCase):
    def configurations(self):
        target = self.make_target()
        self.add_rings(target)
        return (target, self.make_rule())

    def test_two_draft_writers_cannot_overwrite_or_publish_newer_payload(self):
        for row in self.configurations():
            stale = type(row).objects.get(pk=row.pk)
            row.code += "-new"
            row.save()
            before = type(row).objects.filter(pk=row.pk).values().get()
            for action in (stale.save, stale.publish, stale.delete):
                with self.assertRaises(ValidationError):
                    action()
                self.assertEqual(type(row).objects.filter(pk=row.pk).values().get(), before)

    def test_direct_status_save_also_rejects_stale_publication(self):
        row = self.make_rule()
        stale = ScoringRuleVersion.objects.get(pk=row.pk)
        row.numeric_precision = "new synthetic precision"
        row.save()
        stale.status = "published"
        with self.assertRaises(ValidationError):
            stale.save()
        row.refresh_from_db()
        self.assertEqual(row.status, "draft")
        self.assertEqual(row.numeric_precision, "new synthetic precision")

    def test_versions_increment_once_and_explicit_expected_token_is_checked(self):
        row = self.make_rule()
        self.assertEqual(row.edit_version, 1)
        row.save(expected_edit_version=1)
        self.assertEqual(row.edit_version, 2)
        for invalid in (True, 0, 1, "2"):
            with self.assertRaises(ValidationError):
                row.publish(expected_edit_version=invalid)
        row.publish(expected_edit_version=2)
        self.assertEqual(row.edit_version, 3)
        row.retire(expected_edit_version=3)
        self.assertEqual(row.edit_version, 4)

    def test_server_version_cannot_be_changed_and_validation_failure_preserves_it(self):
        row = self.make_rule()
        for invalid in (True, 0, 2):
            row.edit_version = invalid
            with self.assertRaises(ValidationError):
                row.save()
        row.refresh_from_db()
        row.algorithm_version = ""
        with self.assertRaises(ValidationError):
            row.publish()
        self.assertEqual(row.edit_version, 1)
        self.assertEqual(ScoringRuleVersion.objects.get(pk=row.pk).edit_version, 1)

    def test_deleted_configurations_cannot_resurrect(self):
        for row in (self.make_target(), self.make_rule()):
            stale = type(row).objects.get(pk=row.pk)
            identifier = row.pk
            row.delete()
            for instance in (row, stale):
                with self.assertRaises(ValidationError):
                    instance.save()
            self.assertFalse(type(row).objects.filter(pk=identifier).exists())

    def test_new_instance_cannot_replace_existing_primary_key(self):
        for row in (self.make_target(), self.make_rule()):
            replacement = type(row)(**{f.attname: getattr(row, f.attname) for f in row._meta.fields})
            with self.assertRaises(ValidationError):
                replacement.save()

    def test_deferred_and_partial_saves_are_rejected(self):
        row = self.make_rule()
        for instance, options in ((ScoringRuleVersion.objects.only("id", "code").get(pk=row.pk), {}),
                                  (row, {"update_fields": ["code"]})):
            with self.assertRaises(ValidationError):
                instance.save(**options)

    def test_stale_retirement_does_not_change_persisted_state(self):
        row = self.make_rule()
        stale = ScoringRuleVersion.objects.get(pk=row.pk)
        row.publish()
        with self.assertRaises(ValidationError):
            stale.retire()
        row.refresh_from_db()
        self.assertEqual(row.status, "published")

    def test_ring_create_edit_delete_invalidates_loaded_target(self):
        target = self.make_target()
        ring = TargetRing(target_version=target, ring_number=1, outer_radius_mm=10, score=1)
        for action in (ring.save, ring.save, ring.delete):
            old = target.edit_version
            action()
            with self.assertRaises(ValidationError):
                target.publish()
            target.refresh_from_db()
            self.assertEqual(target.edit_version, old + 1)

    def test_stale_ring_edits_deletes_and_moves_are_rejected(self):
        first, second = self.make_target(), self.make_target()
        ring = self.add_rings(first)[0]
        stale = TargetRing.objects.get(pk=ring.pk)
        ring.score = 11
        ring.save()
        versions = list(TargetVersion.objects.order_by("pk").values_list("edit_version", flat=True))
        for action in (stale.save, stale.delete):
            with self.assertRaises(ValidationError):
                action()
        stale.target_version = second
        with self.assertRaises(ValidationError):
            stale.save()
        self.assertEqual(list(TargetVersion.objects.order_by("pk").values_list("edit_version", flat=True)), versions)

    def test_move_bumps_both_aggregates_once(self):
        first, second = self.make_target(), self.make_target()
        ring = self.add_rings(first)[0]
        versions = first.edit_version, second.edit_version
        ring.target_version = second
        ring.save()
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual((first.edit_version, second.edit_version), (versions[0] + 1, versions[1] + 1))

    def test_deleted_ring_cannot_resurrect_or_bump_target(self):
        target = self.make_target()
        ring = self.add_rings(target)[0]
        stale = TargetRing.objects.get(pk=ring.pk)
        ring.delete()
        target.refresh_from_db()
        version = target.edit_version
        with self.assertRaises(ValidationError):
            stale.save()
        self.assertFalse(TargetRing.objects.filter(pk=stale.pk).exists())
        self.assertEqual(TargetVersion.objects.get(pk=target.pk).edit_version, version)

    def test_failed_ring_change_rolls_back_ring_and_aggregate(self):
        target = self.make_target()
        ring = self.add_rings(target)[0]
        before = TargetRing.objects.filter(pk=ring.pk).values().get()
        version = target.edit_version
        ring.score = 11
        with patch("assessments.models.bump_target", side_effect=RuntimeError("synthetic failure")):
            with self.assertRaises(RuntimeError):
                ring.save()
        self.assertEqual(TargetRing.objects.filter(pk=ring.pk).values().get(), before)
        self.assertEqual(TargetVersion.objects.get(pk=target.pk).edit_version, version)
        self.assertEqual(ring.edit_version, before["edit_version"])

    def test_async_writes_use_the_same_expected_version_guard(self):
        row = self.make_rule()
        async_to_sync(row.asave)(expected_edit_version=1)
        self.assertEqual(row.edit_version, 2)
        with self.assertRaises(ValidationError):
            async_to_sync(row.asave)(expected_edit_version=1)
        with self.assertRaises(ValidationError):
            async_to_sync(row.adelete)(expected_edit_version=1)

    def test_version_positive_constraint_in_database(self):
        for row in (*self.configurations(), self.add_rings(self.make_target())[0]):
            with self.assertRaises(IntegrityError), transaction.atomic():
                models.QuerySet(model=type(row)).filter(pk=row.pk).update(edit_version=0)


class CalibrationLifecycleTests(EvidenceTestCase):
    def test_stale_matrix_cannot_be_accepted_edited_or_deleted(self):
        row = self.make_calibration()
        stale = Calibration.objects.get(pk=row.pk)
        row.transform_matrix = deepcopy(MATRIX)
        row.transform_matrix[0][2] += 1
        row.save()
        before = Calibration.objects.filter(pk=row.pk).values().get()
        for action in (stale.save, stale.delete):
            with self.assertRaises(ValidationError):
                action()
        stale.status, stale.accepted_by = "accepted", self.actor
        with self.assertRaises(ValidationError):
            stale.save()
        self.assertEqual(Calibration.objects.filter(pk=row.pk).values().get(), before)

    def test_versioned_acceptance_and_rejection_keep_terminal_guards(self):
        for status in ("accepted", "rejected"):
            row = self.make_calibration()
            row.save(expected_edit_version=1)
            row.status = status
            if status == "accepted":
                row.accepted_by = self.actor
            row.save(expected_edit_version=2)
            self.assertEqual(row.edit_version, 3)
            with self.assertRaises(ValidationError):
                row.save()
            with self.assertRaises(ValidationError):
                row.delete()

    def test_failed_acceptance_preserves_version_and_metadata(self):
        row = self.make_calibration(transform_matrix=None)
        row.status, row.accepted_by = "accepted", self.actor
        with self.assertRaises(ValidationError):
            row.save()
        self.assertEqual(row.edit_version, 1)
        row.refresh_from_db()
        self.assertEqual((row.status, row.accepted_at, row.edit_version), ("draft", None, 1))

    def test_deleted_and_forged_calibration_instances_cannot_write(self):
        row = self.make_calibration()
        replacement = Calibration(**{f.attname: getattr(row, f.attname) for f in row._meta.fields})
        with self.assertRaises(ValidationError):
            replacement.save()
        stale = Calibration.objects.get(pk=row.pk)
        row.delete()
        with self.assertRaises(ValidationError):
            stale.save()


class LifecycleMigrationTests(TransactionTestCase):
    def test_migration_preserves_approved_payloads_and_initializes_tokens(self):
        before = ("assessments", "0003_assessment_shotpoint_auditevent")
        after = ("assessments", "0004_harden_configuration_calibration_lifecycle")
        executor = MigrationExecutor(connection)
        executor.migrate([before])
        try:
            old = executor.loader.project_state([before]).apps
            user = old.get_model("accounts", "User").objects.create(email="rem001@example.com")
            person = old.get_model("accounts", "Participant").objects.create(full_name="Synthetic")
            activity = old.get_model("activities", "Activity").objects.create(
                name="Synthetic", training_date=timezone.localdate(), created_by_id=user.pk)
            participation = old.get_model("activities", "ActivityParticipation").objects.create(
                activity_id=activity.pk, participant_id=person.pk)
            session = old.get_model("activities", "TrainingSession").objects.create(
                participation_id=participation.pk, session_number=1, assigned_assessor_id=user.pk,
                created_by_id=user.pk, weapon_type="pistol", shooting_position="berdiri", distance_m=10, caliber_mm=9)
            target = old.get_model("assessments", "TargetVersion").objects.create(
                code="synthetic", version=1, name="Synthetic", width_mm=200, height_mm=300,
                status="published", published_at=timezone.now(), created_by_id=user.pk)
            ring = old.get_model("assessments", "TargetRing").objects.create(
                target_version_id=target.pk, ring_number=1, outer_radius_mm=10, score=1)
            rule = old.get_model("assessments", "ScoringRuleVersion").objects.create(
                code="synthetic", version=1, status="retired", published_at=timezone.now(),
                created_by_id=user.pk, algorithm_version="synthetic", boundary_policy="synthetic", numeric_precision="synthetic")
            photo = old.get_model("assessments", "TargetPhoto").objects.create(
                session_id=session.pk, storage_key="originals/synthetic-rem001.png", sha256="a" * 64,
                byte_size=1, width_px=80, height_px=60, verified_mime_type="image/png", uploaded_by_id=user.pk,
                preprocessing_version="synthetic", analysis_width_px=80, analysis_height_px=60)
            calibration = old.get_model("assessments", "Calibration").objects.create(
                photo_id=photo.pk, target_version_id=target.pk, status="accepted", transform_matrix=deepcopy(MATRIX),
                method_version="synthetic", created_by_id=user.pk, accepted_by_id=user.pk, accepted_at=timezone.now())
            fixtures = [(type(row).__name__, row.pk, type(row).objects.filter(pk=row.pk).values().get())
                        for row in (target, ring, rule, calibration)]
            executor = MigrationExecutor(connection)
            executor.migrate([after])
            new = executor.loader.project_state([after]).apps
            for name, identifier, values in fixtures:
                stored = new.get_model("assessments", name).objects.filter(pk=identifier).values().get()
                self.assertEqual(stored.pop("edit_version"), 1)
                self.assertEqual(stored, values)
            MigrationExecutor(connection).migrate([before])
            for name, identifier, values in fixtures:
                self.assertEqual(old.get_model("assessments", name).objects.filter(pk=identifier).values().get(), values)
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
