from __future__ import annotations

from datetime import datetime, timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


class Address(models.Model):
    name = models.CharField(max_length=120, unique=True)
    address_text = models.TextField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Task(models.Model):
    class Type(models.TextChoices):
        DELIVERY = "delivery", "Доставка"
        PICKUP = "pickup", "Получаване"

    class Urgency(models.IntegerChoices):
        LOW = 10, "Ниска"
        NORMAL = 20, "Нормална"
        HIGH = 30, "Висока"
        URGENT = 40, "Спешна"

    class Status(models.TextChoices):
        OPEN = "open", "Активна"
        DONE = "done", "Приключена"

    type = models.CharField(max_length=16, choices=Type.choices)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)

    customer_name = models.CharField(max_length=200, blank=True)
    phone = models.CharField(max_length=50, blank=True)

    address = models.ForeignKey(Address, null=True, blank=True, on_delete=models.PROTECT)
    address_text = models.TextField(blank=True)
    location_note = models.CharField(max_length=240, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_tasks"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    due_at = models.DateTimeField()

    urgency = models.IntegerField(choices=Urgency.choices, default=Urgency.NORMAL)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.OPEN)

    done_at = models.DateTimeField(null=True, blank=True)
    done_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="done_tasks",
    )
    recurring_template = models.ForeignKey(
        "RecurringTaskTemplate",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="generated_tasks",
    )

    class Meta:
        ordering = ["-urgency", "due_at", "-created_at"]
        indexes = [
            models.Index(fields=["status", "due_at"]),
            models.Index(fields=["urgency", "due_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_type_display()}: {self.title}"

    @property
    def is_overdue(self) -> bool:
        return self.status != Task.Status.DONE and self.due_at < timezone.now()

    @property
    def priority_score(self) -> int:
        # Stable sort key for UI: urgency first, then overdue boost.
        score = int(self.urgency)
        if self.is_overdue:
            score += 100
        return score

    def clean(self):
        # Ensure we always keep a snapshot address_text for reporting/history.
        if self.address and not self.address_text:
            self.address_text = self.address.address_text

    def mark_done(self, *, by_user) -> None:
        self.status = Task.Status.DONE
        self.done_at = timezone.now()
        self.done_by = by_user

    def move_to_next_day(self) -> None:
        local_due = timezone.localtime(self.due_at)
        next_day = timezone.localdate() + timedelta(days=1)
        self.due_at = timezone.make_aware(
            datetime.combine(next_day, local_due.time())
        )


class CourierCourseItem(models.Model):
    courier = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="course_items"
    )
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="course_items")
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["courier", "task"], name="uniq_course_task")]
        ordering = ["-added_at"]

    def __str__(self) -> str:
        return f"{self.courier} -> {self.task}"


class RecurringTaskTemplate(models.Model):
    class Frequency(models.TextChoices):
        DAILY = "daily", "Всеки ден"
        WEEKLY = "weekly", "Всяка седмица"

    WEEKDAY_CHOICES = [
        (0, "Понеделник"),
        (1, "Вторник"),
        (2, "Сряда"),
        (3, "Четвъртък"),
        (4, "Петък"),
        (5, "Събота"),
        (6, "Неделя"),
    ]

    task_text = models.TextField()
    contact_address = models.TextField()
    note = models.TextField(blank=True)
    type = models.CharField(max_length=16, choices=Task.Type.choices, default=Task.Type.PICKUP)
    urgency = models.IntegerField(choices=Task.Urgency.choices, default=Task.Urgency.NORMAL)
    frequency = models.CharField(max_length=16, choices=Frequency.choices, default=Frequency.DAILY)
    weekday = models.IntegerField(choices=WEEKDAY_CHOICES, null=True, blank=True)
    due_time = models.TimeField()
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="recurring_task_templates"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["frequency", "weekday", "due_time", "task_text"]

    def __str__(self) -> str:
        return self.task_text.splitlines()[0][:80]

    def applies_on(self, day) -> bool:
        if not self.is_active:
            return False
        if self.frequency == self.Frequency.DAILY:
            return True
        return self.weekday == day.weekday()
