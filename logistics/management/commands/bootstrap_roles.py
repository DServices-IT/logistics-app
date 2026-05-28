from __future__ import annotations

from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand

from logistics import authz


class Command(BaseCommand):
    help = "Create default groups (courier/merchant) and assign baseline permissions."

    def handle(self, *args, **options):
        courier, _ = Group.objects.get_or_create(name=authz.COURIER_GROUP)
        merchant, _ = Group.objects.get_or_create(name=authz.MERCHANT_GROUP)

        perm_codenames = {
            "add_task",
            "change_task",
            "view_task",
            "add_couriercourseitem",
            "change_couriercourseitem",
            "delete_couriercourseitem",
            "view_couriercourseitem",
            "view_address",
        }
        perms = Permission.objects.filter(codename__in=perm_codenames)

        courier.permissions.set(perms)
        merchant.permissions.set(
            Permission.objects.filter(
                codename__in={
                    "add_task",
                    "change_task",
                    "view_task",
                    "view_address",
                }
            )
        )

        self.stdout.write(self.style.SUCCESS("Groups ensured: courier, merchant"))

