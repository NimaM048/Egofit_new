"""
Static files storage that adds a release version to every {% static %} URL.

Apache serves /static/ with a long browser cache (7 days). File names are not
hashed, so without a version visitors could keep stale CSS/JS after a deploy.
The version is the deployed git commit (or DJANGO_STATIC_VERSION), so every
release produces new URLs while files stay cacheable between releases.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from django.conf import settings
from whitenoise.storage import CompressedStaticFilesStorage


@lru_cache(maxsize=1)
def static_version() -> str:
    explicit = os.getenv("DJANGO_STATIC_VERSION", "").strip()
    if explicit:
        return explicit
    git_dir = Path(settings.BASE_DIR) / ".git"
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
        if head.startswith("ref:"):
            ref = head.split(" ", 1)[1].strip()
            ref_file = git_dir / ref
            if ref_file.exists():
                return ref_file.read_text(encoding="utf-8").strip()[:10]
            packed = git_dir / "packed-refs"
            for line in packed.read_text(encoding="utf-8").splitlines():
                if line.endswith(" " + ref):
                    return line.split(" ", 1)[0][:10]
            return ""
        return head[:10]
    except OSError:
        return ""


class VersionedStaticFilesStorage(CompressedStaticFilesStorage):
    def url(self, name):
        url = super().url(name)
        version = static_version()
        if not version:
            return url
        parts = urlsplit(url)
        query = f"{parts.query}&v={version}" if parts.query else f"v={version}"
        return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))
