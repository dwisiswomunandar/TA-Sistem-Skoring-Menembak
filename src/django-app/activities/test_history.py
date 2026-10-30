"""Parent integrity only; all official/authenticated states here are synthetic fixtures."""

from contextlib import contextmanager
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models.deletion import ProtectedError
from django.db.models.query import QuerySet
from django.utils import timezone

from accounts.models import Participant
from activities.models import Activity, ActivityParticipation, TrainingSession
from assessments.models import Assessment, AuditEvent, TargetRing, TargetVersion
from assessments.test_results import ResultTestCase

User = get_user_model()


@contextmanager
def traced_locks():
    """Observe ORM lock requests; SQLite does not implement actual row locking."""
    recorded = []
    original_fetch = QuerySet._fetch_all

    def fetch(queryset):
        trace = queryset.query.select_for_update and queryset._result_cache is None
        result = original_fetch(queryset)
        if trace:
            recorded.append((queryset.model.__name__, [getattr(row, "pk", row) for row in queryset._result_cache],
                             queryset.query.select_related))
        return result

    with patch.object(QuerySet, "_fetch_all", fetch):
        yield recorded


class HistoryTestCase(ResultTestCase):
    def another_participation(self, **overrides):
        person = Participant.objects.create(
            full_name="Synthetic other participant", identity_status="verified",
            verified_by=self.actor, verified_at=timezone.now(),
        )
        values = {"activity": self.session.participation.activity, "participant": person,
                  "registration_status": "approved", "participant_identifier": uuid4().hex,
                  "decided_by": self.actor, "decided_at": timezone.now()}
        values.update(overrides)
        return ActivityParticipation.objects.create(**values)

    def empty_session(self, **overrides):
        values = {"participation": self.another_participation(), "session_number": 1,
                  "assigned_assessor": self.actor, "created_by": self.actor, "weapon_type": "pistol",
                  "shooting_position": "berdiri", "distance_m": Decimal("10"), "caliber_mm": Decimal("9")}
        values.update(overrides)
        return TrainingSession.objects.create(**values)

    def freeze_session_fields(self):
        baseline = TrainingSession.objects.filter(pk=self.session.pk).values().get()
        other = self.another_participation()
        assessor = User.objects.create_user("other-history-assessor@example.com")
        for field, value in (
            ("participation", other), ("session_number", 2), ("weapon_type", "laras_panjang"),
            ("shooting_position", "tiarap"), ("distance_m", Decimal("20")),
            ("caliber_mm", Decimal("5.56")), ("assigned_assessor", assessor), ("created_by", assessor),
        ):
            instance = TrainingSession.objects.get(pk=self.session.pk)
            setattr(instance, field, value)
            with self.subTest(field=field), self.assertRaises(ValidationError) as error:
                instance.save()
            self.assertIn(field, error.exception.message_dict)
            self.assertEqual(TrainingSession.objects.filter(pk=self.session.pk).values().get(), baseline)

    def freeze_participation_fields(self, participation):
        baseline = ActivityParticipation.objects.filter(pk=participation.pk).values().get()
        activity = Activity.objects.create(name="Synthetic replacement activity", training_date=timezone.localdate(), created_by=self.actor)
        person = Participant.objects.create(full_name="Synthetic replacement person")
        for field, value in (("activity", activity), ("participant", person)):
            instance = ActivityParticipation.objects.get(pk=participation.pk)
            setattr(instance, field, value)
            with self.subTest(field=field), self.assertRaises(ValidationError) as error:
                instance.save()
            self.assertIn(field, error.exception.message_dict)
            self.assertEqual(ActivityParticipation.objects.filter(pk=participation.pk).values().get(), baseline)


