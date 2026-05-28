from __future__ import annotations

from . import authz


def role_flags(request):
    user = getattr(request, "user", None)
    return {
        "is_courier": authz.is_courier(user),
        "is_merchant": authz.is_merchant(user),
        "is_admin": authz.is_admin(user),
    }

