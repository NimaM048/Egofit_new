"""Load the branded PDF background from the configured assets."""

from __future__ import annotations

from pathlib import Path
import zipfile

from django.conf import settings


def _pdf_background_data():
    try:
        base_dir = Path(settings.BASE_DIR)
    except Exception:
        base_dir = Path(__file__).resolve().parents[3]

    configured_path = getattr(settings, "EGOFIT_PDF_BACKGROUND_PATH", "")
    candidates = [Path(configured_path)] if configured_path else []
    candidates.extend(
        [
            base_dir / "assets" / "images" / "sarbarg-background.jpg",
            base_dir / "assets" / "images" / "sarbarg.jpg",
        ]
    )
    for path in candidates:
        if path and path.exists():
            try:
                return path.read_bytes()
            except Exception:
                continue

    # Keep the reference document useful even if its extracted image asset was
    # not deployed separately.
    docx_path = base_dir / "Sarbarg.docx"
    if docx_path.exists():
        try:
            with zipfile.ZipFile(docx_path) as archive:
                return archive.read("word/media/image1.jpg")
        except (KeyError, OSError, zipfile.BadZipFile):
            pass
    return b""