class SessionHistoryTests(HistoryTestCase):
    def test_photo_reference_freezes_all_session_context(self):
        self.make_photo()
        self.freeze_session_fields()

    def test_draft_assessment_alone_freezes_session_context(self):
        assessment = self.make_assessment()
        self.assertIsNone(assessment.photo_id)
        self.freeze_session_fields()

    def test_audit_reference_alone_freezes_session_context(self):
        AuditEvent.objects.create(**self.audit_values())
        self.freeze_session_fields()

    def test_unreferenced_session_can_be_corrected_and_reassigned(self):
        session = self.empty_session()
        destination = self.another_participation()
        assessor = User.objects.create_user("unreferenced-assessor@example.com")
        session.participation, session.session_number = destination, 2
        session.weapon_type, session.caliber_mm = "laras_panjang", Decimal("5.56")
        session.shooting_position, session.distance_m = "tiarap", Decimal("100")
        session.assigned_assessor = assessor
        session.save()
        session.refresh_from_db()
        self.assertEqual((session.participation_id, session.session_number), (destination.pk, 2))
        self.assertEqual((session.weapon_type, session.caliber_mm, session.edit_version), ("laras_panjang", Decimal("5.56"), 2))
        self.assertEqual(session.assigned_assessor_id, assessor.pk)

    def test_reparenting_unreferenced_session_rechecks_destination_eligibility(self):
        session = self.empty_session()
        destination = self.another_participation(registration_status="pending", participant_identifier=None)
        original_parent = session.participation_id
        session.participation = destination
        with self.assertRaises(ValidationError):
            session.save()
        session.refresh_from_db()
        self.assertEqual((session.participation_id, session.edit_version), (original_parent, 1))

    def test_stale_prefetched_empty_references_cannot_hide_new_history(self):
        stale = TrainingSession.objects.prefetch_related("target_photos", "assessments", "audit_events").get(pk=self.session.pk)
        self.make_photo()
        stale.distance_m = Decimal("11")
        with self.assertRaises(ValidationError):
            stale.save()
        self.session.refresh_from_db()
        self.assertEqual((self.session.distance_m, self.session.edit_version), (Decimal("10"), 1))

    def test_stale_unreferenced_instance_cannot_overwrite_another_edit(self):
        session = self.empty_session()
        stale = TrainingSession.objects.get(pk=session.pk)
        session.distance_m = Decimal("12")
        session.save()
        stale.shooting_position = "duduk"
        with self.assertRaises(ValidationError):
            stale.save()
        session.refresh_from_db()
        self.assertEqual((session.distance_m, session.shooting_position, session.edit_version), (Decimal("12"), "berdiri", 2))

    def test_noop_save_preserves_snapshot_version_and_assessment_remains_editable(self):
        assessment = self.make_assessment()
        snapshot = assessment.historical_context.copy()
        self.session.save()
        self.assertEqual(self.session.edit_version, snapshot["session_edit_version"])
        assessment.notes = "Draft edit after a parent no-op"
        assessment.save(expected_edit_version=1)
        self.assertEqual(assessment.historical_context, snapshot)

    def test_frozen_session_is_still_savable_after_withdrawal_and_activity_closure(self):
        assessment = self.make_assessment()
        participation = self.session.participation
        participation.registration_status, participation.status_reason = "withdrawn", "Synthetic withdrawal"
        participation.save()
        activity = participation.activity
        activity.status = "closed"
        activity.save()
        self.session.save()
        assessment.save(expected_edit_version=1)
        with self.assertRaises(ValidationError):
            self.empty_session(participation=participation, session_number=2)

    def test_locked_history_and_related_evidence_remain_identical(self):
        assessment = self.privileged_state()
        event = AuditEvent.objects.create(**self.audit_values(assessment=assessment))
        before = {model.__name__: list(model.objects.values()) for model in (Assessment, AuditEvent)}
        photo_metadata = (assessment.photo.sha256, assessment.photo.storage_key)
        self.session.session_number = 42
        with self.assertRaises(ValidationError):
            self.session.save()
        for model in (Assessment, AuditEvent):
            self.assertEqual(list(model.objects.values()), before[model.__name__])
        event.refresh_from_db()
        assessment.refresh_from_db()
        self.assertEqual((assessment.photo.sha256, assessment.photo.storage_key), photo_metadata)
        self.assertEqual(assessment.shot_points.get().score, 1)  # Synthetic privileged fixture only.

    def test_session_delete_uses_existing_protect_and_unreferenced_delete_is_allowed(self):
        self.make_photo()
        with self.assertRaises(ProtectedError):
            self.session.delete()
        session = self.empty_session()
        identifier = session.pk
        session.delete()
        self.assertFalse(TrainingSession.objects.filter(pk=identifier).exists())

    def test_full_clean_also_reports_history_violation(self):
        self.make_assessment()
        self.session.shooting_position = "duduk"
        with self.assertRaises(ValidationError) as error:
            self.session.full_clean()
        self.assertIn("shooting_position", error.exception.message_dict)


