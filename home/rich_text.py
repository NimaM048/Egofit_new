from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from urllib.parse import urlparse

from django.core.exceptions import ValidationError


_ALLOWED_TAGS = frozenset(
    {
        "a",
        "b",
        "blockquote",
        "br",
        "code",
        "del",
        "div",
        "em",
        "figcaption",
        "figure",
        "h2",
        "h3",
        "h4",
        "hr",
        "i",
        "img",
        "li",
        "ol",
        "p",
        "pre",
        "s",
        "span",
        "strong",
        "u",
        "ul",
    }
)
_VOID_TAGS = frozenset({"br", "hr", "img"})
_SKIP_TAGS = frozenset({"iframe", "object", "script", "style", "svg", "template"})
_ALLOWED_LINK_SCHEMES = frozenset({"http", "https", "mailto"})
_HTML_TAG_RE = re.compile(r"<\s*/?\s*[a-z][^>]*>", re.IGNORECASE)

_FONT_SIZE_RE = re.compile(r"^(?:\d{1,3}(?:\.\d+)?(?:px|pt|em|rem|%)|small|medium|large|x-large)$", re.IGNORECASE)
_TEXT_ALIGN_VALUES = frozenset({"right", "left", "center", "justify"})
_STYLEABLE_TAGS = frozenset({"div", "figcaption", "h2", "h3", "h4", "li", "p", "span"})
_INLINE_IMAGE_CLASSES = frozenset({"article-inline-image", "article-inline-image--wide", "article-inline-image--compact"})


def _sanitize_style(value: str | None) -> str:
    """Keep only font-size and text-align declarations from an inline style."""
    if not value:
        return ""

    safe_declarations: list[str] = []
    for declaration in value.split(";"):
        if ":" not in declaration:
            continue
        property_name, raw_value = declaration.split(":", 1)
        property_name = property_name.strip().casefold()
        raw_value = raw_value.strip()
        if property_name == "font-size" and _FONT_SIZE_RE.match(raw_value):
            safe_declarations.append(f"font-size: {raw_value}")
        elif property_name == "text-align" and raw_value.casefold() in _TEXT_ALIGN_VALUES:
            safe_declarations.append(f"text-align: {raw_value.casefold()}")
    return "; ".join(safe_declarations)


def _plain_text_to_html(value: str) -> str:
    paragraphs = [
        part.strip()
        for part in re.split(r"\r?\n\s*\r?\n+", value.strip())
        if part.strip()
    ]
    if not paragraphs:
        return ""

    return "".join(
        f"<p>{html.escape(paragraph, quote=False).replace(chr(10), '<br>')}</p>"
        for paragraph in paragraphs
    )


def is_safe_article_url(value: str) -> bool:
    value = (value or "").strip()
    if not value or value.startswith("//"):
        return False
    parsed = urlparse(value)
    return not parsed.scheme or parsed.scheme.casefold() in _ALLOWED_LINK_SCHEMES


def validate_article_url(value: str) -> str:
    if not is_safe_article_url(value):
        raise ValidationError("فقط لینک‌های http، https، mailto یا لینک‌های داخلی مجاز هستند.")
    return value


class _RichTextSanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.parts: list[str] = []
        self._skip_depth = 0

    @staticmethod
    def _safe_attributes(tag: str, attrs: list[tuple[str, str | None]]) -> str:
        safe_attrs: list[str] = []

        if tag in _STYLEABLE_TAGS:
            for name, value in attrs:
                if name.casefold() == "style":
                    style = _sanitize_style(value)
                    if style:
                        safe_attrs.append(f'style="{html.escape(style, quote=True)}"')
            return f" {' '.join(safe_attrs)}" if safe_attrs else ""

        if tag != "a":
            return ""

        for name, value in attrs:
            name = name.casefold()
            if name == "href" and value and is_safe_article_url(value):
                safe_attrs.append(f'href="{html.escape(value.strip(), quote=True)}"')
            elif name == "title" and value:
                safe_attrs.append(f'title="{html.escape(value.strip(), quote=True)}"')
            elif name == "target" and value == "_blank":
                safe_attrs.append('target="_blank"')
            elif name == "rel" and value:
                rel_tokens = {
                    token
                    for token in value.split()
                    if token.casefold() in {"nofollow", "noopener", "noreferrer"}
                }
                if rel_tokens:
                    safe_attrs.append(f'rel="{" ".join(sorted(rel_tokens))}"')
        return f" {' '.join(safe_attrs)}" if safe_attrs else ""

    @staticmethod
    def _safe_image_attributes(attrs: list[tuple[str, str | None]]) -> str | None:
        """Return sanitized img attributes or None when the source is unsafe."""
        safe_attrs: list[str] = []
        has_src = False
        for name, value in attrs:
            name = name.casefold()
            if name == "src" and value and is_safe_article_url(value):
                safe_attrs.append(f'src="{html.escape(value.strip(), quote=True)}"')
                has_src = True
            elif name == "alt" and value:
                safe_attrs.append(f'alt="{html.escape(value.strip(), quote=True)}"')
            elif name == "title" and value:
                safe_attrs.append(f'title="{html.escape(value.strip(), quote=True)}"')
            elif name in {"width", "height"} and value and value.strip().isdigit():
                safe_attrs.append(f'{name}="{value.strip()}"')
            elif name == "class" and value:
                classes = [token for token in value.split() if token in _INLINE_IMAGE_CLASSES]
                if classes:
                    safe_attrs.append(f'class="{" ".join(classes)}"')
            elif name == "loading" and value in {"lazy", "eager"}:
                safe_attrs.append(f'loading="{value}"')
        if not has_src:
            return None
        if not any(attr.startswith("alt=") for attr in safe_attrs):
            safe_attrs.append('alt=""')
        return " ".join(safe_attrs)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        if self._skip_depth:
            if tag in _SKIP_TAGS:
                self._skip_depth += 1
            return
        if tag in _SKIP_TAGS:
            self._skip_depth = 1
            return
        if tag not in _ALLOWED_TAGS:
            return
        if tag == "img":
            safe_attrs = self._safe_image_attributes(attrs)
            if safe_attrs is None:
                return
            self.parts.append(f"<img {safe_attrs}>")
            return
        self.parts.append(f"<{tag}{self._safe_attributes(tag, attrs)}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.casefold() not in _VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if self._skip_depth:
            if tag in _SKIP_TAGS:
                self._skip_depth -= 1
            return
        if tag in _ALLOWED_TAGS and tag not in _VOID_TAGS:
            self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(html.escape(data, quote=False))

    def handle_entityref(self, name: str) -> None:
        if not self._skip_depth:
            self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if not self._skip_depth:
            self.parts.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        return


def sanitize_rich_text(value: str | None) -> str:
    if not value:
        return ""
    value = str(value).replace("\r\n", "\n").replace("\r", "\n").strip()
    if not value:
        return ""

    source = value if _HTML_TAG_RE.search(value) else _plain_text_to_html(value)
    parser = _RichTextSanitizer()
    parser.feed(source)
    parser.close()
    sanitized = "".join(parser.parts).strip()
    if not re.sub(r"<[^>]+>", "", sanitized).strip() and "<img" not in sanitized.casefold():
        return ""
    return sanitized
