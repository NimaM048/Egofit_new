from functools import wraps

from api.responses import error


def api_admin_required(view):
    """Require the existing site administrator role after bearer authentication."""
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        user = getattr(request, "user", None)
        if not getattr(user, "is_authenticated", False) or not getattr(user, "is_admin", False):
            return error("permission_denied", "Administrator access is required.", status=403)
        return view(request, *args, **kwargs)

    return wrapped


def api_coach_required(view):
    """Require a separately granted coach role; coaches do not inherit admin access."""
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        user = getattr(request, "user", None)
        if not getattr(user, "is_authenticated", False) or not getattr(user, "is_coach", False):
            return error("permission_denied", "Coach access is required.", status=403)
        return view(request, *args, **kwargs)

    return wrapped