class ParticipationHistoryTests(HistoryTestCase):
    def test_session_reference_protects_activity_and_participant(self):
        self.freeze_participation_fields(self.session.participation)

    def test_audit_scope_without_sessions_freezes_binding(self):
        participation = self.another_participation()
        self.assertFalse(participation.training_sessions.exists())
        AuditEvent.objects.create(**self.audit_values(session=None, participation=participation))
        self.freeze_participation_fields(participation)

    def test_actor_reference_without_session_or_participation_scope_freezes_binding(self):
        participation = self.another_participation()
        event = AuditEvent(**self.audit_values(
            actor_kind="participant", actor_user=None, actor_participation=participation,
            authentication_context=self.auth_context(participation), session=None,
            activity=participation.activity, created_at=timezone.now(),
        ))
        event.full_clean()
        with self.assertRaises(ValidationError):
            event.save()  # 04A authentication gate remains closed.
        event.save_base(raw=True, force_insert=True)  # Explicit privileged fixture, not real authentication.
        self.assertFalse(participation.training_sessions.exists())
        self.assertFalse(participation.audit_events.exists())
        self.freeze_participation_fields(participation)

    def test_unused_participation_binding_remains_editable(self):
        participation = self.another_participation()
        activity = Activity.objects.create(name="New context", training_date=timezone.localdate(), status="active", created_by=self.actor)
        person = Participant.objects.create(full_name="New participant")
        participation.activity, participation.participant = activity, person
        participation.save()
        participation.refresh_from_db()
        self.assertEqual((participation.activity_id, participation.participant_id, participation.edit_version), (activity.pk, person.pk, 2))

    def test_stale_prefetched_participation_observes_new_audit_reference(self):
        participation = self.another_participation()
        stale = ActivityParticipation.objects.prefetch_related("training_sessions", "audit_events", "acted_audit_events").get(pk=participation.pk)
        AuditEvent.objects.create(**self.audit_values(session=None, participation=participation))
        stale.participant = Participant.objects.create(full_name="Attempted replacement")
        with self.assertRaises(ValidationError):
            stale.save()
        participation.refresh_from_db()
        self.assertNotEqual(participation.participant_id, stale.participant_id)

    def test_stale_administrative_changes_cannot_restore_approved_status(self):
        participation = self.session.participation
        stale = ActivityParticipation.objects.get(pk=participation.pk)
        participation.registration_status, participation.status_reason = "withdrawn", "Synthetic withdrawal"
        participation.save()
        stale.status_reason = "Stale approved payload"
        with self.assertRaises(ValidationError):
            stale.save()
        participation.refresh_from_db()
        self.assertEqual((participation.registration_status, participation.edit_version), ("withdrawn", 2))

    def test_administrative_identifier_and_status_keep_canonical_context(self):
        participation = self.session.participation
        original = (participation.activity_id, participation.participant_id)
        participation.participant_identifier = "CORRECTED-SYNTHETIC-ID"
        participation.save()
        participation.registration_status, participation.status_reason = "withdrawn", "Synthetic withdrawal"
        participation.save()
        participation.refresh_from_db()
        self.assertEqual((participation.activity_id, participation.participant_id), original)
        self.assertEqual(participation.edit_version, 3)
        self.assertEqual(self.session.participation_id, participation.pk)

    def test_audited_participation_cannot_be_deleted_but_unused_one_can(self):
        participation = self.another_participation()
        AuditEvent.objects.create(**self.audit_values(session=None, participation=participation))
        with self.assertRaises(ProtectedError):
            participation.delete()
        unused = self.another_participation()
        identifier = unused.pk
        unused.delete()
        self.assertFalse(ActivityParticipation.objects.filter(pk=identifier).exists())


