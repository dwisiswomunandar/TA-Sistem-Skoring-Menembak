"""All dimensions, ring values, and rule policies here are synthetic fixtures."""

from decimal import Decimal

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.utils import timezone

from assessments.models import ScoringRuleVersion, TargetRing, TargetVersion

User = get_user_model()


class ConfigurationTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.creator = User.objects.create_user("configuration-author@example.com")

    def setUp(self):
        self.sequence = 0

    def make_target(self, **overrides):
        self.sequence += 1
        values = {
            "code": f"fixture-target-{self.sequence}", "version": 1,
            "name": "Synthetic target", "width_mm": Decimal("200.0000"),
            "height_mm": Decimal("300.0000"), "created_by": self.creator,
        }
        values.update(overrides)
        return TargetVersion.objects.create(**values)

    def make_rule(self, **overrides):
        self.sequence += 1
        values = {
            "code": f"fixture-rule-{self.sequence}", "version": 1,
            "created_by": self.creator, "algorithm_version": "fixture-contract-v1",
            "boundary_policy": "Synthetic boundary-policy description, not a field standard.",
            "numeric_precision": "Synthetic precision specification, not an engine default.",
        }
        values.update(overrides)
        return ScoringRuleVersion.objects.create(**values)

    def add_rings(self, target, specifications=None):
        specifications = specifications or ((1, "10", 10), (2, "20", 10), (3, "30", 8))
        return [
            TargetRing.objects.create(
                target_version=target, ring_number=number,
                outer_radius_mm=Decimal(radius), score=score,
            )
            for number, radius, score in specifications
        ]

    def published_target(self):
        target = self.make_target()
        self.add_rings(target)
        target.publish()
        return target

    def raw_queryset(self, model):
        # Deliberate trusted test bypass: prove SQL constraints independently of managers.
        return models.QuerySet(model=model, using="default")

    def assert_database_rejects(self, instance, **changes):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.raw_queryset(type(instance)).filter(pk=instance.pk).update(**changes)


