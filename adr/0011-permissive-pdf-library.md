# 0011. PDF handling with pypdf, a permissively licensed library

- **Status:** Accepted (2026-10-12)
- **Date:** 2026-10-12
- **Spec:** [../specs/platform/](../specs/platform/)
- **Related:** 0008 (supersedes its choice of PyMuPDF)

## Context

ADR-0008 reads the PDF text layer with PyMuPDF. PyMuPDF is licensed under
AGPL-3.0 or a commercial licence from Artifex. The release build bundles the
PDF library into the Lambda package, so every deployment distributes it, and
customers who reuse the package in a networked service take on AGPL-3.0
obligations. The project is published as MIT-0 sample code.

The service needs four PDF operations: open a document and reject encrypted
ones, count pages, extract each page's text, and write a PDF holding only the
pages that need OCR.

## Decision

1. **Use pypdf** (BSD-3-Clause) for all four operations, pinned in
   `scripts/build_release.sh` and `pyproject.toml`.
2. **No AGPL or other copyleft library ships in the Lambda package.**

## Alternatives considered

- **Keep PyMuPDF and document the licence.** Leaves customers with AGPL-3.0
  obligations or a commercial licence, which does not suit MIT-0 sample code.
- **pdfminer.six** (MIT). Extracts text well but cannot write the subset PDF
  that Textract reads, so a second library would still be needed.
- **pdftotext / Poppler.** GPL, and a native binary in a Lambda layer.

## Consequences

- The Lambda package is smaller and pure Python, with no native PDF library.
- pypdf's text extraction differs from PyMuPDF's in spacing and reading order.
  The text-layer check only counts non-whitespace characters, so routing to
  Textract is unaffected, but extracted text is not byte-identical to before.
- pypdf is slower on very large documents. Preflight's page and size limits
  (AC-06) bound the work.

## Revisit when

- Text-layer extraction quality causes measurable misclassification compared
  with Textract output for the same pages.
- pypdf cannot open a class of PDFs that consumers send in volume.