class ParentWriteBoundaryTests(HistoryTestCase):
    def test_bulk_base_default_and_related_managers_cannot_bypass_guards(self):
        self.make_photo()
        for instance in (self.session, self.session.participation):
            manager = type(instance).objects
            for operation in (
                lambda: manager.filter(pk=instance.pk).update(edit_version=99),
                lambda: manager.filter(pk=instance.pk).delete(),
                lambda: manager.bulk_create([instance]),
                lambda: manager.bulk_update([instance], ["edit_version"]),
                lambda: type(instance)._base_manager.filter(pk=instance.pk).update(edit_version=99),
                lambda: type(instance)._default_manager.filter(pk=instance.pk).update(edit_version=99),
                lambda: manager.update_or_create(pk=instance.pk, defaults={"edit_version": 99}),
            ):
                with self.subTest(model=type(instance).__name__), self.assertRaises(ValidationError):
                    operation()
        for manager in (self.session.participation.training_sessions,
                        self.session.participation.activity.participations,
                        self.session.participation.participant.participations):
            with self.assertRaises(ValidationError):
                manager.update(edit_version=99)

    def test_related_reparenting_cannot_use_bulk_or_partial_instance_paths(self):
        self.make_photo()
        destination = self.another_participation()
        for bulk in (True, False):
            # RelatedManager.add(bulk=False) uses atomic(savepoint=False).
            # Isolate its expected rejection from TestCase's surrounding transaction.
            with self.subTest(bulk=bulk), self.assertRaises(ValidationError), transaction.atomic():
                destination.training_sessions.add(self.session, bulk=bulk)
        self.session.refresh_from_db()
        self.assertNotEqual(self.session.participation_id, destination.pk)

    def test_partial_and_deferred_saves_are_rejected_even_without_history(self):
        session = self.empty_session()
        participation = self.another_participation()
        for instance in (session, participation):
            with self.assertRaises(ValidationError):
                instance.save(update_fields=["updated_at"])
            for deferred in (type(instance).objects.only("id").get(pk=instance.pk),
                             type(instance).objects.defer("edit_version").get(pk=instance.pk)):
                with self.assertRaises(ValidationError):
                    deferred.save()
                with self.assertRaises(ValidationError):
                    deferred.delete()

    def test_versions_and_creation_time_cannot_be_forged(self):
        for instance in (self.empty_session(), self.another_participation()):
            for field, value in (("edit_version", 99), ("edit_version", True), ("created_at", timezone.now())):
                instance.refresh_from_db()
                setattr(instance, field, value)
                with self.subTest(model=type(instance).__name__, field=field), self.assertRaises(ValidationError):
                    instance.save()

    def test_deleted_parent_cannot_be_resurrected_and_stale_delete_is_rejected(self):
        for instance in (self.empty_session(), self.another_participation()):
            model = type(instance)
            stale = model.objects.get(pk=instance.pk)
            instance.delete()
            with self.assertRaises(ValidationError):
                stale.save()
            with self.assertRaises(ValidationError):
                stale.delete()
        session = self.empty_session()
        stale = TrainingSession.objects.get(pk=session.pk)
        session.distance_m = Decimal("12")
        session.save()
        with self.assertRaises(ValidationError):
            stale.delete()
        self.assertTrue(TrainingSession.objects.filter(pk=session.pk).exists())

    def test_new_instance_cannot_overwrite_existing_primary_key(self):
        for instance in (self.session, self.session.participation):
            values = {field.attname: getattr(instance, field.attname) for field in instance._meta.concrete_fields}
            with self.assertRaises(ValidationError):
                type(instance)(**values).save()

    def test_failed_save_preserves_stored_version_and_payload(self):
        session = self.empty_session()
        session.distance_m = Decimal("0")
        with self.assertRaises(ValidationError):
            session.save()
        self.assertEqual(session.edit_version, 1)
        session.refresh_from_db()
        self.assertEqual((session.distance_m, session.edit_version), (Decimal("10"), 1))
        session.distance_m = Decimal("20")
        with patch.object(models.Model, "save", side_effect=ValidationError("Synthetic database failure")):
            with self.assertRaises(ValidationError):
                session.save()
        self.assertEqual(session.edit_version, 1)
        session.refresh_from_db()
        self.assertEqual(session.distance_m, Decimal("10"))

    def test_async_instance_and_queryset_guards_preserve_context(self):
        self.make_photo()
        self.session.distance_m = Decimal("12")
        participation = self.session.participation
        participation.participant = Participant.objects.create(full_name="Replacement")
        for instance in (self.session, participation):
            with self.assertRaises(ValidationError):
                async_to_sync(instance.asave)()
            queryset = type(instance).objects.filter(pk=instance.pk)
            for operation in (
                lambda: async_to_sync(queryset.aupdate)(edit_version=99),
                lambda: async_to_sync(queryset.adelete)(),
                lambda: async_to_sync(queryset.abulk_update)([instance], ["edit_version"]),
            ):
                with self.assertRaises(ValidationError):
                    operation()

    def test_raw_sql_boundary_remains_explicit(self):
        self.make_photo()
        self.raw(TrainingSession).filter(pk=self.session.pk).update(distance_m=Decimal("12"))
        self.session.refresh_from_db()
        self.assertEqual(self.session.distance_m, Decimal("12"))


