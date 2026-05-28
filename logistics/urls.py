from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

app_name = "tasks"

urlpatterns = [
    path("", views.home_redirect, name="home"),
    path("login/", auth_views.LoginView.as_view(template_name="auth/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("tasks/", views.task_list, name="list"),
    path("tasks/new/", views.task_create, name="new"),
    path("tasks/<int:pk>/", views.task_detail, name="detail"),
    path("recurring/", views.recurring_list, name="recurring_list"),
    path("recurring/new/", views.recurring_create, name="recurring_new"),
    path("course/", views.course, name="course"),
    path("course/add/<int:task_id>/", views.course_add, name="course_add"),
    path("course/remove/<int:task_id>/", views.course_remove, name="course_remove"),
    path("course/bulk-done/", views.course_bulk_done, name="course_bulk_done"),
    path("reports/", views.reports, name="reports"),
]

