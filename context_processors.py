from __future__ import annotations

from . import authz


def role_flags(request):
    user = getattr(request, "user", None)
    return {
        "is_courier": authz.is_courier(user),
        "can_edit_course": authz.can_edit_course(user),
        "can_add_to_course": authz.can_add_to_course(user),
        "can_view_reports": authz.can_view_reports(user),
        "is_courier_only": authz.is_courier_only(user),
        "is_merchant": authz.is_merchant(user),
        "is_merchant_member": authz.is_merchant_member(user),
        "is_admin": authz.is_admin(user),
    }
