"""Scoped read routes and IDOR regressions; synthetic data in the test DB only."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group, Permission
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from accounts.models import Participant
from .models import Activity, ActivityParticipation, ActivityPermissionGrant, TrainingSession
from .read_access import readable_sessions

User = get_user_model()


class OperationalUITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.actor = User.objects.create_user("assigned@example.com")
        cls.other = User.objects.create_user("other-private@example.com")
        cls.permission = Permission.objects.get(
            content_type__app_label="assessments", content_type__model="assessment",
            codename="change_assessment",
        )
        cls.actor.user_permissions.add(cls.permission)
        cls.activity_a = cls.activity("Latihan Terlihat", cls.other)
        cls.activity_b = cls.activity("Kegiatan Rahasia B", cls.actor)
        cls.activity_c = cls.activity("Kegiatan Rahasia C", cls.actor)
        cls.person = cls.person_row("Peserta Terlihat")
        cls.hidden_person = cls.person_row("Peserta Rahasia")
        cls.participation_a = cls.participation(cls.activity_a, cls.person, "A-001")
        cls.hidden_participation = cls.participation(cls.activity_a, cls.hidden_person, "RAHASIA-A")
        # Same person in another activity must not merge memberships or sessions.
        cls.participation_b = cls.participation(cls.activity_b, cls.person, "RAHASIA-B")
        cls.participation_c = cls.participation(cls.activity_c, cls.hidden_person, "RAHASIA-C")
        cls.session_a = cls.session(cls.participation_a, cls.actor)
        cls.unassigned_session = cls.session(cls.participation_a, cls.other, number=2)
        cls.session(cls.hidden_participation, cls.other)
        cls.session_b = cls.session(cls.participation_b, cls.actor)
        cls.session(cls.participation_c, cls.other)
        cls.grant_a = cls.grant(cls.activity_a)
        cls.grant(cls.activity_c)

    @classmethod
    def activity(cls, name, creator):
        return Activity.objects.create(name=name, training_date=timezone.localdate(),
                                       status="active", created_by=creator)

    @classmethod
    def person_row(cls, name):
        return Participant.objects.create(
            full_name=name, identity_status="verified", verified_by=cls.actor,
            verified_at=timezone.now(), identity_number="PRIVATE-IDENTITY-NUMBER",
        )

    @classmethod
    def participation(cls, activity, person, identifier):
        return ActivityParticipation.objects.create(
            activity=activity, participant=person, registration_status="approved",
            participant_identifier=identifier, decided_by=cls.actor, decided_at=timezone.now(),
        )

    @classmethod
    def session(cls, participation, assessor, number=1):
        return TrainingSession.objects.create(
            participation=participation, assigned_assessor=assessor, created_by=cls.actor,
            session_number=number, weapon_type="pistol", shooting_position="berdiri",
            distance_m=15, caliber_mm=9,
        )

    @classmethod
    def grant(cls, activity, permission=None):
        return ActivityPermissionGrant.objects.create(
            activity=activity, user=cls.actor, permission=permission or cls.permission,
            created_by=cls.actor,
        )

    def setUp(self):
        self.client.force_login(self.actor)

    def detail_url(self, activity=None):
        return reverse("activities:activity_detail", args=[(activity or self.activity_a).pk])

    def sessions_url(self, activity=None, participation=None):
        return reverse("activities:participant_sessions", args=[
            (activity or self.activity_a).pk, (participation or self.participation_a).pk,
        ])

    def routes(self):
        return (reverse("activities:activity_list"), self.detail_url(), self.sessions_url())

    def assert_denied(self):
        self.assertContains(self.client.get(reverse("activities:activity_list")),
                            "Belum ada kegiatan yang dapat ditampilkan")
        self.assertEqual(self.client.get(self.detail_url()).status_code, 404)
        self.assertEqual(self.client.get(self.sessions_url()).status_code, 404)

    def test_anonymous_routes_redirect_without_database_reads(self):
        self.client.logout()
        for url in self.routes():
            with self.subTest(url=url), self.assertNumQueries(0):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.url, reverse("login") + "?next=" + url)

    def test_activity_list_only_contains_assigned_and_granted_context(self):
        response = self.client.get(reverse("activities:activity_list"))
        self.assertTemplateUsed(response, "activities/activity_list.html")
        self.assertEqual(list(response.context["page_obj"]), [self.activity_a])
        self.assertContains(response, self.activity_a.name)
        self.assertNotContains(response, self.activity_b.name)
        self.assertNotContains(response, self.activity_c.name)

    def test_navigation_from_dashboard_to_activity_participant_and_sessions(self):
        listing = reverse("activities:activity_list")
        self.assertContains(self.client.get(reverse("dashboard")), f'href="{listing}"')
        self.assertContains(self.client.get(listing), f'href="{self.detail_url()}"')
        self.assertContains(self.client.get(self.detail_url()), f'href="{self.sessions_url()}"')
        response = self.client.get(self.sessions_url())
        self.assertContains(response, f'href="{self.detail_url()}"')
        self.assertContains(response, f'href="{listing}" aria-current="page"')
        self.assertTemplateUsed(response, "ui/layout.html")
        self.assertContains(response, '/static/ui/app.css')

    def test_participant_list_is_subset_of_assigned_work_not_entire_activity(self):
        response = self.client.get(self.detail_url())
        self.assertEqual(list(response.context["page_obj"]), [self.participation_a])
        self.assertContains(response, "Peserta Terlihat")
        self.assertContains(response, "A-001")
        for secret in ("Peserta Rahasia", "RAHASIA-A", "RAHASIA-B", "PRIVATE-IDENTITY-NUMBER"):
            self.assertNotContains(response, secret)

    def test_sessions_only_show_own_assignment_and_database_parameters(self):
        response = self.client.get(self.sessions_url())
        self.assertEqual(list(response.context["page_obj"]), [self.session_a])
        self.assertContains(response, "Sesi 1")
        self.assertNotContains(response, "Sesi 2")
        self.assertContains(response, self.session_a.get_weapon_type_display())
        self.assertContains(response, self.session_a.get_shooting_position_display())
        self.assertNotContains(response, "RAHASIA-B")
        self.assertNotContains(response, self.other.email)

    def test_activity_without_grant_and_activity_without_assignment_are_404(self):
        for activity in (self.activity_b, self.activity_c):
            with self.subTest(activity=activity.pk):
                response = self.client.get(self.detail_url(activity))
                self.assertEqual(response.status_code, 404)
                self.assertNotContains(response, activity.name, status_code=404)

    def test_idor_rejects_hidden_and_cross_activity_participations(self):
        for activity, participation in (
            (self.activity_a, self.hidden_participation),
            (self.activity_a, self.participation_b),
            (self.activity_b, self.participation_a),
            (self.activity_b, self.participation_b),
        ):
            with self.subTest(activity=activity.pk, participation=participation.pk):
                response = self.client.get(self.sessions_url(activity, participation))
                self.assertEqual(response.status_code, 404)
                self.assertNotContains(response, self.person.full_name, status_code=404)
                self.assertNotContains(response, self.hidden_person.full_name, status_code=404)

    def test_cross_activity_idor_still_denied_when_both_activities_are_allowed(self):
        self.grant(self.activity_b)
        self.assertEqual(self.client.get(self.detail_url(self.activity_b)).status_code, 200)
        self.assertEqual(self.client.get(self.sessions_url(self.activity_b, self.participation_b)).status_code, 200)
        self.assertEqual(self.client.get(self.sessions_url(self.activity_a, self.participation_b)).status_code, 404)
        self.assertEqual(self.client.get(self.sessions_url(self.activity_b, self.participation_a)).status_code, 404)

    def test_missing_and_unauthorized_objects_are_indistinguishable(self):
        missing_activity = reverse("activities:activity_detail", args=[999999])
        missing_participation = reverse("activities:participant_sessions", args=[self.activity_a.pk, 999999])
        for url in (missing_activity, missing_participation, self.detail_url(self.activity_b)):
            self.assertEqual(self.client.get(url).status_code, 404)

    def test_global_permission_without_grant_is_denied(self):
        self.grant_a.is_active = False
        self.grant_a.save()
        self.assert_denied()

    def test_grant_without_global_permission_is_denied(self):
        self.actor.user_permissions.clear()
        self.assert_denied()

    def test_wrong_permission_grant_does_not_authorize_read(self):
        self.grant_a.is_active = False
        self.grant_a.save()
        permission = Permission.objects.get(content_type__app_label="assessments", codename="add_assessment")
        self.actor.user_permissions.add(permission)
        self.grant(self.activity_a, permission)
        self.assert_denied()

    def test_staff_superuser_and_creator_never_bypass_read_scope(self):
        for staff, superuser in ((True, False), (True, True)):
            with self.subTest(staff=staff, superuser=superuser):
                self.actor.is_staff, self.actor.is_superuser = staff, superuser
                self.actor.save(update_fields=("is_staff", "is_superuser"))
                self.assertEqual(self.client.get(self.detail_url(self.activity_b)).status_code, 404)
                self.assertEqual(self.client.get(self.detail_url(self.activity_c)).status_code, 404)
                self.actor.user_permissions.clear()
                self.assert_denied()
                self.actor.user_permissions.add(self.permission)

    def test_group_permission_and_revocation_ignore_cached_actor(self):
        self.actor.user_permissions.clear()
        group = Group.objects.create(name="Synthetic assigned readers")
        group.permissions.add(self.permission)
        self.actor.groups.add(group)
        self.assertTrue(self.actor.has_perm("assessments.change_assessment"))
        self.assertEqual(list(readable_sessions(actor=self.actor)), [self.session_a])
        self.assertEqual(self.client.get(self.detail_url()).status_code, 200)
        group.permissions.clear()
        self.assertFalse(readable_sessions(actor=self.actor).exists())
        self.assert_denied()

    def test_assignment_revocation_is_effective_next_request(self):
        session = TrainingSession.objects.get(pk=self.session_a.pk)
        session.assigned_assessor = self.other
        session.save()
        self.assert_denied()

    def test_inactive_user_and_untrusted_actors_cannot_read(self):
        for actor in (AnonymousUser(), self.actor.pk, User(email="unsaved@example.com")):
            with self.subTest(actor=type(actor).__name__), self.assertRaises(PermissionDenied):
                readable_sessions(actor=actor)
        self.actor.is_active = False
        self.actor.save(update_fields=("is_active",))
        with self.assertRaises(PermissionDenied):
            readable_sessions(actor=self.actor)
        for url in self.routes():
            self.assertEqual(self.client.get(url).status_code, 302)

    def test_empty_worklist_when_granted_activity_has_no_assigned_sessions(self):
        reader = User.objects.create_user("no-assignments@example.com")
        reader.user_permissions.add(self.permission)
        ActivityPermissionGrant.objects.create(
            activity=self.activity_a, user=reader, permission=self.permission, created_by=self.actor,
        )
        self.client.force_login(reader)
        response = self.client.get(reverse("activities:activity_list"))
        self.assertEqual(response.context["page_obj"].paginator.count, 0)
        self.assertContains(response, "Belum ada kegiatan yang dapat ditampilkan")
        self.assertNotContains(response, "Latihan Terlihat")

    def test_pagination_is_scoped_stable_and_does_not_duplicate_rows(self):
        self.grant(self.activity_b)
        self.session(self.participation_a, self.actor, number=3)
        self.assertEqual(self.client.get(reverse("activities:activity_list")).context["page_obj"].paginator.count, 2)
        self.assertEqual(self.client.get(self.detail_url()).context["page_obj"].paginator.count, 1)
        with patch("activities.views.PAGE_SIZE", 1):
            first = self.client.get(reverse("activities:activity_list"))
            second = self.client.get(reverse("activities:activity_list"), {"page": 2})
            self.assertContains(first, '?page=2')
            self.assertNotEqual(first.context["page_obj"][0].pk, second.context["page_obj"][0].pk)
            sessions = self.client.get(self.sessions_url(), {"page": 2})
            self.assertContains(sessions, "Sesi 3")
            self.assertNotContains(sessions, "Sesi 2")
            for page in ("invalid", "-1", "999999"):
                self.assertEqual(self.client.get(self.detail_url(), {"page": page}).status_code, 200)

    def test_participant_pagination_and_query_count_do_not_grow_per_row(self):
        participation = self.participation(self.activity_a, self.person_row("Peserta Kedua"), "A-002")
        self.session(participation, self.actor)
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.detail_url())
        self.assertContains(response, "Peserta Kedua")
        self.assertLessEqual(len(queries), 6)
        with patch("activities.views.PAGE_SIZE", 1):
            response = self.client.get(self.detail_url(), {"page": 2})
            self.assertEqual(len(response.context["page_obj"]), 1)
            self.assertEqual(response.context["page_obj"].paginator.count, 2)

    def test_querystring_cannot_select_another_actor_or_activity(self):
        response = self.client.get(self.detail_url(), {
            "actor": self.other.pk, "activity_id": self.activity_b.pk,
            "participant_id": self.hidden_person.pk, "is_superuser": "true",
        })
        self.assertContains(response, self.activity_a.name)
        self.assertNotContains(response, self.activity_b.name)
        self.assertNotContains(response, self.hidden_person.full_name)

    def test_routes_reject_mutations_and_never_cache(self):
        for url in self.routes():
            with self.subTest(url=url):
                self.assertEqual(self.client.post(url).status_code, 405)
                self.assertEqual(self.client.put(url).status_code, 405)
                response = self.client.get(url)
                self.assertIn("no-store", response.headers["Cache-Control"])
                self.assertIn("Cookie", response.headers["Vary"])
                self.assertEqual(self.client.head(url).status_code, 200)

    def test_routes_do_not_query_assessments_sensitive_fields_or_write(self):
        for url in self.routes():
            with self.subTest(url=url), CaptureQueriesContext(connection) as queries:
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
            for query in queries:
                sql = query["sql"].lower()
                self.assertTrue(sql.lstrip().startswith("select"), sql)
                self.assertNotIn('"assessments_', sql)
                self.assertNotIn('"identity_number"', sql)
                self.assertNotIn('"entry_code"', sql)

    def test_database_text_is_escaped_and_not_replaced_with_mock_data(self):
        self.activity_a.name = '<script>alert("activity")</script>'
        self.activity_a.save()
        self.person.full_name = '<script>alert("participant")</script>'
        self.person.save()
        for url in self.routes():
            response = self.client.get(url)
            self.assertNotContains(response, "<script>")
            self.assertContains(response, "&lt;script&gt;")
