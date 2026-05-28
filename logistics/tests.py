from __future__ import annotations

from datetime import time, timedelta

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from . import authz
from .forms import TaskForm
from .models import CourierCourseItem, RecurringTaskTemplate, Task
from .services import END_OF_DAY_HOUR, generate_recurring_tasks_for_day


class AuthFlowTests(TestCase):
    def test_logout_uses_post_and_redirects_to_login(self):
        User.objects.create_user(username="u1", password="pw")
        self.client.login(username="u1", password="pw")

        resp = self.client.post(reverse("tasks:logout"))

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], reverse("tasks:login"))


class TaskFormTests(TestCase):
    def test_requires_address_or_text(self):
        form = TaskForm(
            data={
                "type": Task.Type.DELIVERY,
                "task_text": "T1",
                "due_date": str(timezone.localdate()),
                "due_slot": "eod",
                "urgency": str(Task.Urgency.NORMAL),
            }
        )
        self.assertFalse(form.is_valid())

    def test_minimal_form_defaults_title_and_end_of_day(self):
        form = TaskForm(
            data={
                "type": Task.Type.DELIVERY,
                "task_text": "Доставка на документи\nДопълнителни детайли",
                "contact_address": "София, бул. България 1\nИван: 0888123456",
                "due_date": str(timezone.localdate()),
                "due_slot": "eod",
                "urgency": str(Task.Urgency.NORMAL),
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        task = form.save(commit=False)
        self.assertEqual(task.title, "Доставка на документи")
        self.assertIn("Иван", task.address_text)
        self.assertEqual(timezone.localtime(task.due_at).hour, END_OF_DAY_HOUR)


class CourseBulkDoneTests(TestCase):
    def setUp(self):
        self.courier_group, _ = Group.objects.get_or_create(name=authz.COURIER_GROUP)
        self.user = User.objects.create_user(username="c1", password="pw")
        self.user.groups.add(self.courier_group)

        self.creator = User.objects.create_user(username="m1", password="pw")

        self.t_in = Task.objects.create(
            type=Task.Type.DELIVERY,
            title="In course",
            created_by=self.creator,
            due_at=timezone.now() + timedelta(days=1),
            urgency=Task.Urgency.NORMAL,
        )
        self.t_out = Task.objects.create(
            type=Task.Type.DELIVERY,
            title="Not in course",
            created_by=self.creator,
            due_at=timezone.now() + timedelta(days=1),
            urgency=Task.Urgency.NORMAL,
        )
        CourierCourseItem.objects.create(courier=self.user, task=self.t_in)

        self.t_ready = Task.objects.create(
            type=Task.Type.DELIVERY,
            title="Ready in course",
            created_by=self.creator,
            due_at=timezone.now() + timedelta(days=1),
            urgency=Task.Urgency.NORMAL,
        )
        CourierCourseItem.objects.create(courier=self.user, task=self.t_ready)

    def test_bulk_done_only_affects_course_tasks(self):
        self.client.login(username="c1", password="pw")
        resp = self.client.post(
            reverse("tasks:course_bulk_done"),
            data={"task_ids": [str(self.t_in.id), str(self.t_out.id)]},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)

        self.t_in.refresh_from_db()
        self.t_out.refresh_from_db()
        self.assertEqual(self.t_in.status, Task.Status.DONE)
        self.assertEqual(self.t_out.status, Task.Status.OPEN)

    def test_finish_except_not_ready_carries_selected_and_finishes_rest(self):
        self.client.login(username="c1", password="pw")
        before = timezone.localdate()

        resp = self.client.post(
            reverse("tasks:course_bulk_done"),
            data={"mode": "finish_except_not_ready", "task_ids": [str(self.t_in.id)]},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)

        self.t_in.refresh_from_db()
        self.t_ready.refresh_from_db()
        self.assertEqual(self.t_in.status, Task.Status.OPEN)
        self.assertEqual(timezone.localtime(self.t_in.due_at).date(), before + timedelta(days=1))
        self.assertEqual(self.t_ready.status, Task.Status.DONE)

    def test_removing_from_course_returns_task_to_unassigned_list(self):
        self.client.login(username="c1", password="pw")
        resp = self.client.post(
            reverse("tasks:course_remove", args=[self.t_in.id]),
            data={"next": reverse("tasks:list")},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(CourierCourseItem.objects.filter(courier=self.user, task=self.t_in).exists())

        resp = self.client.get(reverse("tasks:list"))
        ids = [task.id for task in resp.context["page"].object_list]
        self.assertIn(self.t_in.id, ids)


class RecurringTaskTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="merchant", password="pw")

    def test_daily_template_generates_once_and_does_not_duplicate_when_open(self):
        template = RecurringTaskTemplate.objects.create(
            task_text="Вземане на карти от Борика",
            contact_address="Борика, контакт: Иван",
            type=Task.Type.PICKUP,
            urgency=Task.Urgency.NORMAL,
            frequency=RecurringTaskTemplate.Frequency.DAILY,
            due_time=time(14, 0),
            created_by=self.user,
        )

        self.assertEqual(generate_recurring_tasks_for_day(timezone.localdate()), 1)
        self.assertEqual(generate_recurring_tasks_for_day(timezone.localdate()), 0)
        self.assertEqual(Task.objects.filter(recurring_template=template).count(), 1)

        tomorrow = timezone.localdate() + timedelta(days=1)
        self.assertEqual(generate_recurring_tasks_for_day(tomorrow), 0)
        self.assertEqual(Task.objects.filter(recurring_template=template).count(), 1)

    def test_weekly_template_generates_only_on_matching_weekday(self):
        today = timezone.localdate()
        template = RecurringTaskTemplate.objects.create(
            task_text="Вземане на пратки на Гопет",
            contact_address="Гопет склад",
            type=Task.Type.PICKUP,
            urgency=Task.Urgency.NORMAL,
            frequency=RecurringTaskTemplate.Frequency.WEEKLY,
            weekday=today.weekday(),
            due_time=time(9, 0),
            created_by=self.user,
        )

        self.assertEqual(generate_recurring_tasks_for_day(today), 1)
        task = Task.objects.get(recurring_template=template)
        self.assertEqual(timezone.localtime(task.due_at).time().replace(second=0, microsecond=0), time(9, 0))


class TaskListOrderingTests(TestCase):
    def setUp(self):
        self.creator = User.objects.create_user(username="u1", password="pw")

        now = timezone.now()
        self.overdue = Task.objects.create(
            type=Task.Type.DELIVERY,
            title="Overdue",
            created_by=self.creator,
            due_at=now - timedelta(hours=2),
            urgency=Task.Urgency.NORMAL,
        )
        self.future = Task.objects.create(
            type=Task.Type.DELIVERY,
            title="Future",
            created_by=self.creator,
            due_at=now + timedelta(hours=2),
            urgency=Task.Urgency.NORMAL,
        )
        self.urgent_future = Task.objects.create(
            type=Task.Type.DELIVERY,
            title="Urgent future",
            created_by=self.creator,
            due_at=now + timedelta(hours=4),
            urgency=Task.Urgency.URGENT,
        )

    def test_default_sort_is_urgency_then_due_date(self):
        self.client.login(username="u1", password="pw")
        resp = self.client.get(reverse("tasks:list"))
        self.assertEqual(resp.status_code, 200)
        page = resp.context["page"]
        first = page.object_list[0]
        self.assertEqual(first.id, self.urgent_future.id)

    def test_due_sort_puts_soonest_first(self):
        self.client.login(username="u1", password="pw")
        resp = self.client.get(reverse("tasks:list"), data={"sort": "due"})
        self.assertEqual(resp.status_code, 200)
        page = resp.context["page"]
        first = page.object_list[0]
        self.assertEqual(first.id, self.overdue.id)

    def test_default_list_excludes_done_and_assigned_tasks(self):
        courier = User.objects.create_user(username="courier2", password="pw")
        done_task = Task.objects.create(
            type=Task.Type.DELIVERY,
            title="Done",
            created_by=self.creator,
            due_at=timezone.now(),
            urgency=Task.Urgency.URGENT,
            status=Task.Status.DONE,
        )
        CourierCourseItem.objects.create(courier=courier, task=self.urgent_future)

        self.client.login(username="u1", password="pw")
        resp = self.client.get(reverse("tasks:list"))
        self.assertEqual(resp.status_code, 200)
        ids = [task.id for task in resp.context["page"].object_list]

        self.assertNotIn(done_task.id, ids)
        self.assertNotIn(self.urgent_future.id, ids)
        self.assertIn(self.overdue.id, ids)
