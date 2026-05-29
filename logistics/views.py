from __future__ import annotations

from datetime import datetime, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Case, Count, Exists, IntegerField, OuterRef, Q, Value, When
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.db.models.functions import TruncDate

from . import authz
from .forms import RecurringTaskTemplateForm, TaskForm
from .models import CourierCourseItem, RecurringTaskTemplate, Task
from .services import generate_recurring_tasks_for_day


def home_redirect(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("tasks:list")
    return redirect("tasks:login")


def _can_add_task(user) -> bool:
    return authz.is_admin(user) or authz.is_courier(user) or authz.is_merchant(user)


def _can_manage_recurring(user) -> bool:
    return authz.is_admin(user) or authz.is_merchant(user)


def _available_course_tasks():
    assigned = CourierCourseItem.objects.filter(task_id=OuterRef("pk"))
    return (
        Task.objects.filter(status=Task.Status.OPEN)
        .annotate(is_assigned=Exists(assigned))
        .filter(is_assigned=False)
    )


SORT_OPTIONS = {
    "priority": ("-urgency", "due_at", "-created_at"),
    "id": ("id",),
    "id_desc": ("-id",),
    "title": ("title", "due_at"),
    "title_desc": ("-title", "due_at"),
    "address": ("address__name", "address_text", "due_at"),
    "address_desc": ("-address__name", "-address_text", "due_at"),
    "due": ("due_at", "-urgency", "-created_at"),
    "due_desc": ("-due_at", "-urgency", "-created_at"),
    "urgency": ("-urgency", "due_at", "-created_at"),
    "urgency_asc": ("urgency", "due_at", "-created_at"),
    "status": ("status", "due_at"),
    "status_desc": ("-status", "due_at"),
}


def _sort_link(query_params, current_sort: str, asc_sort: str, desc_sort: str) -> dict[str, str | bool]:
    next_sort = desc_sort if current_sort == asc_sort else asc_sort
    params = query_params.copy()
    params["sort"] = next_sort
    params.pop("page", None)
    return {
        "url": f"?{params.urlencode()}",
        "active": current_sort in {asc_sort, desc_sort},
        "desc": current_sort == desc_sort,
    }


@login_required
def task_list(request: HttpRequest) -> HttpResponse:
    created_recurring = generate_recurring_tasks_for_day()
    if created_recurring:
        messages.info(request, f"Добавени постоянни задачи за днес: {created_recurring}.")

    qs = Task.objects.select_related("address", "created_by", "done_by")

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
        qs = qs.filter(course_items__isnull=True)
    elif assignment == "assigned":
        qs = qs.filter(course_items__isnull=False)
    elif assignment != "all":
        assignment = "unassigned"
        qs = qs.filter(course_items__isnull=True)

    ttype = request.GET.get("type") or ""
    if ttype in {Task.Type.DELIVERY, Task.Type.PICKUP}:
        qs = qs.filter(type=ttype)

    urgency = request.GET.get("urgency") or ""
    if urgency.isdigit():
        qs = qs.filter(urgency=int(urgency))

    query = (request.GET.get("q") or "").strip()
    if query:
        qs = qs.filter(
            Q(title__icontains=query)
            | Q(description__icontains=query)
            | Q(customer_name__icontains=query)
            | Q(phone__icontains=query)
            | Q(address_text__icontains=query)
            | Q(location_note__icontains=query)
            | Q(address__name__icontains=query)
        )

    sort = request.GET.get("sort") or "priority"
    qs = qs.distinct().annotate(
        overdue_boost=Case(
            When(status=Task.Status.OPEN, due_at__lt=timezone.now(), then=Value(100)),
            default=Value(0),
            output_field=IntegerField(),
        )
    )
    if sort not in SORT_OPTIONS:
        sort = "priority"
    qs = qs.order_by(*SORT_OPTIONS[sort])

    paginator = Paginator(qs, 25)
    page = paginator.get_page(request.GET.get("page"))

    in_course_ids: set[int] = set()
    if authz.is_courier(request.user):
        in_course_ids = set(
            CourierCourseItem.objects.filter(courier=request.user).values_list("task_id", flat=True)
        )

    today = timezone.localdate()
    missed_count = Task.objects.filter(status=Task.Status.OPEN, due_at__date__lt=today).count()
    today_open_count = Task.objects.filter(status=Task.Status.OPEN, due_at__date=today).count()
    future_count = Task.objects.filter(status=Task.Status.OPEN, due_at__date__gt=today).count()

    query_params = request.GET.copy()
    query_params.pop("page", None)
    page_query = query_params.urlencode()
    sort_links = {
        "id": _sort_link(query_params, sort, "id", "id_desc"),
        "title": _sort_link(query_params, sort, "title", "title_desc"),
        "address": _sort_link(query_params, sort, "address", "address_desc"),
        "due": _sort_link(query_params, sort, "due", "due_desc"),
        "urgency": _sort_link(query_params, sort, "urgency_asc", "urgency"),
        "status": _sort_link(query_params, sort, "status", "status_desc"),
    }

    return render(
        request,
        "tasks/list.html",
        {
            "page": page,
            "can_add_task": _can_add_task(request.user),
            "can_manage_recurring": _can_manage_recurring(request.user),
            "filters": {
                "status": status,
                "assignment": assignment,
                "type": ttype,
                "urgency": urgency,
                "sort": sort,
                "q": query,
            },
            "sort_links": sort_links,
            "page_query": page_query,
            "Task": Task,
            "in_course_ids": in_course_ids,
            "course_count": len(in_course_ids),
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
    if not _can_add_task(request.user):
        raise PermissionDenied

    if request.method == "POST":
        form = TaskForm(request.POST)
        if form.is_valid():
            task = form.save(commit=False)
            task.created_by = request.user
            task.save()
            form.save_m2m()
            messages.success(request, "Задачата е добавена.")
            return redirect("tasks:detail", pk=task.pk)
    else:
        form = TaskForm()

    return render(request, "tasks/new.html", {"form": form})


@login_required
def task_detail(request: HttpRequest, pk: int) -> HttpResponse:
    task = get_object_or_404(Task.objects.select_related("address", "created_by", "done_by"), pk=pk)
    return render(request, "tasks/detail.html", {"task": task})


@login_required
def recurring_list(request: HttpRequest) -> HttpResponse:
    if not _can_manage_recurring(request.user):
        raise PermissionDenied
    templates = RecurringTaskTemplate.objects.select_related("created_by").all()
    return render(request, "recurring/list.html", {"templates": templates})


@login_required
def recurring_create(request: HttpRequest) -> HttpResponse:
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
    if not authz.is_courier(request.user):
        raise PermissionDenied
    items = (
        CourierCourseItem.objects.filter(courier=request.user)
        .select_related("task", "task__address", "task__created_by")
        .order_by("-added_at")
    )
    return render(request, "course/index.html", {"items": items, "Task": Task})


@login_required
def course_add(request: HttpRequest, task_id: int) -> HttpResponse:
    if request.method != "POST":
        raise PermissionDenied
    if not authz.is_courier(request.user):
        raise PermissionDenied
    task = get_object_or_404(_available_course_tasks(), pk=task_id)
    _, created = CourierCourseItem.objects.get_or_create(courier=request.user, task=task)
    if created:
        messages.success(request, "Добавено в курса.")
    else:
        messages.info(request, "Задачата вече е в курса.")
    return redirect(request.POST.get("next") or "tasks:list")


@login_required
def course_add_all(request: HttpRequest) -> HttpResponse:
    if request.method != "POST":
        raise PermissionDenied
    if not authz.is_courier(request.user):
        raise PermissionDenied

    with transaction.atomic():
        task_ids = list(
            _available_course_tasks()
            .select_for_update()
            .order_by("-urgency", "due_at", "-created_at")
            .values_list("id", flat=True)
        )
        CourierCourseItem.objects.bulk_create(
            [CourierCourseItem(courier=request.user, task_id=task_id) for task_id in task_ids],
            ignore_conflicts=True,
        )

    if task_ids:
        messages.success(request, f"Взети задачи в курса: {len(task_ids)}.")
        return redirect("tasks:course")
    messages.info(request, "Няма свободни активни задачи за вземане.")
    return redirect(request.POST.get("next") or "tasks:list")


@login_required
def course_remove(request: HttpRequest, task_id: int) -> HttpResponse:
    if request.method != "POST":
        raise PermissionDenied
    if not authz.is_courier(request.user):
        raise PermissionDenied
    CourierCourseItem.objects.filter(courier=request.user, task_id=task_id).delete()
    messages.info(request, "Премахнато от курса.")
    return redirect(request.POST.get("next") or "tasks:list")


@login_required
def course_bulk_done(request: HttpRequest) -> HttpResponse:
    if request.method != "POST":
        raise PermissionDenied
    if not authz.is_courier(request.user):
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
    if not (authz.is_merchant(request.user) or authz.is_admin(request.user)):
        raise PermissionDenied
    generate_recurring_tasks_for_day()

    now = timezone.now()
    selected_date_raw = request.GET.get("date") or ""
    selected_date = timezone.localdate()
    if selected_date_raw:
        try:
            selected_date = datetime.strptime(selected_date_raw, "%Y-%m-%d").date()
        except ValueError:
            selected_date = timezone.localdate()

    since = now - timedelta(days=30)

    base = Task.objects.all()
    open_qs = base.filter(status=Task.Status.OPEN)
    done_qs = base.filter(status=Task.Status.DONE)
    due_on_day = base.filter(due_at__date=selected_date)
    open_due_on_day = open_qs.filter(due_at__date=selected_date)
    missed_before_day = open_qs.filter(due_at__date__lt=selected_date)
    due_by_day_open = open_qs.filter(due_at__date__lte=selected_date)
    done_on_day = done_qs.filter(done_at__date=selected_date)

    kpi = {
        "planned_day": due_on_day.count(),
        "open_day": open_due_on_day.count(),
        "missed_before_day": missed_before_day.count(),
        "workload_day": due_by_day_open.count(),
        "done_day": done_on_day.count(),
        "urgent_open": due_by_day_open.filter(urgency=Task.Urgency.URGENT).count(),
        "created_30d": base.filter(created_at__gte=since).count(),
        "done_30d": done_qs.filter(done_at__gte=since).count(),
    }

    by_day = (
        base.filter(due_at__gte=since)
        .annotate(day=TruncDate("due_at"))
        .values("day")
        .annotate(c=Count("id"))
        .order_by("day")
    )
    chart_days = [str(r["day"]) for r in by_day]
    chart_counts = [r["c"] for r in by_day]

    urgency_dist = due_by_day_open.values("urgency").annotate(c=Count("id")).order_by("urgency")
    urgency_labels_map = dict(Task.Urgency.choices)
    chart_urgency_labels = [urgency_labels_map[r["urgency"]] for r in urgency_dist]
    chart_urgency_counts = [r["c"] for r in urgency_dist]

    return render(
        request,
        "reports/index.html",
        {
            "kpi": kpi,
            "selected_date": selected_date,
            "chart_days": chart_days,
            "chart_counts": chart_counts,
            "chart_urgency_labels": chart_urgency_labels,
            "chart_urgency_counts": chart_urgency_counts,
        },
    )
