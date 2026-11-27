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


def is_merchant_member(user: AbstractBaseUser) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    return user.groups.filter(name=MERCHANT_GROUP).exists()


def can_edit_course(user: AbstractBaseUser) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    if is_merchant_member(user):
        return False
    if is_admin(user):
        return True
    return user.groups.filter(name=COURIER_GROUP).exists()


def can_add_to_course(user: AbstractBaseUser) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    return is_merchant_member(user) or can_edit_course(user)


def can_view_reports(user: AbstractBaseUser) -> bool:
    return is_admin(user) and not is_merchant_member(user)


def is_courier_only(user: AbstractBaseUser) -> bool:
    if not getattr(user, "is_authenticated", False) or is_admin(user):
        return False
    return user.groups.filter(name=COURIER_GROUP).exists()


def is_merchant(user: AbstractBaseUser) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    return is_admin(user) or user.groups.filter(name=MERCHANT_GROUP).exists()
