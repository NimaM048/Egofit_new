from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.sessions.backends.base import UpdateError
from django.contrib.sessions.exceptions import SessionInterrupted
from django.contrib.sessions.middleware import SessionMiddleware
from django.middleware.gzip import GZipMiddleware
from django.utils.cache import patch_vary_headers
from django.utils.regex_helper import _lazy_re_compile


logger = logging.getLogger(__name__)


COMPRESSIBLE_CONTENT_TYPE_REGEX = _lazy_re_compile(
    r"^(?:"
    r"text/[a-z0-9.+-]+"
    r"|application/(?:[a-z0-9.+-]+\+)?(?:json|xml|javascript|x-www-form-urlencoded)"
    r"|application/(?:atom|rss)\+xml"
    r"|image/svg\+xml"
    r")"
)


class SelectableGZipMiddleware(GZipMiddleware):
    """
    GZip only text-like payloads.

    Django's stock middleware compresses *every* response when the client
    advertises gzip, including ``video/*``/``audio/*`` media served through
    ``/media-files/``. That breaks byte-range streaming: Android/iOS players and
    HTML5 ``<video>`` seek with a ``Range`` header, but gzip re-frames the bytes
    and drops ``Content-Length``, so the player sees a malformed stream (and
    binary formats gain ~0 bytes of compression anyway).
    """

    def process_response(self, request, response):
        content_type = response.get("Content-Type", "")
        if content_type and not COMPRESSIBLE_CONTENT_TYPE_REGEX.match(content_type):
            patch_vary_headers(response, ("Accept-Encoding",))
            return response
        return super().process_response(request, response)


class ResilientSessionMiddleware(SessionMiddleware):
    """
    Recover from concurrent session writes instead of returning HTTP 400.

    This happens when login() rotates the session key while another parallel
    request still holds the previous key (common on mobile/homepage loads).
    """

    def process_response(self, request, response):
        try:
            return super().process_response(request, response)
        except SessionInterrupted:
            logger.warning(
                "Session interrupted for %s; preserving in-memory session and saving with a new key.",
                getattr(request, "path", "unknown"),
            )
            return self._retry_session_save(request, response)

    def _retry_session_save(self, request, response):
        if not hasattr(request, "session"):
            raise

        session_data = dict(request.session.items())
        request.session = self.SessionStore()
        if session_data:
            request.session.update(session_data)
        request.session.modified = True

        try:
            return super().process_response(request, response)
        except (SessionInterrupted, UpdateError):
            logger.exception("Unable to recover session for %s", getattr(request, "path", "unknown"))
            request.session = self.SessionStore()
            request.session.modified = False
            return response


class ForceDefaultLanguageMiddleware:
    """
    Drop the browser's Accept-Language header for visitors who haven't made an
    explicit language choice yet, so LocaleMiddleware falls through to
    settings.LANGUAGE_CODE (Persian) instead of auto-detecting English for
    English-locale browsers. Must run before LocaleMiddleware. An explicit
    choice via the language switcher (which sets the django_language cookie)
    always takes priority.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if settings.LANGUAGE_COOKIE_NAME not in request.COOKIES:
            request.META.pop("HTTP_ACCEPT_LANGUAGE", None)
        return self.get_response(request)
