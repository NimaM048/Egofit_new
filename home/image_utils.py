from __future__ import annotations

import io
from pathlib import Path

from django.core.files.base import ContentFile
from PIL import Image, ImageOps


DEFAULT_MAX_DIMENSION = 1600
DEFAULT_QUALITY = 82
INLINE_IMAGE_MAX_DIMENSION = 1400

# Animated GIFs and vector/camera formats are intentionally left untouched so
# that compression never breaks animation or transparency semantics.
_COMPRESSIBLE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"})


def _target_format(suffix: str) -> str:
    if suffix == ".png":
        return "PNG"
    if suffix == ".webp":
        return "WEBP"
    return "JPEG"


def compress_image_file(file, *, max_dimension: int = DEFAULT_MAX_DIMENSION, quality: int = DEFAULT_QUALITY):
    """Return a smaller in-memory copy of an uploaded image.

    Falls back to the original upload whenever Pillow cannot read the file or
    the re-encoded version is not actually smaller.
    """
    if not file:
        return file

    name = getattr(file, "name", "") or ""
    suffix = Path(name).suffix.lower()
    if suffix not in _COMPRESSIBLE_SUFFIXES:
        return file

    try:
        file.seek(0)
        image = Image.open(file)
        image.load()
    except Exception:
        return file

    original_size = getattr(file, "size", None)
    try:
        image = ImageOps.exif_transpose(image)
    except Exception:
        pass

    target_format = _target_format(suffix)
    if target_format == "JPEG" and image.mode not in ("RGB", "L"):
        background = Image.new("RGB", image.size, (255, 255, 255))
        if image.mode in ("RGBA", "LA", "P"):
            image = image.convert("RGBA")
            background.paste(image, mask=image.split()[-1])
        else:
            background.paste(image.convert("RGB"))
        image = background
    elif target_format == "WEBP" and image.mode == "P":
        image = image.convert("RGBA" if "transparency" in image.info else "RGB")

    if max(image.size) > max_dimension:
        image.thumbnail((max_dimension, max_dimension), Image.LANCZOS)

    output = io.BytesIO()
    try:
        if target_format == "JPEG":
            image.save(output, format=target_format, quality=quality, optimize=True, progressive=True)
        else:
            image.save(output, format=target_format, quality=quality, optimize=True)
    except Exception:
        return file

    data = output.getvalue()
    if not data or (original_size and len(data) >= original_size):
        return file

    return ContentFile(data, name=Path(name).name)


def optimize_image_field(instance, field_name: str, **kwargs) -> None:
    """Compress a not-yet-saved ImageField/FileField upload in place."""
    field = getattr(instance, field_name, None)
    if not field or not getattr(field, "name", ""):
        return
    if getattr(field, "_committed", True):
        return

    source = getattr(field, "file", None)
    if not source:
        return

    compressed = compress_image_file(source, **kwargs)
    if compressed is source:
        return
    field.save(Path(field.name).name, compressed, save=False)


def profile_picture_name(author) -> str:
    picture = getattr(author, "profile_picture", None) if author else None
    return getattr(picture, "name", "") or ""


def apply_author_image(instance, field_name: str = "author_image") -> None:
    """Fill a blank author image from the author's profile picture."""
    current = getattr(instance, field_name, None)
    if current and getattr(current, "name", ""):
        return
    picture_name = profile_picture_name(getattr(instance, "author", None))
    if picture_name:
        setattr(instance, field_name, picture_name)
