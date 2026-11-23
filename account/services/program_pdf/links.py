"""Resolve site and movement media links for PDF output."""

from __future__ import annotations

from urllib.parse import urljoin, urlparse

from django.conf import settings


def _pdf_link_base_url(request=None) -> str:
    if request is not None:
        try:
            return request.build_absolute_uri("/")
        except Exception:
            pass

    configured_base_url = (
        getattr(settings, "EGOFIT_PUBLIC_BASE_URL", "")
        or getattr(settings, "PUBLIC_BASE_URL", "")
    )
    return str(configured_base_url or "").rstrip("/") + "/" if configured_base_url else ""


def _pdf_movement_media_url(movement, link_base_url="") -> str:
    media = next(iter(_movement_video_files(movement)), None)
    if media is None:
        media = getattr(movement, "media", None) or getattr(movement, "image", None)
    media_name = getattr(media, "name", "") if media else ""
    if not media_name:
        return ""
    if str(media_name).startswith(("http://", "https://")):
        return str(media_name)

    media_url = ""
    try:
        storage = getattr(media, "storage", None)
        if storage is not None:
            media_url = storage.url(media_name)
    except Exception:
        pass
    if not media_url:
        try:
            media_url = media.url
        except Exception:
            return ""

    media_url = str(media_url or "")
    parsed_url = urlparse(media_url)
    if parsed_url.scheme:
        return media_url if parsed_url.scheme in {"http", "https"} else ""
    if media_url.startswith("//"):
        return urljoin(link_base_url, media_url) if link_base_url else ""
    return urljoin(link_base_url, media_url.lstrip("/")) if link_base_url else media_url


def _movement_video_files(movement):
    try:
        video_files = list(movement.video_files)
    except Exception:
        video_files = []
    if video_files:
        return video_files
    return [
        video
        for field_name in ("video_1", "video_2", "video_3")
        if (video := getattr(movement, field_name, None))
    ]