class TargetVersionTests(ConfigurationTestCase):
    def test_saved_draft_can_be_edited_and_then_published(self):
        target = self.make_target()
        target.name = "Edited synthetic target"
        target.save()
        self.add_rings(target)
        target.publish()
        target.refresh_from_db()
        self.assertEqual(target.status, "published")
        self.assertEqual(target.name, "Edited synthetic target")
        self.assertIsNotNone(target.published_at)

    def test_adjacent_equal_scores_are_allowed(self):
        target = self.make_target()
        self.add_rings(target, ((1, "10", 10), (2, "20", 10), (3, "30", 10)))
        target.publish()
        self.assertEqual(target.status, "published")

    def test_ring_numbers_need_not_be_consecutive(self):
        target = self.make_target()
        self.add_rings(target, ((1, "10", 10), (4, "20", 8)))
        target.publish()
        self.assertEqual(target.status, "published")

    def test_publication_requires_a_ring_and_rolls_back_status(self):
        target = self.make_target()
        with self.assertRaises(ValidationError):
            target.publish()
        self.assertEqual(target.status, "draft")
        self.assertIsNone(target.published_at)
        target.refresh_from_db()
        self.assertEqual(target.status, "draft")

    def test_publication_rejects_equal_or_decreasing_radii(self):
        for radius in ("10", "9"):
            target = self.make_target()
            self.add_rings(target, ((1, "10", 10), (2, radius, 8)))
            with self.subTest(radius=radius), self.assertRaises(ValidationError):
                target.publish()
            target.refresh_from_db()
            self.assertEqual(target.status, "draft")

    def test_publication_rejects_scores_increasing_outward(self):
        target = self.make_target()
        self.add_rings(target, ((1, "10", 8), (2, "20", 10)))
        with self.assertRaises(ValidationError):
            target.publish()

    def test_geometry_checks_both_field_dimensions(self):
        for width, height in (("39", "200"), ("200", "39")):
            target = self.make_target(width_mm=Decimal(width), height_mm=Decimal(height))
            self.add_rings(target, ((1, "20", 10),))
            with self.subTest(width=width, height=height), self.assertRaises(ValidationError):
                target.publish()

    def test_ring_may_touch_field_boundary_without_an_invented_margin(self):
        target = self.make_target(width_mm=Decimal("40"), height_mm=Decimal("60"))
        self.add_rings(target, ((1, "20", 10),))
        target.publish()
        self.assertEqual(target.status, "published")

    def test_nonpositive_nonfinite_and_overprecise_dimensions_are_rejected(self):
        for field in ("width_mm", "height_mm"):
            for value in ("0", "-1", "NaN", "Infinity", "1.00001"):
                with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                    self.make_target(**{field: Decimal(value)})

    def test_target_has_no_default_physical_dimensions(self):
        for field in ("width_mm", "height_mm"):
            with self.subTest(field=field), self.assertRaises(ValidationError) as error:
                self.make_target(**{field: None})
            self.assertIn(field, error.exception.message_dict)

    def test_blank_code_name_and_invalid_version_are_rejected(self):
        for changes in ({"code": " \t "}, {"name": " \t "}, {"version": 0}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                self.make_target(**changes)

    def test_target_code_version_is_unique_in_database(self):
        first = self.make_target()
        second = self.make_target()
        self.assert_database_rejects(second, code=first.code, version=first.version)

    def test_database_enforces_target_dimensions_status_and_metadata(self):
        target = self.make_target()
        for changes in (
            {"width_mm": Decimal("0")}, {"height_mm": Decimal("-1")},
            {"version": 0}, {"code": ""}, {"name": ""}, {"status": "unknown"},
            {"status": "published"}, {"status": "retired"},
            {"published_at": timezone.now()},
        ):
            with self.subTest(changes=changes):
                self.assert_database_rejects(target, **changes)

    def test_published_and_retired_target_payloads_cannot_be_saved_or_deleted(self):
        for retired in (False, True):
            target = self.published_target()
            if retired:
                target.retire()
            for field, value in (
                ("name", "Unsafe edit"), ("width_mm", Decimal("400")),
                ("code", "unsafe-code"), ("version", 99),
                ("status", "draft"), ("published_at", None),
            ):
                target.refresh_from_db()
                setattr(target, field, value)
                with self.subTest(retired=retired, field=field), self.assertRaises(ValidationError):
                    target.save()
            with self.subTest(retired=retired), self.assertRaises(ValidationError):
                target.delete()
            target.refresh_from_db()
            self.assertEqual(target.status, "retired" if retired else "published")
            self.assertEqual(target.name, "Synthetic target")

    def test_stale_draft_cannot_overwrite_a_published_target(self):
        target = self.make_target()
        stale = TargetVersion.objects.get(pk=target.pk)
        self.add_rings(target)
        target.publish()
        stale.name = "Stale overwrite"
        with self.assertRaises(ValidationError):
            stale.save()
        target.refresh_from_db()
        self.assertEqual(target.name, "Synthetic target")

    def test_new_version_can_be_created_without_editing_published_version(self):
        first = self.published_target()
        second = self.make_target(code=first.code, version=2)
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(first.status, "published")
        self.assertEqual(second.status, "draft")

    def test_retirement_changes_status_without_persisting_unsaved_payload(self):
        target = self.published_target()
        published_at, width = target.published_at, target.width_mm
        target.width_mm = Decimal("900")
        target.retire()
        self.assertEqual(target.status, "retired")
        self.assertEqual(target.width_mm, width)
        self.assertEqual(target.published_at, published_at)
        with self.assertRaises(ValidationError):
            target.publish()

    def test_draft_or_unsaved_target_cannot_be_retired(self):
        for target in (self.make_target(), TargetVersion()):
            with self.subTest(pk=target.pk), self.assertRaises(ValidationError):
                target.retire()

    def test_initial_published_or_retired_records_are_not_accepted(self):
        for state in ("published", "retired"):
            with self.subTest(state=state), self.assertRaises(ValidationError):
                self.make_target(status=state, published_at=timezone.now())

    def test_draft_target_requires_explicit_removal_of_protected_rings(self):
        target = self.make_target()
        ring = self.add_rings(target, ((1, "10", 10),))[0]
        with self.assertRaises(ProtectedError):
            target.delete()
        ring.delete()
        pk = target.pk
        target.delete()
        self.assertFalse(TargetVersion.objects.filter(pk=pk).exists())


class TargetRingTests(ConfigurationTestCase):
    def test_draft_rings_can_be_created_edited_moved_and_deleted(self):
        first, second = self.make_target(), self.make_target()
        ring = self.add_rings(first, ((1, "10", 10),))[0]
        ring.outer_radius_mm = Decimal("12")
        ring.save()
        ring.target_version = second
        ring.save()
        ring.refresh_from_db()
        self.assertEqual(ring.target_version, second)
        self.assertEqual(ring.outer_radius_mm, Decimal("12"))
        pk = ring.pk
        ring.delete()
        self.assertFalse(TargetRing.objects.filter(pk=pk).exists())

    def test_ring_number_radius_and_score_are_positive(self):
        target = self.make_target()
        for changes in (
            {"ring_number": 0}, {"outer_radius_mm": Decimal("0")},
            {"outer_radius_mm": Decimal("-1")}, {"score": 0}, {"score": -1},
        ):
            values = {
                "target_version": target, "ring_number": 1,
                "outer_radius_mm": Decimal("10"), "score": 10,
            }
            values.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                TargetRing.objects.create(**values)

    def test_ring_radius_has_no_hidden_default_and_rejects_nonfinite_values(self):
        target = self.make_target()
        for radius in (None, Decimal("NaN"), Decimal("Infinity"), Decimal("1.00001")):
            with self.subTest(radius=radius), self.assertRaises(ValidationError):
                TargetRing.objects.create(
                    target_version=target, ring_number=1, outer_radius_mm=radius, score=10,
                )

    def test_invalid_or_missing_parent_is_rejected_before_writing(self):
        for parent in (None, "not-an-id", 999999):
            with self.subTest(parent=parent), self.assertRaises(ValidationError) as error:
                TargetRing.objects.create(
                    target_version_id=parent, ring_number=1,
                    outer_radius_mm=Decimal("10"), score=10,
                )
            self.assertIn("target_version", error.exception.message_dict)

    def test_ring_number_is_unique_within_target_in_database(self):
        target = self.make_target()
        first, second = self.add_rings(target, ((1, "10", 10), (2, "20", 8)))
        self.assert_database_rejects(second, ring_number=first.ring_number)
        elsewhere = self.add_rings(self.make_target(), ((1, "10", 10),))[0]
        self.assertEqual(elsewhere.ring_number, first.ring_number)

    def test_database_enforces_positive_ring_values(self):
        ring = self.add_rings(self.make_target(), ((1, "10", 10),))[0]
        for changes in (
            {"ring_number": 0}, {"outer_radius_mm": Decimal("0")},
            {"outer_radius_mm": Decimal("-1")}, {"score": 0}, {"score": -1},
        ):
            with self.subTest(changes=changes):
                self.assert_database_rejects(ring, **changes)

    def test_published_and_retired_target_rings_cannot_be_added_edited_or_deleted(self):
        for retired in (False, True):
            target = self.published_target()
            if retired:
                target.retire()
            ring = target.rings.first()
            ring.score = 7
            with self.subTest(retired=retired):
                with self.assertRaises(ValidationError):
                    ring.save()
                with self.assertRaises(ValidationError):
                    ring.delete()
                with self.assertRaises(ValidationError):
                    TargetRing.objects.create(
                        target_version=target, ring_number=99,
                        outer_radius_mm=Decimal("40"), score=1,
                    )
            ring.refresh_from_db()
            self.assertEqual(ring.score, 10)

    def test_ring_cannot_escape_a_frozen_parent_by_being_moved_to_a_draft(self):
        for retired in (False, True):
            frozen = self.published_target()
            if retired:
                frozen.retire()
            ring = frozen.rings.first()
            ring.target_version = self.make_target()
            with self.subTest(retired=retired), self.assertRaises(ValidationError):
                ring.save()
            ring.refresh_from_db()
            self.assertEqual(ring.target_version, frozen)

    def test_draft_ring_cannot_be_moved_into_a_frozen_parent(self):
        ring = self.add_rings(self.make_target(), ((99, "40", 1),))[0]
        original_parent = ring.target_version_id
        for retired in (False, True):
            frozen = self.published_target()
            if retired:
                frozen.retire()
            ring.target_version = frozen
            with self.subTest(retired=retired), self.assertRaises(ValidationError):
                ring.save()
            ring.refresh_from_db()
            self.assertEqual(ring.target_version_id, original_parent)

    def test_stale_ring_observes_parent_publication_before_saving(self):
        target = self.make_target()
        ring = self.add_rings(target, ((1, "10", 10),))[0]
        target.publish()
        ring.outer_radius_mm = Decimal("11")
        with self.assertRaises(ValidationError):
            ring.save()


class ScoringRuleVersionTests(ConfigurationTestCase):
    def test_complete_rule_can_be_published_without_executing_an_engine(self):
        rule = self.make_rule()
        policy = rule.boundary_policy
        rule.publish()
        rule.refresh_from_db()
        self.assertEqual(rule.status, "published")
        self.assertEqual(rule.method, "inward")
        self.assertEqual(rule.boundary_policy, policy)
        self.assertIsNotNone(rule.published_at)

    def test_draft_may_have_incomplete_metadata_but_cannot_be_published(self):
        for field in ("algorithm_version", "boundary_policy", "numeric_precision"):
            rule = self.make_rule(**{field: " \t "})
            with self.subTest(field=field), self.assertRaises(ValidationError) as error:
                rule.publish()
            self.assertIn(field, error.exception.message_dict)
            rule.refresh_from_db()
            self.assertEqual(rule.status, "draft")
            self.assertIsNone(rule.published_at)

    def test_none_metadata_becomes_incomplete_draft_text(self):
        for field in ("algorithm_version", "boundary_policy", "numeric_precision"):
            rule = self.make_rule(**{field: None})
            rule.refresh_from_db()
            with self.subTest(field=field):
                self.assertEqual(getattr(rule, field), "")
                with self.assertRaises(ValidationError):
                    rule.publish()

    def test_direct_status_change_still_runs_publication_validation(self):
        rule = self.make_rule(algorithm_version="")
        rule.status = "published"
        with self.assertRaises(ValidationError):
            rule.save()
        rule.refresh_from_db()
        self.assertEqual(rule.status, "draft")

    def test_rule_code_version_is_unique_in_database(self):
        first, second = self.make_rule(), self.make_rule()
        self.assert_database_rejects(second, code=first.code, version=first.version)

    def test_rule_method_must_be_inward(self):
        with self.assertRaises(ValidationError):
            self.make_rule(method="arbitrary-formula")
        rule = self.make_rule()
        self.assert_database_rejects(rule, method="outward")

    def test_database_rejects_missing_policy_on_published_or_retired_rule(self):
        rule = self.make_rule()
        for state in ("published", "retired"):
            for field in ("algorithm_version", "boundary_policy", "numeric_precision"):
                with self.subTest(state=state, field=field):
                    self.assert_database_rejects(
                        rule, status=state, published_at=timezone.now(), **{field: ""},
                    )

    def test_database_enforces_rule_version_status_and_publication_metadata(self):
        rule = self.make_rule()
        for changes in (
            {"version": 0}, {"code": ""}, {"status": "unknown"},
            {"status": "published"}, {"status": "retired"}, {"published_at": timezone.now()},
        ):
            with self.subTest(changes=changes):
                self.assert_database_rejects(rule, **changes)

    def test_published_and_retired_rule_payloads_cannot_be_saved_or_deleted(self):
        for retired in (False, True):
            rule = self.make_rule().publish()
            if retired:
                rule.retire()
            for field, value in (
                ("algorithm_version", "unsafe-algorithm"),
                ("boundary_policy", "unsafe-policy"),
                ("numeric_precision", "unsafe-precision"),
                ("status", "draft"), ("version", 99),
            ):
                rule.refresh_from_db()
                setattr(rule, field, value)
                with self.subTest(retired=retired, field=field), self.assertRaises(ValidationError):
                    rule.save()
            with self.subTest(retired=retired), self.assertRaises(ValidationError):
                rule.delete()

    def test_stale_draft_rule_cannot_overwrite_publication(self):
        rule = self.make_rule()
        stale = ScoringRuleVersion.objects.get(pk=rule.pk)
        rule.publish()
        stale.boundary_policy = "Stale replacement"
        with self.assertRaises(ValidationError):
            stale.save()
        rule.refresh_from_db()
        self.assertNotEqual(rule.boundary_policy, "Stale replacement")

    def test_rule_retirement_preserves_payload_and_publication_time(self):
        rule = self.make_rule().publish()
        algorithm, published_at = rule.algorithm_version, rule.published_at
        rule.algorithm_version = "unsaved-replacement"
        rule.retire()
        self.assertEqual(rule.status, "retired")
        self.assertEqual(rule.algorithm_version, algorithm)
        self.assertEqual(rule.published_at, published_at)
        with self.assertRaises(ValidationError):
            rule.retire()
        with self.assertRaises(ValidationError):
            rule.publish()

    def test_new_version_and_draft_deletion_do_not_modify_existing_rule(self):
        first = self.make_rule().publish()
        second = self.make_rule(code=first.code, version=2)
        pk = second.pk
        second.delete()
        self.assertFalse(ScoringRuleVersion.objects.filter(pk=pk).exists())
        first.refresh_from_db()
        self.assertEqual(first.status, "published")

    def test_rule_creation_cannot_skip_draft_and_publication_lifecycle(self):
        for state in ("published", "retired"):
            with self.subTest(state=state), self.assertRaises(ValidationError):
                self.make_rule(status=state, published_at=timezone.now())

    def test_configuration_creators_are_protected(self):
        for kind in ("target", "rule"):
            actor = User.objects.create_user(f"{kind}-creator@example.com")
            config = (
                self.make_target(created_by=actor)
                if kind == "target" else self.make_rule(created_by=actor)
            )
            with self.subTest(kind=kind), self.assertRaises(ProtectedError) as error:
                actor.delete()
            self.assertIn(config, error.exception.protected_objects)


class ConfigurationWriteBoundaryTests(ConfigurationTestCase):
    def test_public_bulk_operations_are_disabled_for_all_three_models(self):
        target = self.published_target()
        ring = target.rings.first()
        rule = self.make_rule().publish()
        prototypes = (
            TargetVersion(
                code="bulk-target-fixture", version=1, name="Bulk fixture",
                width_mm=Decimal("200"), height_mm=Decimal("300"),
                created_by=self.creator, status="published", published_at=timezone.now(),
            ),
            TargetRing(
                target_version=target, ring_number=99, outer_radius_mm=Decimal("40"), score=1,
            ),
            ScoringRuleVersion(
                code="bulk-rule-fixture", version=1, created_by=self.creator,
                status="published", published_at=timezone.now(), algorithm_version="bulk-fixture",
                boundary_policy="Fixture boundary", numeric_precision="Fixture precision",
            ),
        )
        for instance, field, value, prototype in (
            (target, "name", "Bulk overwrite", prototypes[0]),
            (ring, "score", 7, prototypes[1]),
            (rule, "boundary_policy", "Bulk overwrite", prototypes[2]),
        ):
            manager = type(instance).objects
            with self.subTest(model=type(instance).__name__, operation="update"):
                with self.assertRaises(ValidationError):
                    manager.filter(pk=instance.pk).update(**{field: value})
            with self.subTest(model=type(instance).__name__, operation="delete"):
                with self.assertRaises(ValidationError):
                    manager.filter(pk=instance.pk).delete()
            with self.subTest(model=type(instance).__name__, operation="bulk_create"):
                with self.assertRaises(ValidationError):
                    manager.bulk_create([prototype])
            with self.subTest(model=type(instance).__name__, operation="bulk_update"):
                setattr(instance, field, value)
                with self.assertRaises(ValidationError):
                    manager.bulk_update([instance], [field])

    def test_bulk_writes_are_also_disabled_for_drafts(self):
        for instance in (
            self.make_target(), self.make_rule(),
            self.add_rings(self.make_target(), ((1, "10", 10),))[0],
        ):
            with self.subTest(model=type(instance).__name__), self.assertRaises(ValidationError):
                type(instance).objects.filter(pk=instance.pk).delete()

    def test_base_and_related_managers_preserve_write_guards(self):
        target = self.published_target()
        with self.assertRaises(ValidationError):
            TargetVersion._base_manager.filter(pk=target.pk).update(name="Bypass")
        with self.assertRaises(ValidationError):
            target.rings.update(score=7)
        destination = self.make_target()
        ring = target.rings.first()
        for bulk in (True, False):
            with self.subTest(bulk=bulk), self.assertRaises(ValidationError), transaction.atomic():
                destination.rings.add(ring, bulk=bulk)
        ring.refresh_from_db()
        self.assertEqual(ring.target_version, target)

    def test_partial_saves_cannot_validate_one_payload_and_persist_another(self):
        target, rule = self.make_target(), self.make_rule()
        ring = self.add_rings(target, ((1, "10", 10),))[0]
        for instance in (target, rule, ring):
            with self.subTest(model=type(instance).__name__), self.assertRaises(ValidationError):
                instance.save(update_fields=["updated_at"])

    def test_async_save_update_and_delete_preserve_freeze_guards(self):
        target = self.published_target()
        for operation in (
            lambda: async_to_sync(target.asave)(),
            lambda: async_to_sync(target.adelete)(),
            lambda: async_to_sync(
                TargetVersion.objects.filter(pk=target.pk).aupdate,
            )(name="Async bypass"),
        ):
            with self.subTest(operation=operation), self.assertRaises(ValidationError):
                operation()

    def test_database_constraints_alone_do_not_enforce_historical_immutability(self):
        target = self.published_target()
        # A deliberately constructed raw QuerySet bypasses the application manager.
        # This documents the boundary; no PostgreSQL or external database is touched.
        self.raw_queryset(TargetVersion).filter(pk=target.pk).update(name="Privileged bypass")
        target.refresh_from_db()
        self.assertEqual(target.status, "published")
        self.assertEqual(target.name, "Privileged bypass")
