# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

import io
from dataclasses import dataclass, field

MAX_BYTES = 500 * 1024 * 1024
MAX_PAGES = 3000


class PdfError(Exception):
    pass


@dataclass
class PdfInfo:
    page_count: int
    pages: list[tuple[int, str]] = field(default_factory=list)
    needs_ocr: list[int] = field(default_factory=list)


def _open(source: bytes | str):
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(source) if isinstance(source, (bytes, bytearray)) else source)
        if reader.is_encrypted:
            raise PdfError("encrypted PDF")
        len(reader.pages)
    except PdfError:
        raise
    except Exception as e:
        raise PdfError(f"not a readable PDF: {type(e).__name__}")
    return reader


def preflight(source: bytes | str, size_bytes: int) -> int:
    if size_bytes > MAX_BYTES:
        raise PdfError(f"file exceeds {MAX_BYTES} bytes")
    pages = len(_open(source).pages)
    if pages == 0:
        raise PdfError("PDF has no pages")
    if pages > MAX_PAGES:
        raise PdfError(f"PDF exceeds {MAX_PAGES} pages")
    return pages


def inspect_pdf(source: bytes | str, *, max_pages: int, min_chars: int) -> PdfInfo:
    reader = _open(source)
    info = PdfInfo(len(reader.pages))
    for i in range(min(max_pages, info.page_count)):
        text = reader.pages[i].extract_text() or ""
        if len("".join(text.split())) >= min_chars:
            info.pages.append((i + 1, text))
        else:
            info.pages.append((i + 1, ""))
            info.needs_ocr.append(i + 1)
    return info


def subset_pdf(source: bytes | str, page_numbers: list[int]) -> bytes:
    from pypdf import PdfWriter

    reader = _open(source)
    out = PdfWriter()
    for n in page_numbers:
        out.add_page(reader.pages[n - 1])
    buf = io.BytesIO()
    out.write(buf)
    return buf.getvalue()
