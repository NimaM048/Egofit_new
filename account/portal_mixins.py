from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.urls import reverse
from functools import wraps


def get_post_login_redirect_url(user) -> str:
    if getattr(user, "is_admin", False):
        return reverse("register:admin_search")
    if getattr(user, "is_coach", False):
        # Coaches land on their own dashboard rather than the public site.
        return reverse("register:coach_dashboard")
    return reverse("home:home")


class AdminRequiredMixin(LoginRequiredMixin):
    login_url = "/accounts/register/"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not request.user.is_admin:
            if getattr(request.user, "is_coach", False):
                # A coach hitting an administrator-only page (library CRUD,
                # role management, automatic programming) is sent back to their
                # own roster instead of the athlete portal, which would bounce
                # them straight back here.
                return redirect("register:admin_search")
            return redirect("register:profile")
        return super().dispatch(request, *args, **kwargs)


class UserPortalRequiredMixin(LoginRequiredMixin):
    login_url = "/accounts/register/"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if request.user.is_admin:
            return redirect("register:admin_search")
        if request.user.is_coach:
            return redirect("register:admin_search")
        return super().dispatch(request, *args, **kwargs)


def user_portal_required(view_func):
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{reverse('register:register')}?next={request.path}")
        if request.user.is_admin:
            return redirect("register:admin_search")
        if request.user.is_coach:
            return redirect("register:admin_search")
        return view_func(request, *args, **kwargs)

    return _wrapped_view


class StaffRequiredMixin(LoginRequiredMixin):
    """Allow administrators *and* coaches into the staff portal.

    Coaches share most of the admin portal scaffolding (athlete hub, analysis,
    measurements, programs) but must never reach library CRUD, role management
    or automatic programming. Views that are admin-only keep using
    ``AdminRequiredMixin``; views a coach may use while browsing their own
    athletes use this mixin together with an ownership-scoped queryset.
    """

    login_url = "/accounts/register/"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not (request.user.is_admin or request.user.is_coach):
            return redirect("register:profile")
        return super().dispatch(request, *args, **kwargs)


def is_coach_only(user) -> bool:
    """True when the user is a coach who is *not* also an administrator."""
    return bool(getattr(user, "is_coach", False) and not getattr(user, "is_admin", False))


def staff_portal_role(user) -> str:
    """Return ``"admin"`` or ``"coach"`` for template chrome decisions."""
    return "admin" if getattr(user, "is_admin", False) else "coach"


def coach_roster_queryset(coach):
    """Active, non-staff athletes assigned to ``coach``."""
    from account.models import User

    return User.objects.filter(coach=coach, is_active=True, is_admin=False, is_coach=False)
