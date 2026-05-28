from django.contrib import admin

from .models import Address, CourierCourseItem, RecurringTaskTemplate, Task


@admin.register(Address)
class AddressAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("name", "address_text")


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ("id", "type", "title", "status", "urgency", "due_at", "recurring_template", "created_at")
    list_filter = ("status", "type", "urgency")
    search_fields = ("title", "description", "address_text", "customer_name", "phone")
    autocomplete_fields = ("address", "created_by", "done_by")
    readonly_fields = ("created_at",)


@admin.register(CourierCourseItem)
class CourierCourseItemAdmin(admin.ModelAdmin):
    list_display = ("courier", "task", "added_at")
    search_fields = ("courier__username", "task__title")
    autocomplete_fields = ("courier", "task")


@admin.register(RecurringTaskTemplate)
class RecurringTaskTemplateAdmin(admin.ModelAdmin):
    list_display = ("task_text", "frequency", "weekday", "due_time", "is_active", "created_by")
    list_filter = ("frequency", "weekday", "is_active", "urgency")
    search_fields = ("task_text", "contact_address", "note")
    autocomplete_fields = ("created_by",)
