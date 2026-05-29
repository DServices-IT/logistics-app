from __future__ import annotations

import random
from datetime import datetime, time, timedelta

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from logistics import authz
from logistics.models import Address, CourierCourseItem, RecurringTaskTemplate, Task


DEMO_PASSWORD = "password"
DEMO_USERNAMES = ("demo-admin", "demo-merchant", "demo-courier", "demo-courier-2")
DAYS_TO_SEED = 30
MIN_TASKS_PER_BUSINESS_DAY = 15
MAX_OVERDUE_CARRYOVER_TASKS = 6
RANDOM_SEED = 20260528
WEEKDAY_TASK_RANGES = {
    0: (24, 31),  # Monday is heavy after the weekend.
    1: (16, 22),
    2: (15, 20),
    3: (17, 23),
    4: (25, 33),  # Friday is heavy before the weekend.
}


class Command(BaseCommand):
    help = "Load deterministic demo users, addresses, recurring templates and tasks for the last month."

    def add_arguments(self, parser):
        parser.add_argument(
            "--keep-existing",
            action="store_true",
            help="Do not remove previously generated demo tasks/templates before seeding.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        call_command("bootstrap_roles", verbosity=0)

        users = self._ensure_users()

        if not options["keep_existing"]:
            self._clear_demo_data()

        addresses = self._ensure_addresses()
        templates = self._create_recurring_templates(users["merchant"])
        tasks = self._create_tasks(users["merchant"], users["courier"], addresses, templates)
        course_items = self._create_course_items(users["courier"], users["courier_2"], tasks)

        self.stdout.write(
            self.style.SUCCESS(
                "Demo data loaded: "
                f"{len(addresses)} addresses, {len(templates)} recurring templates, "
                f"{len(tasks)} tasks, {course_items} course items. "
                f"Demo password: {DEMO_PASSWORD}"
            )
        )

    def _ensure_users(self) -> dict[str, User]:
        courier_group = Group.objects.get(name=authz.COURIER_GROUP)
        merchant_group = Group.objects.get(name=authz.MERCHANT_GROUP)

        admin = self._ensure_user(
            "demo-admin",
            first_name="Demo",
            last_name="Admin",
            email="demo-admin@example.test",
            is_staff=True,
            is_superuser=True,
        )
        merchant = self._ensure_user(
            "demo-merchant",
            first_name="Demo",
            last_name="Merchant",
            email="demo-merchant@example.test",
            is_staff=False,
            is_superuser=False,
        )
        courier = self._ensure_user(
            "demo-courier",
            first_name="Demo",
            last_name="Courier",
            email="demo-courier@example.test",
            is_staff=False,
            is_superuser=False,
        )
        courier_2 = self._ensure_user(
            "demo-courier-2",
            first_name="Demo",
            last_name="Courier 2",
            email="demo-courier-2@example.test",
            is_staff=False,
            is_superuser=False,
        )

        merchant.groups.add(merchant_group)
        courier.groups.add(courier_group)
        courier_2.groups.add(courier_group)

        return {
            "admin": admin,
            "merchant": merchant,
            "courier": courier,
            "courier_2": courier_2,
        }

    def _ensure_user(self, username: str, **defaults) -> User:
        user, _ = User.objects.update_or_create(username=username, defaults=defaults)
        user.set_password(DEMO_PASSWORD)
        user.save()
        return user

    def _clear_demo_data(self) -> None:
        Task.objects.filter(created_by__username__in=DEMO_USERNAMES).delete()
        RecurringTaskTemplate.objects.filter(created_by__username__in=DEMO_USERNAMES).delete()

    def _ensure_addresses(self) -> list[Address]:
        address_rows = [
            ("Демо: Централен офис", "София, бул. България 1\nРецепция: 0888 100 001"),
            ("Демо: Склад Изток", "София, бул. Цариградско шосе 115\nРампа 3, 0888 100 002"),
            ("Демо: Склад Запад", "София, бул. Сливница 188\nОхрана: 0888 100 003"),
            ("Демо: Клиент Младост", "София, ж.к. Младост 4, бл. 420\nИван Петров: 0888 100 004"),
            ("Демо: Клиент Лозенец", "София, ул. Крум Попов 12\nМария Георгиева: 0888 100 005"),
            ("Демо: Клиент Надежда", "София, ж.к. Надежда 2, бл. 221\nПетър Иванов: 0888 100 006"),
            ("Демо: Клиент Оборище", "София, ул. Оборище 35\nАнна Стоянова: 0888 100 007"),
            ("Демо: Клиент Изгрев", "София, ул. Тинтява 15\nГеорги Димитров: 0888 100 008"),
            ("Демо: Клиент Люлин", "София, ж.к. Люлин 7, бл. 711\nНиколай Николов: 0888 100 009"),
            ("Демо: Клиент Дружба", "София, ж.к. Дружба 1, бл. 28\nЕлена Маринова: 0888 100 010"),
            ("Демо: Клиент Хаджи Димитър", "София, ул. Макгахан 42\nВиктор Христов: 0888 100 011"),
            ("Демо: Клиент Красно село", "София, бул. Цар Борис III 87\nДаниела Тодорова: 0888 100 012"),
            ("Демо: Клиент Манастирски ливади", "София, ул. Ралевица 96\nСимеон Василев: 0888 100 013"),
            ("Демо: Клиент Бизнес парк", "София, Бизнес парк, сграда 7\nРецепция: 0888 100 014"),
            ("Демо: Клиент Сердика", "София, бул. Ситняково 48\nСклад прием: 0888 100 015"),
            ("Демо: Клиент Витоша", "София, ул. Йордан Радичков 8\nКалина Петрова: 0888 100 016"),
        ]
        addresses = []
        for name, address_text in address_rows:
            address, _ = Address.objects.update_or_create(
                name=name,
                defaults={"address_text": address_text, "is_active": True},
            )
            addresses.append(address)
        return addresses

    def _create_recurring_templates(self, merchant: User) -> list[RecurringTaskTemplate]:
        rows = [
            {
                "task_text": "Демо: Ежедневно вземане на документи от Централен офис",
                "contact_address": "София, бул. България 1\nРецепция: 0888 100 001",
                "note": "Питай за готовия плик на рецепция.",
                "type": Task.Type.PICKUP,
                "urgency": Task.Urgency.NORMAL,
                "frequency": RecurringTaskTemplate.Frequency.DAILY,
                "weekday": None,
                "due_time": time(11, 30),
                "created_by": merchant,
            },
            {
                "task_text": "Демо: Ежедневно връщане на складови документи",
                "contact_address": "София, бул. Сливница 188\nОхрана: 0888 100 003",
                "note": "Връща се в края на деня, ако има готов пакет.",
                "type": Task.Type.DELIVERY,
                "urgency": Task.Urgency.NORMAL,
                "frequency": RecurringTaskTemplate.Frequency.DAILY,
                "weekday": None,
                "due_time": time(16, 30),
                "created_by": merchant,
            },
            {
                "task_text": "Демо: Седмична доставка към Склад Изток",
                "contact_address": "София, бул. Цариградско шосе 115\nРампа 3",
                "note": "Само в понеделник, до обяд.",
                "type": Task.Type.DELIVERY,
                "urgency": Task.Urgency.HIGH,
                "frequency": RecurringTaskTemplate.Frequency.WEEKLY,
                "weekday": 0,
                "due_time": time(12, 0),
                "created_by": merchant,
            },
            {
                "task_text": "Демо: Петъчно събиране на фактури от ключови клиенти",
                "contact_address": "София, Бизнес парк, сграда 7\nРецепция: 0888 100 014",
                "note": "Преди края на работната седмица.",
                "type": Task.Type.PICKUP,
                "urgency": Task.Urgency.HIGH,
                "frequency": RecurringTaskTemplate.Frequency.WEEKLY,
                "weekday": 4,
                "due_time": time(15, 30),
                "created_by": merchant,
            },
        ]
        return [RecurringTaskTemplate.objects.create(**row) for row in rows]

    def _create_tasks(
        self,
        merchant: User,
        courier: User,
        addresses: list[Address],
        templates: list[RecurringTaskTemplate],
    ) -> list[Task]:
        today = timezone.localdate()
        start_day = today - timedelta(days=DAYS_TO_SEED - 1)
        business_days = self._business_days(start_day, today)
        rng = random.Random(RANDOM_SEED)
        titles = [
            "Доставка на договори",
            "Получаване на подписани документи",
            "Доставка на резервни части",
            "Получаване на фактури",
            "Доставка на мостри",
            "Получаване на върнати стоки",
            "Доставка на офис консумативи",
            "Получаване на сервизни протоколи",
            "Доставка на клиентски пакети",
            "Получаване на касови документи",
            "Доставка на маркетинг материали",
            "Получаване на архивни кашони",
            "Доставка на гаранционни карти",
            "Получаване на складови разписки",
            "Доставка на мострена пратка",
        ]
        customers = [
            "София Трейд",
            "Балкан Сервиз",
            "Витоша Комерс",
            "Логистик Партнер",
            "Медика Про",
            "Техно Груп",
            "Офис Маркет",
            "Делта Фуудс",
            "Сити Ритейл",
            "Еко Фарм",
        ]
        notes = [
            "Обади се 15 минути по-рано.",
            "Входът е откъм служебния паркинг.",
            "Остави при охрана, ако контактът отсъства.",
            "Нужен е подпис на приемо-предавателен протокол.",
            "Пратката е малка, но документите са спешни.",
            "Провери дали има върната опаковка.",
        ]
        tasks: list[Task] = []
        overdue_carryover_created = 0
        overdue_window_start = today - timedelta(days=3)

        for day_index, day in enumerate(business_days):
            range_start, range_end = WEEKDAY_TASK_RANGES[day.weekday()]
            target_count = max(MIN_TASKS_PER_BUSINESS_DAY, rng.randint(range_start, range_end))
            daily_tasks: list[Task] = []

            for template in [template for template in templates if template.applies_on(day)]:
                due_at = timezone.make_aware(datetime.combine(day, template.due_time))
                created_at = due_at - timedelta(hours=rng.randint(1, 3), minutes=rng.choice([0, 15, 30]))
                status = Task.Status.OPEN if day == today and rng.random() < 0.45 else Task.Status.DONE
                daily_tasks.append(
                    self._create_task(
                        task_type=template.type,
                        title=f"Демо: {template.task_text.removeprefix('Демо: ').strip()}",
                        description=f"{template.task_text}\nПовтаряща се задача от шаблон.",
                        customer_name="Демо регулярна логистика",
                        phone="0888 40 000",
                        address=addresses[(day_index + len(daily_tasks)) % len(addresses)],
                        location_note=template.note,
                        created_by=merchant,
                        created_at=created_at,
                        due_at=due_at,
                        urgency=template.urgency,
                        status=status,
                        done_by=courier if status == Task.Status.DONE else None,
                        done_at=due_at + timedelta(minutes=rng.randint(30, 120)) if status == Task.Status.DONE else None,
                        recurring_template=template,
                    )
                )

            for slot in range(target_count - len(daily_tasks)):
                title = rng.choice(titles)
                address = rng.choice(addresses)
                due_at = timezone.make_aware(
                    datetime.combine(
                        day,
                        time(rng.randint(9, 17), rng.choice([0, 15, 30, 45])),
                    )
                )
                created_at = due_at - timedelta(
                    hours=rng.randint(1, 5),
                    minutes=rng.choice([0, 10, 20, 30, 40, 50]),
                )
                delivery_weight = 4 if day.weekday() in {0, 4} else 3
                task_type = rng.choices(
                    [Task.Type.DELIVERY, Task.Type.PICKUP],
                    weights=[delivery_weight, 2],
                    k=1,
                )[0]
                urgency = rng.choices(
                    [Task.Urgency.LOW, Task.Urgency.NORMAL, Task.Urgency.HIGH, Task.Urgency.URGENT],
                    weights=[2, 7, 4, 1],
                    k=1,
                )[0]
                status = Task.Status.OPEN if day == today and rng.random() < 0.35 else Task.Status.DONE

                daily_tasks.append(
                    self._create_task(
                        task_type=task_type,
                        title=f"Демо: {title}",
                        description=(
                            f"{title} за {rng.choice(customers)} на дата {day:%Y-%m-%d}.\n"
                            "Генерирана примерна задача със случаен дневен профил."
                        ),
                        customer_name=f"Демо {rng.choice(customers)}",
                        phone=f"0888 {rng.randint(200000, 899999)}",
                        address=address,
                        location_note=rng.choice(notes),
                        created_by=merchant,
                        created_at=created_at,
                        due_at=due_at,
                        urgency=urgency,
                        status=status,
                        done_by=courier if status == Task.Status.DONE else None,
                        done_at=due_at + timedelta(minutes=rng.randint(30, 180)) if status == Task.Status.DONE else None,
                        recurring_template=None,
                    )
                )

            tasks.extend(daily_tasks)

            next_business_day = self._next_business_day(day)
            if next_business_day >= today or next_business_day < overdue_window_start:
                continue

            carryover_count = min(
                rng.randint(1, 2),
                MAX_OVERDUE_CARRYOVER_TASKS - overdue_carryover_created,
            )
            if carryover_count <= 0:
                continue

            for carry_index in range(carryover_count):
                address = rng.choice(addresses)
                due_at = timezone.make_aware(
                    datetime.combine(next_business_day, time(10 + carry_index * 3, rng.choice([0, 30])))
                )
                created_at = timezone.make_aware(
                    datetime.combine(day, time(16 + carry_index, rng.choice([10, 25, 40])))
                )
                tasks.append(
                    self._create_task(
                        task_type=Task.Type.DELIVERY if carry_index == 0 else Task.Type.PICKUP,
                        title=f"Демо: Изоставаща задача от {day:%Y-%m-%d}",
                        description=(
                            f"Задача, започната на {day:%Y-%m-%d}, но оставена за следващ работен ден.\n"
                            "Целта е да се виждат просрочени и прехвърлени доставки в интерфейса."
                        ),
                        customer_name=f"Демо {rng.choice(customers)}",
                        phone=f"0888 {rng.randint(900000, 999999)}",
                        address=address,
                        location_note="Изостава от предишен работен ден; провери дали се показва като отворена.",
                        created_by=merchant,
                        created_at=created_at,
                        due_at=due_at,
                        urgency=Task.Urgency.HIGH if carry_index == 0 else Task.Urgency.URGENT,
                        status=Task.Status.OPEN,
                        done_by=None,
                        done_at=None,
                        recurring_template=None,
                    )
                )
                overdue_carryover_created += 1

        return tasks

    def _business_days(self, start_day, end_day):
        days = []
        day = start_day
        while day <= end_day:
            if day.weekday() < 5:
                days.append(day)
            day += timedelta(days=1)
        return days

    def _next_business_day(self, day):
        next_day = day + timedelta(days=1)
        while next_day.weekday() >= 5:
            next_day += timedelta(days=1)
        return next_day

    def _create_task(
        self,
        *,
        task_type,
        title: str,
        description: str,
        customer_name: str,
        phone: str,
        address: Address,
        location_note: str,
        created_by: User,
        created_at,
        due_at,
        urgency,
        status,
        done_by: User | None,
        done_at,
        recurring_template: RecurringTaskTemplate | None,
    ) -> Task:
        task = Task.objects.create(
            type=task_type,
            title=title,
            description=description,
            customer_name=customer_name,
            phone=phone,
            address=address,
            address_text=address.address_text,
            location_note=location_note[:240],
            created_by=created_by,
            due_at=due_at,
            urgency=urgency,
            status=status,
            done_at=done_at,
            done_by=done_by,
            recurring_template=recurring_template,
        )
        Task.objects.filter(pk=task.pk).update(created_at=created_at)
        task.created_at = created_at
        return task

    def _create_course_items(self, courier: User, courier_2: User, tasks: list[Task]) -> int:
        open_tasks = [task for task in tasks if task.status == Task.Status.OPEN]
        course_items = [
            CourierCourseItem(courier=courier, task=task)
            for task in open_tasks[:4]
        ]
        course_items.extend(
            CourierCourseItem(courier=courier_2, task=task)
            for task in open_tasks[4:7]
        )
        CourierCourseItem.objects.bulk_create(course_items, ignore_conflicts=True)
        return len(course_items)
