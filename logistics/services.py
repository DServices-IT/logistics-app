from __future__ import annotations

from datetime import datetime, time

from django.db import transaction
from django.utils import timezone

from .models import RecurringTaskTemplate, Task


NOON_HOUR = 12
END_OF_DAY_HOUR = 18


def make_due_at(day, slot: str):
    hour = NOON_HOUR if slot == "noon" else END_OF_DAY_HOUR
    return timezone.make_aware(datetime.combine(day, time.min)).replace(
        hour=hour, minute=0, second=0, microsecond=0
    )


def make_due_at_time(day, due_time):
    return timezone.make_aware(datetime.combine(day, due_time))


def title_from_text(text: str, fallback: str = "Задача") -> str:
    first_line = (text or "").strip().splitlines()[0:1]
    title = first_line[0].strip() if first_line else fallback
    return title[:200] or fallback


def generate_recurring_tasks_for_day(day=None) -> int:
    """Create due recurring tasks once, unless a previous task from the template is still open."""
    day = day or timezone.localdate()
    created = 0
    templates = RecurringTaskTemplate.objects.select_related("created_by").filter(is_active=True)

    with transaction.atomic():
        for template in templates:
            if not template.applies_on(day):
                continue
            if Task.objects.filter(recurring_template=template, status=Task.Status.OPEN).exists():
                continue
            if Task.objects.filter(recurring_template=template, due_at__date=day).exists():
                continue

            Task.objects.create(
                type=template.type,
                title=title_from_text(template.task_text),
                description=template.task_text,
                address_text=template.contact_address,
                location_note=template.note[:240],
                created_by=template.created_by,
                due_at=make_due_at_time(day, template.due_time),
                urgency=template.urgency,
                recurring_template=template,
            )
            created += 1
    return created


def roll_over_open_tasks_to_today() -> int:
    """Move unfinished tasks from previous days to today, preserving noon/eod time."""
    today = timezone.localdate()
    moved = 0
    with transaction.atomic():
        tasks = Task.objects.select_for_update().filter(status=Task.Status.OPEN, due_at__date__lt=today)
        for task in tasks:
            local_due = timezone.localtime(task.due_at)
            task.due_at = timezone.make_aware(
                datetime.combine(today, local_due.time())
            )
            task.save(update_fields=["due_at"])
            moved += 1
    return moved

