from __future__ import annotations

from datetime import datetime, time, timedelta

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from . import authz
from .forms import TaskForm
from .models import Address, CourierCourseItem, RecurringTaskTemplate, Task
from .services import END_OF_DAY_HOUR, generate_recurring_tasks_for_day, roll_over_open_tasks_to_today


class AuthFlowTests(TestCase):
    def test_logout_uses_post_and_redirects_to_login(self):
        User.objects.create_user(username="u1", password="pw")
        self.client.login(username="u1", password="pw")

        resp = self.client.post(reverse("tasks:logout"))

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], reverse("tasks:login"))


class CourierPageAccessTests(TestCase):
    def setUp(self):
        courier_group, _ = Group.objects.get_or_create(name=authz.COURIER_GROUP)
        self.courier = User.objects.create_user(username="page-courier", password="pass12345")
        self.courier.groups.add(courier_group)
        self.creator = User.objects.create_user(username="page-creator", password="pass12345")
        self.task = Task.objects.create(
            type=Task.Type.DELIVERY,
            title="Курс",
            created_by=self.creator,
            due_at=timezone.now(),
            course_active=True,
        )
        self.client.login(username="page-courier", password="pass12345")

    def test_courier_root_and_non_course_pages_redirect_to_course(self):
        urls = [
            "/",
            reverse("tasks:list"),
            reverse("tasks:new"),
            reverse("tasks:reports"),
            reverse("tasks:recurring_list"),
            reverse("tasks:recurring_new"),
        ]

        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertRedirects(response, reverse("tasks:course"))

        response = self.client.get(reverse("tasks:detail", args=[self.task.pk]))
        self.assertEqual(response.status_code, 200)

    def test_courier_menu_only_offers_course_and_logout(self):
        response = self.client.get(reverse("tasks:course"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("tasks:course"))
        self.assertContains(response, reverse("tasks:logout"))
        self.assertNotContains(response, 'href="/tasks/"')
        self.assertNotContains(response, reverse("tasks:reports"))
        self.assertNotContains(response, reverse("tasks:recurring_list"))
        self.assertContains(response, reverse("tasks:detail", args=[self.task.pk]))


class TaskFormTests(TestCase):
    def test_requires_address_or_text(self):
        form = TaskForm(
            data={
                "type": Task.Type.DELIVERY,
                "client": "Клиент",
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
                "client": "Доставка на документи",
                "contact_address": "София, бул. България 1\nИван: 0888123456",
                "due_date": str(timezone.localdate()),
                "due_slot": "eod",
                "urgency": str(Task.Urgency.NORMAL),
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        task = form.save(commit=False)
        self.assertEqual(task.title, "Доставка на документи")
        self.assertEqual(task.customer_name, "Доставка на документи")
        self.assertIn("Иван", task.address_text)
        self.assertEqual(timezone.localtime(task.due_at).hour, END_OF_DAY_HOUR)
        self.assertTrue(task.course_active)

    def test_selected_date_is_used_for_course_day(self):
        selected_day = timezone.localdate() + timedelta(days=3)
        form = TaskForm(
            data={
                "type": Task.Type.DELIVERY,
                "client": "Клиент",
                "contact_address": "София",
                "due_date": selected_day.isoformat(),
                "due_slot": "noon",
                "urgency": str(Task.Urgency.NORMAL),
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        task = form.save(commit=False)
        self.assertEqual(timezone.localtime(task.due_at).date(), selected_day)

    def test_address_book_selection_sets_task_address_and_address_text(self):
        address = Address.objects.create(
            name="Клиент А",
            address_text="София, ул. Примерна 1",
        )
        form = TaskForm(
            data={
                "type": Task.Type.DELIVERY,
                "client": address.name,
                "contact_address": address.address_text,
                "due_date": timezone.localdate().isoformat(),
                "due_slot": "eod",
                "urgency": str(Task.Urgency.NORMAL),
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        task = form.save(commit=False)
        self.assertEqual(task.address, address)
        self.assertEqual(task.address_text, address.address_text)

    def test_address_book_suggestions_only_include_active_addresses(self):
        active_address = Address.objects.create(
            name="Активен клиент",
            address_text="София",
        )
        inactive_address = Address.objects.create(
            name="Неактивен клиент",
            address_text="Пловдив",
            is_active=False,
        )

        form = TaskForm()

        self.assertIn(active_address, form.address_book)
        self.assertNotIn(inactive_address, form.address_book)

    def test_address_book_suggestions_render_client_and_address(self):
        address = Address.objects.create(
            name="Клиент с адрес",
            address_text="София, бул. Примерен 10",
        )
        user = User.objects.create_user(
            username="address-form-user",
            password="pass12345",
            is_staff=True,
        )
        self.client.login(username=user.username, password="pass12345")

        response = self.client.get(reverse("tasks:new"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'data-client-name="{address.name}"')
        self.assertContains(response, f'data-address-text="{address.address_text}"')
        self.assertContains(response, "id=\"client-suggestions\"")

    def test_manually_entered_client_is_saved_with_manual_address(self):
        form = TaskForm(
            data={
                "type": Task.Type.DELIVERY,
                "client": "Нов клиент",
                "contact_address": "Варна, ул. Морска 2",
                "due_date": timezone.localdate().isoformat(),
                "due_slot": "eod",
                "urgency": str(Task.Urgency.NORMAL),
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        task = form.save(commit=False)
        self.assertEqual(task.customer_name, "Нов клиент")
        self.assertEqual(task.title, "Нов клиент")
        self.assertIsNone(task.address)
        self.assertEqual(task.address_text, "Варна, ул. Морска 2")


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
            course_active=True,
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

        self.t_in.refresh_from_db()
        self.assertFalse(self.t_in.course_active)
        resp = self.client.get(reverse("tasks:list"))
        self.assertRedirects(resp, reverse("tasks:course"))


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
        self.assertTrue(task.course_active)


class IndividualCourseTaskTests(TestCase):
    def setUp(self):
        self.courier_group, _ = Group.objects.get_or_create(name=authz.COURIER_GROUP)
        self.courier = User.objects.create_user(username="course-courier", password="pass12345")
        self.courier.groups.add(self.courier_group)
        self.creator = User.objects.create_user(username="course-creator", password="pass12345")
        merchant_group, _ = Group.objects.get_or_create(name=authz.MERCHANT_GROUP)
        self.creator.groups.add(merchant_group)
        self.course_day = timezone.localdate()
        self.task = Task.objects.create(
            type=Task.Type.DELIVERY,
            title="Курс за днес",
            created_by=self.creator,
            due_at=timezone.make_aware(datetime.combine(self.course_day, time(10))),
            urgency=Task.Urgency.NORMAL,
            course_active=True,
        )
        self.client.login(username="course-courier", password="pass12345")

    def test_course_shows_only_todays_tasks_and_ignores_date_parameter(self):
        future_task = Task.objects.create(
            type=Task.Type.DELIVERY,
            title="Курс за утре",
            created_by=self.creator,
            due_at=timezone.make_aware(
                datetime.combine(self.course_day + timedelta(days=1), time(10))
            ),
            urgency=Task.Urgency.NORMAL,
            course_active=True,
        )
        completed_task = Task.objects.create(
            type=Task.Type.DELIVERY,
            title="Вече приключена задача",
            created_by=self.creator,
            due_at=timezone.now(),
            urgency=Task.Urgency.NORMAL,
            course_active=True,
            status=Task.Status.DONE,
        )
        today_task = Task.objects.create(
            type=Task.Type.DELIVERY,
            title="Курс за днес",
            created_by=self.creator,
            due_at=timezone.now(),
            urgency=Task.Urgency.NORMAL,
            course_active=True,
        )
        response = self.client.get(
            reverse("tasks:course"),
            data={"date": self.course_day.isoformat()},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, today_task.title)
        self.assertContains(response, 'class="form-check-input course-task-checkbox"')
        self.assertContains(response, "Защо задачите не са завършени?")
        self.assertContains(response, "Приключи избраните")
        self.assertContains(response, "Маркирай като незавършени")
        self.assertNotContains(response, reverse("tasks:course_remove", args=[today_task.pk]))
        self.assertNotContains(response, future_task.title)
        self.assertNotContains(response, completed_task.title)
        content = response.content.decode()
        checkbox_position = content.index(f'id="incomplete-task-{today_task.pk}"')
        task_id_position = content.index(f">{today_task.pk}</td>", checkbox_position)
        self.assertLess(checkbox_position, task_id_position)
        self.assertEqual(response.context["selected_date"], timezone.localdate())

    def test_daily_recurring_task_generates_into_todays_course(self):
        RecurringTaskTemplate.objects.create(
            task_text="Постоянно вземане",
            contact_address="София",
            frequency=RecurringTaskTemplate.Frequency.DAILY,
            due_time=time(14, 0),
            created_by=self.creator,
        )

        response = self.client.get(reverse("tasks:course"))

        self.assertEqual(response.status_code, 200)
        recurring_task = Task.objects.get(title="Постоянно вземане")
        self.assertTrue(recurring_task.course_active)
        self.assertEqual(timezone.localtime(recurring_task.due_at).date(), timezone.localdate())
        self.assertIn(recurring_task, response.context["tasks"])

    def test_courier_can_open_details_for_a_current_course_task(self):
        response = self.client.get(reverse("tasks:detail", args=[self.task.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.task.title)
        self.assertContains(response, "Клиент:")

    def test_courier_cannot_open_details_for_a_task_outside_the_course(self):
        self.task.course_active = False
        self.task.save(update_fields=["course_active"])

        response = self.client.get(reverse("tasks:detail", args=[self.task.pk]))

        self.assertEqual(response.status_code, 404)

    def test_task_rolled_over_from_a_previous_day_stays_red_in_course(self):
        old_day = self.course_day - timedelta(days=1)
        self.task.due_at = timezone.make_aware(datetime.combine(old_day, time(10)))
        self.task.save(update_fields=["due_at"])

        response = self.client.get(reverse("tasks:course"))

        self.assertEqual(response.status_code, 200)
        self.task.refresh_from_db()
        self.assertTrue(self.task.course_overdue)
        self.assertContains(
            response,
            f'<tr id="task-row-{self.task.pk}" class="table-danger">',
        )

    def test_new_task_uses_selected_date_and_joins_its_course(self):
        selected_day = self.course_day + timedelta(days=3)
        self.client.force_login(self.creator)
        response = self.client.post(
            reverse("tasks:new"),
            data={
                "type": Task.Type.DELIVERY,
                "client": "Доставка по избрана дата",
                "contact_address": "София",
                "due_date": selected_day.isoformat(),
                "due_slot": "noon",
                "urgency": str(Task.Urgency.NORMAL),
            },
        )

        self.assertEqual(response.status_code, 302)
        created_task = Task.objects.get(title="Доставка по избрана дата")
        self.assertTrue(created_task.course_active)
        self.assertEqual(timezone.localtime(created_task.due_at).date(), selected_day)

    def test_courier_can_complete_a_course_task_individually(self):
        response = self.client.post(
            reverse("tasks:course_task_done", args=[self.task.pk]),
        )
        self.assertRedirects(
            response,
            reverse("tasks:course"),
        )
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.DONE)
        self.assertEqual(self.task.done_by, self.courier)

    def test_courier_can_remove_task_from_course(self):
        response = self.client.post(
            reverse("tasks:course_remove", args=[self.task.pk]),
            data={"next": reverse("tasks:course")},
        )

        self.assertRedirects(response, reverse("tasks:course"))
        self.task.refresh_from_db()
        self.assertFalse(self.task.course_active)

    def test_incomplete_task_details_render_without_course_remove_action(self):
        admin = User.objects.create_superuser(username="detail-admin", email="", password="pw")
        self.client.force_login(admin)
        response = self.client.get(reverse("tasks:detail", args=[self.task.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.task.title)
        self.assertNotContains(response, "Премахни")

    def test_admin_can_reassign_or_remove_incomplete_task_from_home_task_list(self):
        admin = User.objects.create_superuser(username="course-admin", email="", password="pass12345")
        self.client.force_login(admin)
        self.task.course_note = "Клиентът не беше на адреса."
        self.task.course_active = False
        self.task.save(update_fields=["course_note", "course_active"])
        list_url = reverse("tasks:list")
        response = self.client.get(list_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Добави в курса")
        self.assertContains(response, "Премахни")
        self.assertNotContains(response, "?status=open&amp;assignment=assigned")

        response = self.client.post(reverse("tasks:delete", args=[self.task.pk]))

        self.assertRedirects(response, reverse("tasks:list"))
        self.assertFalse(Task.objects.filter(pk=self.task.pk).exists())

    def test_admin_can_add_and_remove_course_task_from_task_list(self):
        admin = User.objects.create_superuser(
            username="course-list-admin",
            email="",
            password="pass12345",
        )
        self.client.force_login(admin)
        task = Task.objects.create(
            type=Task.Type.PICKUP,
            title="Администраторска задача за курс",
            created_by=self.creator,
            due_at=timezone.now(),
        )
        list_url = reverse("tasks:list")

        response = self.client.get(list_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Добави в курса")
        self.assertEqual(response.context["filters"]["assignment"], "unassigned")

        response = self.client.post(
            reverse("tasks:course_add", args=[task.pk]),
            data={"next": list_url},
        )
        self.assertRedirects(response, list_url)
        task.refresh_from_db()
        self.assertTrue(task.course_active)

        course_tasks_url = f"{list_url}?assignment=assigned"
        response = self.client.get(course_tasks_url)
        self.assertContains(response, "Премахни от курса")
        response = self.client.post(
            reverse("tasks:course_remove", args=[task.pk]),
            data={"next": course_tasks_url},
        )

        self.assertRedirects(response, course_tasks_url)
        task.refresh_from_db()
        self.assertFalse(task.course_active)

    def test_bulk_complete_only_finishes_selected_today_tasks(self):
        another_task = Task.objects.create(
            type=Task.Type.PICKUP,
            title="Друга задача",
            created_by=self.creator,
            due_at=timezone.now(),
            urgency=Task.Urgency.NORMAL,
            course_active=True,
        )

        response = self.client.post(
            reverse("tasks:course_bulk_action"),
            data={"mode": "done", "task_ids": [str(self.task.pk)]},
        )

        self.assertRedirects(response, reverse("tasks:course"))
        self.task.refresh_from_db()
        another_task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.DONE)
        self.assertEqual(self.task.done_by, self.courier)
        self.assertEqual(another_task.status, Task.Status.OPEN)
        self.assertTrue(another_task.course_active)

    def test_bulk_incomplete_moves_selected_tasks_and_saves_reason(self):
        another_task = Task.objects.create(
            type=Task.Type.PICKUP,
            title="Друга задача",
            created_by=self.creator,
            due_at=timezone.now(),
            urgency=Task.Urgency.NORMAL,
            course_active=True,
        )
        response = self.client.post(
            reverse("tasks:course_bulk_action"),
            data={
                "mode": "incomplete",
                "task_ids": [str(self.task.pk), str(another_task.pk)],
                "note": "Неуспешен адрес.",
            },
        )

        self.assertRedirects(response, reverse("tasks:course"))
        self.task.refresh_from_db()
        another_task.refresh_from_db()
        for task in (self.task, another_task):
            self.assertEqual(task.status, Task.Status.OPEN)
            self.assertFalse(task.course_active)
            self.assertEqual(task.course_note, "Неуспешен адрес.")

    def test_incomplete_task_requires_reason_and_returns_to_course(self):
        url = reverse("tasks:course_task_incomplete", args=[self.task.pk])
        response = self.client.post(url)
        self.assertRedirects(
            response,
            reverse("tasks:course"),
        )
        self.task.refresh_from_db()
        self.assertTrue(self.task.course_active)
        self.assertEqual(self.task.status, Task.Status.OPEN)

        response = self.client.post(
            url,
            data={"note": "Клиентът не беше на адреса."},
        )
        self.assertEqual(response.status_code, 302)
        self.task.refresh_from_db()
        self.assertFalse(self.task.course_active)
        self.assertEqual(self.task.course_note, "Клиентът не беше на адреса.")
        self.assertEqual(self.task.status, Task.Status.OPEN)

        response = self.client.get(reverse("tasks:list"))
        self.assertRedirects(response, reverse("tasks:course"))
        self.task.refresh_from_db()
        self.assertEqual(self.task.course_note, "Клиентът не беше на адреса.")

    def test_unmarked_open_course_task_rolls_forward_to_today(self):
        old_day = timezone.localdate() - timedelta(days=1)
        self.task.due_at = timezone.make_aware(datetime.combine(old_day, time(10)))
        self.task.save(update_fields=["due_at"])

        roll_over_open_tasks_to_today()

        self.task.refresh_from_db()
        self.assertEqual(timezone.localtime(self.task.due_at).date(), timezone.localdate())


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

    def test_default_sort_is_due_date(self):
        self.client.login(username="u1", password="pw")
        resp = self.client.get(reverse("tasks:list"))
        self.assertEqual(resp.status_code, 200)
        page = resp.context["page"]
        first = page.object_list[0]
        self.assertEqual(first.id, self.overdue.id)

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


class ReportsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="reports-admin",
            email="",
            password="pass12345",
        )
        self.client.login(username="reports-admin", password="pass12345")
        self.end_date = timezone.localdate()
        self.start_date = self.end_date - timedelta(days=6)

        self.completed_task = Task.objects.create(
            type=Task.Type.DELIVERY,
            title="Приключена задача от периода",
            customer_name="Клиент приключен",
            created_by=self.user,
            due_at=timezone.make_aware(datetime.combine(self.start_date, time(10))),
            done_at=timezone.make_aware(datetime.combine(self.end_date, time(12))),
            done_by=self.user,
            status=Task.Status.DONE,
        )
        self.open_task = Task.objects.create(
            type=Task.Type.PICKUP,
            title="Активна задача от периода",
            created_by=self.user,
            due_at=timezone.make_aware(datetime.combine(self.end_date, time(14))),
        )
        self.outside_task = Task.objects.create(
            type=Task.Type.DELIVERY,
            title="Задача извън периода",
            created_by=self.user,
            due_at=timezone.make_aware(
                datetime.combine(self.start_date - timedelta(days=1), time(10))
            ),
        )

    def test_selected_period_shows_individual_completed_and_open_tasks(self):
        response = self.client.get(
            reverse("tasks:reports"),
            data={
                "start_date": self.start_date.isoformat(),
                "end_date": self.end_date.isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.completed_task.title)
        self.assertContains(response, self.open_task.title)
        self.assertNotContains(response, self.outside_task.title)
        self.assertEqual(response.context["kpi"]["planned_period"], 2)
        self.assertEqual(response.context["kpi"]["done_period"], 1)
        self.assertEqual(
            {task.pk for task in response.context["report_tasks"]},
            {self.completed_task.pk, self.open_task.pk},
        )

    def test_reversed_date_range_is_normalized(self):
        response = self.client.get(
            reverse("tasks:reports"),
            data={
                "start_date": self.end_date.isoformat(),
                "end_date": self.start_date.isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["start_date"], self.start_date)
        self.assertEqual(response.context["end_date"], self.end_date)


class MerchantReadOnlyCourseTests(TestCase):
    def setUp(self):
        merchant_group, _ = Group.objects.get_or_create(name=authz.MERCHANT_GROUP)
        self.merchant = User.objects.create_user(
            username="course-merchant",
            password="pass12345",
            is_staff=True,
        )
        self.merchant.groups.add(merchant_group)
        self.client.login(username="course-merchant", password="pass12345")
        self.task = Task.objects.create(
            type=Task.Type.DELIVERY,
            title="Задача в курс за merchant",
            created_by=self.merchant,
            due_at=timezone.now(),
            course_active=True,
        )

    def test_merchant_can_view_course_without_edit_controls_or_reports(self):
        response = self.client.get(reverse("tasks:course"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.task.title)
        self.assertNotContains(response, "course-task-checkbox")
        self.assertNotContains(response, "Приключи избраните")
        self.assertNotContains(response, "Маркирай като незавършени")
        self.assertContains(response, reverse("tasks:course"))
        self.assertNotContains(response, reverse("tasks:reports"))
        self.assertFalse(response.context["can_edit_course"])
        self.assertFalse(response.context["can_view_reports"])

    def test_merchant_cannot_open_reports_or_edit_course_execution(self):
        reports_response = self.client.get(reverse("tasks:reports"))
        update_response = self.client.post(
            reverse("tasks:course_bulk_action"),
            data={"mode": "done", "task_ids": [str(self.task.pk)]},
        )

        self.assertEqual(reports_response.status_code, 403)
        self.assertEqual(update_response.status_code, 403)

    def test_staff_merchant_can_remove_task_from_course_from_task_list(self):
        recurring_template = RecurringTaskTemplate.objects.create(
            task_text="Постоянна задача извън task list",
            contact_address="София",
            due_time=time(10, 0),
            created_by=self.merchant,
        )
        recurring_task = Task.objects.create(
            type=Task.Type.PICKUP,
            title="Генерирана постоянна задача",
            created_by=self.merchant,
            due_at=timezone.now(),
            course_active=True,
            recurring_template=recurring_template,
        )
        default_list_url = reverse("tasks:list")
        response = self.client.get(default_list_url)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.task.title)
        self.assertNotContains(response, recurring_task.title)
        self.assertEqual(response.context["filters"]["assignment"], "unassigned")

        list_url = f"{default_list_url}?assignment=assigned"
        response = self.client.get(list_url)
        self.assertContains(response, "Премахни от курса")
        self.assertContains(response, self.task.title)
        self.assertNotContains(response, recurring_task.title)

        response = self.client.post(
            reverse("tasks:course_remove", args=[self.task.pk]),
            data={"next": list_url},
        )

        self.assertRedirects(response, list_url)
        self.task.refresh_from_db()
        self.assertFalse(self.task.course_active)

    def test_staff_merchant_can_remove_incomplete_task_from_task_list(self):
        self.task.course_active = False
        self.task.course_note = "Клиентът не беше на адреса."
        self.task.save(update_fields=["course_active", "course_note"])

        list_url = reverse("tasks:list")
        response = self.client.get(list_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.task.title)
        self.assertContains(response, "Премахни")

        response = self.client.post(reverse("tasks:delete", args=[self.task.pk]))

        self.assertRedirects(response, list_url)
        self.assertFalse(Task.objects.filter(pk=self.task.pk).exists())

    def test_staff_merchant_can_add_task_to_course_from_task_list(self):
        unassigned_task = Task.objects.create(
            type=Task.Type.PICKUP,
            title="Незададена задача за staff merchant",
            created_by=self.merchant,
            due_at=timezone.now(),
        )
        response = self.client.get(reverse("tasks:list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Добави в курса")

        response = self.client.post(
            reverse("tasks:course_add", args=[unassigned_task.pk]),
            data={"next": reverse("tasks:list")},
        )

        self.assertRedirects(response, reverse("tasks:list"))
        unassigned_task.refresh_from_db()
        self.assertTrue(unassigned_task.course_active)
