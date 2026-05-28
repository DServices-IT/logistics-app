from __future__ import annotations

from django.contrib.auth.models import AbstractBaseUser


COURIER_GROUP = "courier"
MERCHANT_GROUP = "merchant"


def is_admin(user: AbstractBaseUser) -> bool:
    return bool(getattr(user, "is_staff", False) or getattr(user, "is_superuser", False))


def is_courier(user: AbstractBaseUser) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    return is_admin(user) or user.groups.filter(name=COURIER_GROUP).exists()


def is_merchant(user: AbstractBaseUser) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    return is_admin(user) or user.groups.filter(name=MERCHANT_GROUP).exists()

