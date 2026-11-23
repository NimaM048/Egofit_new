"""File type classification shared by uploaded media models."""

VIDEO_FILE_EXTENSIONS = frozenset(
    {"mp4", "webm", "mov", "m4v", "ogg", "ogv", "avi", "mkv"}
)


IMAGE_FILE_EXTENSIONS = frozenset(
    {"jpg", "jpeg", "png", "gif", "webp", "bmp", "avif", "heic", "heif"}
)


def _is_video_file_name(name):
    suffix = str(name or "").rsplit(".", 1)
    return len(suffix) == 2 and suffix[1].lower() in VIDEO_FILE_EXTENSIONS
