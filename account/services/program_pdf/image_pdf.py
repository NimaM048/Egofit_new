"""Build PDF objects and annotations for image-based and minimal output."""

from __future__ import annotations

from io import BytesIO


def _build_image_pdf_with_links(pages, page_links, *, page_width, page_height):
    """Build an image PDF and retain URI annotations for the fallback renderer."""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        None,
    ]
    page_object_ids = []

    for page, links in zip(pages, page_links):
        page_object_id = len(objects) + 1
        objects.append(None)
        page_object_ids.append(page_object_id)

        image_output = BytesIO()
        page.convert("RGB").save(image_output, format="JPEG", quality=90, optimize=True)
        image_data = image_output.getvalue()
        image_object_id = len(objects) + 1
        objects.append(
            b"<< /Type /XObject /Subtype /Image "
            + f"/Width {page_width} /Height {page_height} ".encode("ascii")
            + b"/ColorSpace /DeviceRGB /BitsPerComponent 8 "
            + b"/Filter /DCTDecode /Length "
            + str(len(image_data)).encode("ascii")
            + b" >>\nstream\n"
            + image_data
            + b"\nendstream"
        )

        content = (
            f"q\n{page_width} 0 0 {page_height} 0 0 cm\n"
            "/Im0 Do\nQ\n"
        ).encode("ascii")
        content_object_id = len(objects) + 1
        objects.append(
            b"<< /Length "
            + str(len(content)).encode("ascii")
            + b" >>\nstream\n"
            + content
            + b"endstream"
        )

        annotation_ids = []
        for left, top, width, height, href in links:
            annotation_object_id = len(objects) + 1
            annotation_ids.append(annotation_object_id)
            bottom = page_height - top - height
            uri = _pdf_literal_string(href)
            objects.append(
                b"<< /Type /Annot /Subtype /Link "
                + f"/Rect [{left:g} {bottom:g} {left + width:g} {bottom + height:g}] ".encode("ascii")
                + b"/Border [0 0 0] /A << /S /URI /URI "
                + uri
                + b" >> >>"
            )

        page_object = (
            b"<< /Type /Page /Parent 2 0 R "
            + f"/MediaBox [0 0 {page_width} {page_height}] ".encode("ascii")
            + b"/Resources << /XObject << /Im0 "
            + f"{image_object_id} 0 R".encode("ascii")
            + b" >> >> /Contents "
            + f"{content_object_id} 0 R".encode("ascii")
        )
        if annotation_ids:
            page_object += b" /Annots [" + b" ".join(
                f"{annotation_id} 0 R".encode("ascii")
                for annotation_id in annotation_ids
            ) + b"]"
        page_object += b" >>"
        objects[page_object_id - 1] = page_object

    objects[1] = (
        b"<< /Type /Pages /Kids ["
        + b" ".join(f"{page_id} 0 R".encode("ascii") for page_id in page_object_ids)
        + f"] /Count {len(page_object_ids)} >>".encode("ascii")
    )
    return _serialize_pdf_objects(objects)


def _pdf_literal_string(value):
    escaped = str(value).encode("utf-8").replace(b"\\", b"\\\\")
    escaped = escaped.replace(b"(", b"\\(").replace(b")", b"\\)")
    return b"(" + escaped + b")"


def _serialize_pdf_objects(objects):
    pdf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for object_number, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{object_number} 0 obj\n".encode("ascii"))
        pdf.extend(obj)
        pdf.extend(b"\nendobj\n")

    xref_offset = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    pdf.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF".encode("ascii")
    )
    return bytes(pdf)


def _minimal_pdf(lines: list[str]) -> bytes:
    """Create a valid, readable ASCII PDF when ReportLab is not installed."""
    escaped_lines = []
    for line in lines[:45]:
        safe = str(line).encode("ascii", "replace").decode("ascii")
        safe = safe.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        escaped_lines.append(f"({safe}) Tj")
    content = "BT /F1 10 Tf 40 800 Td 0 -16 Td ".encode("ascii")
    content += b" ".join(
        [f"{line} 0 -16 Td".encode("ascii") for line in escaped_lines]
    )
    content += b" ET"

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content)).encode("ascii") + b" >>\nstream\n" + content + b"\nendstream",
    ]
    pdf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{number} 0 obj\n".encode("ascii"))
        pdf.extend(obj)
        pdf.extend(b"\nendobj\n")
    xref_offset = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    pdf.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF".encode("ascii")
    )
    return bytes(pdf)
