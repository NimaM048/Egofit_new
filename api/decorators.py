from __future__ import annotations

from functools import wraps
import logging

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.http import Http404
from django.views.decorators.csrf import csrf_exempt

from account.exceptions import AccountException
from cart.exceptions import CartException
from home.exceptions import HomeException
from api.responses import error


logger = logging.getLogger("api")


def _readable_validation_message(details) -> str:
    """Return the first human message from a DRF-style error mapping.

    ``str(ValidationError)`` renders the raw Python dict (``{'field': ['...']}``)
    which leaked straight into mobile toasts. Prefer the first actual message.
    """
    if not isinstance(details, dict):
        return ""
    for value in details.values():
        for item in value or []:
            text = str(item).strip()
            if text:
                return text
    return ""


def api_auth_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not getattr(request, "api_token", None) or not getattr(request.user, "is_authenticated", False):
            return error("authentication_required", "A valid API bearer token is required.", status=401)
        return view(request, *args, **kwargs)

    return wrapped


def api_methods(*methods):
    allowed = {method.upper() for method in methods}

    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if request.method.upper() not in allowed:
                response = error("method_not_allowed", "This HTTP method is not supported.", status=405)
                response["Allow"] = ", ".join(sorted(allowed))
                return response
            return view(request, *args, **kwargs)

        return wrapped

    return decorator


def api_endpoint(view):
    """Return predictable JSON errors and opt bearer-token calls out of CSRF.

    Bearer tokens are sent explicitly in an Authorization header and are not
    ambient browser credentials, so CSRF is not the relevant control here.
    Browser/session authentication is rejected by the API middleware.
    """

    @wraps(view)
    def wrapped(request, *args, **kwargs):
        try:
            return view(request, *args, **kwargs)
        except (ValueError, ValidationError) as exc:
            details = None
            message = ""
            if isinstance(exc, ValidationError):
                if hasattr(exc, "message_dict"):
                    details = {str(key): [str(item) for item in value] for key, value in exc.message_dict.items()}
                else:
                    details = {"non_field_errors": [str(item) for item in exc.messages]}
                message = _readable_validation_message(details)
            if not message:
                message = str(exc) or "Invalid request."
            return error("validation_error", message, details=details)
        except (AccountException, CartException, HomeException) as exc:
            return error("request_rejected", str(exc) or "The request could not be completed.")
        except (Http404, LookupError):
            return error("not_found", "The requested resource was not found.", status=404)
        except IntegrityError:
            return error("conflict", "The request conflicts with existing data.", status=409)
        except Exception:
            logger.exception("Unhandled API exception", extra={"path": request.path, "method": request.method})
            return error("server_error", "An unexpected error occurred.", status=500)

    return csrf_exempt(wrapped)
