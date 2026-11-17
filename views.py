from __future__ import annotations

from datetime import datetime, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.db.models.functions import TruncDate

from . import authz
from .forms import CourseTaskIncompleteForm, RecurringTaskTemplateForm, TaskForm
from .models import CourierCourseItem, RecurringTaskTemplate, Task
from .services import generate_recurring_tasks_for_day, roll_over_open_tasks_to_today


def home_redirect(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        if authz.is_courier_only(request.user):
            return redirect("tasks:course")
        return redirect("tasks:list")
    return redirect("tasks:login")


def _can_add_task(user) -> bool:
    return authz.is_admin(user) or authz.is_courier(user) or authz.is_merchant(user)


def _can_manage_recurring(user) -> bool:
    return authz.is_admin(user) or authz.is_merchant(user)


@login_required
def task_list(request: HttpRequest) -> HttpResponse:
    if authz.is_courier_only(request.user):
        return redirect("tasks:course")

    roll_over_open_tasks_to_today()
    created_recurring = generate_recurring_tasks_for_day()
    if created_recurring:
        messages.info(request, f"Добавени постоянни задачи за днес: {created_recurring}.")

    qs = Task.objects.select_related("address", "created_by", "done_by")
    qs = qs.filter(recurring_template__isnull=True)

    status = request.GET.get("status") or Task.Status.OPEN
    if status == "all":
        status = "all"
    elif status in {Task.Status.OPEN, Task.Status.DONE}:
        qs = qs.filter(status=status)
    else:
        status = Task.Status.OPEN
        qs = qs.filter(status=Task.Status.OPEN)

    assignment = request.GET.get("assignment") or "unassigned"
    if assignment == "unassigned":
        qs = qs.filter(course_active=False, course_items__isnull=True)
    elif assignment == "assigned":
        qs = qs.filter(course_active=True)
    elif assignment != "all":
        assignment = "unassigned"
        qs = qs.filter(course_active=False, course_items__isnull=True)

    ttype = request.GET.get("type") or ""
    if ttype in {Task.Type.DELIVERY, Task.Type.PICKUP}:
        qs = qs.filter(type=ttype)

    sort = request.GET.get("sort") or "due"
    qs = qs.distinct()
    if sort == "due":
        qs = qs.order_by("due_at", "-created_at")
    elif sort == "due_desc":
        qs = qs.order_by("-due_at", "-created_at")
    else:
        sort = "due"
        qs = qs.order_by("due_at", "-created_at")

    paginator = Paginator(qs, 25)
    page = paginator.get_page(request.GET.get("page"))

    today = timezone.localdate()
    missed_count = Task.objects.filter(status=Task.Status.OPEN, due_at__date__lt=today).count()
    today_open_count = Task.objects.filter(status=Task.Status.OPEN, due_at__date=today).count()
    future_count = Task.objects.filter(status=Task.Status.OPEN, due_at__date__gt=today).count()

    return render(
        request,
        "tasks/list.html",
        {
            "page": page,
            "can_add_task": _can_add_task(request.user),
            "can_manage_recurring": _can_manage_recurring(request.user),
            "filters": {"status": status, "assignment": assignment, "type": ttype, "sort": sort},
            "Task": Task,
            "course_count": Task.objects.filter(
                status=Task.Status.OPEN,
                course_active=True,
                due_at__date=today,
            ).count(),
            "daily_summary": {
                "today": today,
                "missed_count": missed_count,
                "today_open_count": today_open_count,
                "future_count": future_count,
            },
        },
    )


@login_required
def task_create(request: HttpRequest) -> HttpResponse:
    if authz.is_courier_only(request.user):
        return redirect("tasks:course")
    if not _can_add_task(request.user):
        raise PermissionDenied

    if request.method == "POST":
        form = TaskForm(request.POST)
        if form.is_valid():
            task = form.save(commit=False)
            task.created_by = request.user
            task.save()
            messages.success(request, "Задачата е добавена.")
            return redirect("tasks:detail", pk=task.pk)
    else:
        form = TaskForm()

    return render(request, "tasks/new.html", {"form": form})


@login_required
def task_detail(request: HttpRequest, pk: int) -> HttpResponse:
    if authz.is_courier_only(request.user):
        task = get_object_or_404(
            Task.objects.select_related("address", "created_by", "done_by"),
            pk=pk,
            status=Task.Status.OPEN,
            course_active=True,
            due_at__date=timezone.localdate(),
        )
    else:
        task = get_object_or_404(
            Task.objects.select_related("address", "created_by", "done_by"),
            pk=pk,
        )
    return render(request, "tasks/detail.html", {"task": task, "Task": Task})


@login_required
def task_delete(request: HttpRequest, task_id: int) -> HttpResponse:
    if request.method != "POST":
        raise PermissionDenied
    if not (
        authz.is_admin(request.user)
        or authz.is_merchant_member(request.user)
    ):
        raise PermissionDenied

    task = get_object_or_404(
        Task,
        pk=task_id,
        status=Task.Status.OPEN,
        course_active=False,
        course_note__gt="",
    )
    task.delete()
    messages.success(request, "Незавършената задача е премахната.")
    return redirect("tasks:list")


@login_required
def recurring_list(request: HttpRequest) -> HttpResponse:
    if authz.is_courier_only(request.user):
        return redirect("tasks:course")
    if not _can_manage_recurring(request.user):
        raise PermissionDenied
    templates = RecurringTaskTemplate.objects.select_related("created_by").all()
    return render(request, "recurring/list.html", {"templates": templates})


@login_required
def recurring_create(request: HttpRequest) -> HttpResponse:
    if authz.is_courier_only(request.user):
        return redirect("tasks:course")
    if not _can_manage_recurring(request.user):
        raise PermissionDenied
    if request.method == "POST":
        form = RecurringTaskTemplateForm(request.POST)
        if form.is_valid():
            template = form.save(commit=False)
            template.created_by = request.user
            template.save()
            messages.success(request, "Постоянната задача е запазена.")
            return redirect("tasks:recurring_list")
    else:
        form = RecurringTaskTemplateForm()
    return render(request, "recurring/new.html", {"form": form})


@login_required
def course(request: HttpRequest) -> HttpResponse:
    if not (authz.can_edit_course(request.user) or authz.is_merchant(request.user)):
        raise PermissionDenied
    today = timezone.localdate()
    roll_over_open_tasks_to_today()
    generate_recurring_tasks_for_day(today)

    tasks = (
        Task.objects.filter(
            course_active=True,
            status=Task.Status.OPEN,
            due_at__date=today,
        )
        .select_related("address", "created_by", "done_by")
        .order_by("due_at", "-created_at")
    )
    return render(
        request,
        "course/index.html",
        {"tasks": tasks, "selected_date": today, "Task": Task},
    )


@login_required
def course_add(request: HttpRequest, task_id: int) -> HttpResponse:
    if request.method != "POST":
        raise PermissionDenied
    if not authz.can_add_to_course(request.user):
        raise PermissionDenied
    task = get_object_or_404(Task, pk=task_id)
    if task.status == Task.Status.DONE:
        messages.warning(request, "Приключена задача не може да се добави в курс.")
        return redirect(request.POST.get("next") or "tasks:list")
    task.course_active = True
    task.course_note = ""
    task.save(update_fields=["course_active", "course_note"])
    CourierCourseItem.objects.get_or_create(courier=request.user, task=task)
    messages.success(request, "Добавено в курса.")
    return redirect(request.POST.get("next") or "tasks:list")


@login_required
def course_remove(request: HttpRequest, task_id: int) -> HttpResponse:
    if request.method != "POST":
        raise PermissionDenied
    if not authz.can_add_to_course(request.user):
        raise PermissionDenied
    Task.objects.filter(pk=task_id, status=Task.Status.OPEN).update(course_active=False)
    CourierCourseItem.objects.filter(task_id=task_id).delete()
    messages.info(request, "Премахнато от курса.")
    return redirect(request.POST.get("next") or "tasks:list")


@login_required
def course_task_done(request: HttpRequest, task_id: int) -> HttpResponse:
    if request.method != "POST":
        raise PermissionDenied
    if not authz.can_edit_course(request.user):
        raise PermissionDenied

    selected_date = timezone.localdate()
    task = get_object_or_404(
        Task,
        pk=task_id,
        status=Task.Status.OPEN,
        course_active=True,
        due_at__date=selected_date,
    )
    task.mark_done(by_user=request.user)
    task.save(update_fields=["status", "done_at", "done_by"])
    CourierCourseItem.objects.filter(task=task).delete()
    messages.success(request, "Задачата е отбелязана като приключена.")
    return redirect("tasks:course")


@login_required
def course_task_incomplete(request: HttpRequest, task_id: int) -> HttpResponse:
    if request.method != "POST":
        raise PermissionDenied
    if not authz.can_edit_course(request.user):
        raise PermissionDenied

    selected_date = timezone.localdate()
    task = get_object_or_404(
        Task,
        pk=task_id,
        status=Task.Status.OPEN,
        course_active=True,
        due_at__date=selected_date,
    )
    form = CourseTaskIncompleteForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Въведете причина, за да преместите задачата в списъка със задачи.")
        return redirect("tasks:course")

    task.course_note = form.cleaned_data["note"].strip()
    task.course_active = False
    task.save(update_fields=["course_note", "course_active"])
    CourierCourseItem.objects.filter(task=task).delete()
    messages.info(request, "Задачата е преместена в списъка със задачи.")
    return redirect("tasks:course")


@login_required
def course_bulk_action(request: HttpRequest) -> HttpResponse:
    if request.method != "POST":
        raise PermissionDenied
    if not authz.can_edit_course(request.user):
        raise PermissionDenied

    selected_ids = {
        int(value)
        for value in request.POST.getlist("task_ids")
        if value.isdigit()
    }
    if not selected_ids:
        messages.warning(request, "Изберете поне една задача от курса.")
        return redirect("tasks:course")

    mode = request.POST.get("mode")
    if mode not in {"done", "incomplete"}:
        messages.error(request, "Изберете дали задачите са приключени или незавършени.")
        return redirect("tasks:course")

    note = ""
    if mode == "incomplete":
        form = CourseTaskIncompleteForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Въведете причина за незавършените задачи.")
            return redirect("tasks:course")
        note = form.cleaned_data["note"].strip()

    today = timezone.localdate()
    with transaction.atomic():
        tasks = list(
            Task.objects.select_for_update().filter(
                pk__in=selected_ids,
                status=Task.Status.OPEN,
                course_active=True,
                due_at__date=today,
            )
        )
        if not tasks:
            messages.warning(request, "Избраните задачи вече не са в днешния курс.")
            return redirect("tasks:course")

        if mode == "done":
            now = timezone.now()
            task_ids = [task.pk for task in tasks]
            Task.objects.filter(pk__in=task_ids).update(
                status=Task.Status.DONE,
                done_at=now,
                done_by=request.user,
            )
            CourierCourseItem.objects.filter(task_id__in=task_ids).delete()
            messages.success(request, f"Приключени задачи: {len(task_ids)}.")
        else:
            for task in tasks:
                task.course_active = False
                task.course_note = note
            Task.objects.bulk_update(tasks, ["course_active", "course_note"])
            CourierCourseItem.objects.filter(task__in=tasks).delete()
            messages.info(request, f"Преместени в списъка със задачи: {len(tasks)}.")

    return redirect("tasks:course")


@login_required
def course_bulk_done(request: HttpRequest) -> HttpResponse:
    if request.method != "POST":
        raise PermissionDenied
    if not authz.can_edit_course(request.user):
        raise PermissionDenied

    mode = request.POST.get("mode") or "finish_selected"
    selected_ids = [int(x) for x in request.POST.getlist("task_ids") if str(x).isdigit()]

    with transaction.atomic():
        course_items = CourierCourseItem.objects.select_for_update().filter(courier=request.user).select_related("task")
        open_items = [item for item in course_items if item.task.status == Task.Status.OPEN]

        if mode == "finish_all":
            done_ids = [item.task_id for item in open_items]
            carry_ids = []
        elif mode == "finish_except_not_ready":
            carry_ids = [item.task_id for item in open_items if item.task_id in selected_ids]
            done_ids = [item.task_id for item in open_items if item.task_id not in selected_ids]
        else:
            if not selected_ids:
                messages.warning(request, "Не са избрани задачи.")
                return redirect("tasks:course")
            allowed_ids = {item.task_id for item in open_items}
            done_ids = [task_id for task_id in selected_ids if task_id in allowed_ids]
            carry_ids = []

        now = timezone.now()
        if done_ids:
            Task.objects.filter(pk__in=done_ids, status=Task.Status.OPEN).update(
                status=Task.Status.DONE, done_at=now, done_by=request.user
            )
            CourierCourseItem.objects.filter(courier=request.user, task_id__in=done_ids).delete()

        if carry_ids:
            for task in Task.objects.select_for_update().filter(pk__in=carry_ids, status=Task.Status.OPEN):
                task.move_to_next_day()
                task.save(update_fields=["due_at"])

    if mode == "finish_except_not_ready":
        messages.success(request, f"Приключени: {len(done_ids)}. Прехвърлени за следващ ден: {len(carry_ids)}.")
    else:
        messages.success(request, f"Приключени: {len(done_ids)}.")
    return redirect("tasks:course")


@login_required
def reports(request: HttpRequest) -> HttpResponse:
    if authz.is_courier_only(request.user):
        return redirect("tasks:course")
    if not authz.can_view_reports(request.user):
        raise PermissionDenied
    generate_recurring_tasks_for_day()

    today = timezone.localdate()
    default_start = today - timedelta(days=29)

    def parse_date(value, fallback):
        try:
            return datetime.strptime(value, "%Y-%m-%d").date() if value else fallback
        except ValueError:
            return fallback

    start_date = parse_date(request.GET.get("start_date", ""), default_start)
    end_date = parse_date(request.GET.get("end_date", ""), today)
    if start_date > end_date:
        start_date, end_date = end_date, start_date
        messages.warning(request, "Началната и крайната дата бяха разменени.")

    base = Task.objects.all()
    open_qs = base.filter(status=Task.Status.OPEN)
    done_qs = base.filter(status=Task.Status.DONE)
    due_in_range = base.filter(due_at__date__gte=start_date, due_at__date__lte=end_date)
    open_due_in_range = open_qs.filter(due_at__date__gte=start_date, due_at__date__lte=end_date)
    missed_before_range = open_qs.filter(due_at__date__lt=start_date)
    workload_by_end = open_qs.filter(due_at__date__lte=end_date)
    done_in_range = done_qs.filter(done_at__date__gte=start_date, done_at__date__lte=end_date)

    kpi = {
        "planned_period": due_in_range.count(),
        "open_period": open_due_in_range.count(),
        "missed_before_period": missed_before_range.count(),
        "workload_by_end": workload_by_end.count(),
        "done_period": done_in_range.count(),
        "created_period": base.filter(
            created_at__date__gte=start_date,
            created_at__date__lte=end_date,
        ).count(),
    }

    by_day = (
        base.filter(due_at__date__gte=start_date, due_at__date__lte=end_date)
        .annotate(day=TruncDate("due_at"))
        .values("day")
        .annotate(c=Count("id"))
        .order_by("day")
    )
    chart_days = [str(r["day"]) for r in by_day]
    chart_counts = [r["c"] for r in by_day]
    report_tasks = (
        base.filter(
            Q(due_at__date__gte=start_date, due_at__date__lte=end_date)
            | Q(done_at__date__gte=start_date, done_at__date__lte=end_date)
        )
        .select_related("address", "created_by", "done_by")
        .distinct()
        .order_by("-done_at", "due_at", "-created_at")
    )

    return render(
        request,
        "reports/index.html",
        {
            "kpi": kpi,
            "start_date": start_date,
            "end_date": end_date,
            "report_tasks": report_tasks,
            "Task": Task,
            "chart_days": chart_days,
            "chart_counts": chart_counts,
        },
    )