class ParentLockOrderTests(HistoryTestCase):
    def test_existing_session_is_locked_before_sorted_participations_without_join_locks(self):
        destination = self.another_participation()
        source = self.another_participation()
        session = self.empty_session(participation=source)
        session.participation = destination
        with traced_locks() as trace:
            session.save()
        self.assertEqual([entry[0] for entry in trace], ["TrainingSession", "ActivityParticipation", "Activity", "Participant"])
        self.assertEqual(trace[1][1], sorted([source.pk, destination.pk]))
        self.assertTrue(all(not entry[2] for entry in trace))

    def test_new_session_starts_at_participation_because_no_session_row_exists(self):
        participation = self.another_participation()
        with traced_locks() as trace:
            self.empty_session(participation=participation)
        self.assertEqual([entry[0] for entry in trace], ["ActivityParticipation", "Activity", "Participant"])

    def test_participation_edit_never_locks_sessions_in_reverse_order(self):
        participation = self.session.participation
        participation.status_reason = "Administrative metadata only"
        with traced_locks() as trace:
            participation.save()
        self.assertEqual([entry[0] for entry in trace], ["ActivityParticipation"])

    def test_photo_and_audit_first_references_lock_domain_parents(self):
        with traced_locks() as trace:
            self.make_photo()
        self.assertEqual([entry[0] for entry in trace], ["TrainingSession"])
        with traced_locks() as trace:
            AuditEvent.objects.create(**self.audit_values())
        self.assertEqual([entry[0] for entry in trace], ["TrainingSession", "ActivityParticipation"])
        participation = self.another_participation()
        with traced_locks() as trace:
            AuditEvent.objects.create(**self.audit_values(session=None, participation=participation))
        self.assertEqual([entry[0] for entry in trace], ["ActivityParticipation"])

    def test_result_and_point_paths_remain_session_first(self):
        assessment = self.complete_draft()
        assessment.notes = "Draft only"
        with traced_locks() as trace:
            assessment.save(expected_edit_version=1)
        self.assertEqual([entry[0] for entry in trace], ["TrainingSession", "Assessment"])
        point = self.make_point(assessment)
        assessment.refresh_from_db()
        point.x_px = Decimal("15")
        with traced_locks() as trace:
            point.save(expected_edit_version=assessment.edit_version)
        self.assertEqual([entry[0] for entry in trace], ["TrainingSession", "Assessment", "ShotPoint"])

    def test_configuration_branches_keep_existing_calibration_target_ring_order(self):
        calibration = self.make_calibration()
        with traced_locks() as trace:
            self.accept(calibration)
        self.assertEqual([entry[0] for entry in trace], ["Calibration", "TargetVersion"])

    def test_target_ring_and_scoring_configuration_branches_have_no_reverse_locks(self):
        target = TargetVersion.objects.create(
            code="synthetic-lock-order", version=1, name="Synthetic target",
            width_mm=200, height_mm=300, created_by=self.actor,
        )
        ring = TargetRing.objects.create(target_version=target, ring_number=1, outer_radius_mm=10, score=1)
        ring.score = 2
        with traced_locks() as trace:
            ring.save()
        self.assertEqual([entry[0] for entry in trace], ["TargetVersion", "TargetRing"])
        with traced_locks() as trace:
            target.publish()
        self.assertEqual([entry[0] for entry in trace], ["TargetVersion"])
        with traced_locks() as trace:
            self.rule.retire()
        self.assertEqual([entry[0] for entry in trace], ["ScoringRuleVersion"])

    def test_detection_and_immutable_photo_never_request_locks_in_reverse(self):
        run = self.make_run()
        run.status = "running"
        with traced_locks() as trace:
            run.save()
        self.assertEqual([entry[0] for entry in trace], ["DetectionRun"])
        with traced_locks() as trace:
            with self.assertRaises(ValidationError):
                run.photo.save()
        self.assertEqual([entry[0] for entry in trace], ["TrainingSession", "TargetPhoto"])
