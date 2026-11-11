# 0008. Text acquisition: text layer first, Textract for scans

- **Status:** Accepted (2026-10-02); PDF library superseded by [0011](0011-permissive-pdf-library.md)
- **Date:** 2026-10-02
- **Spec:** [../specs/platform/](../specs/platform/)
- **Related:** 0001, 0006

## Context

Every tier reads `DocumentText` (ADR-0001 §8). Documents arrive as born-digital
PDFs (with an exact, free text layer), as scans (needing OCR), or as a mix of
the two within one file. OCR is the largest per-page cost in the pipeline, and
running it twice on the same document is pure waste.

## Decision

1. **Per page, use the text layer when it is good enough.** That means at
   least `min_chars` (default 50) of extractable characters, read with PyMuPDF
   in the Text Lambda. Only the remaining pages go to OCR.
2. **OCR with Amazon Textract `StartDocumentTextDetection`** (async, S3 input),
   resumed through SNS → Lambda → `SendTaskSuccess` on the execution's task
   token. Only the page range the classifier needs (first `max_pages`, default
   2) is OCR'd for classification. Extraction may request further pages later.
3. **Store `DocumentText` once**, at `text/{namespace}/{doc_hash}.json`, and
   reuse it whenever the same hash arrives again.
4. **Record provenance per page** (`text_layer` | `ocr`).
5. **Pre-flight limits:** files over 500 MB, or over 3,000 pages, are rejected
   before OCR, with a clear error. Encrypted or corrupt PDFs fail as `Rejected`
   with a reason, never retried.

## Alternatives considered

| Alternative | Why it lost |
|---|---|
| Textract for every page | Pays for OCR on pages that already carry exact text |
| Textract `AnalyzeDocument` (FORMS/TABLES) at classification time | Costs more per page than plain text detection, and classification needs no layout. Extraction can call it later on the chosen pages |
| Self-hosted Tesseract in Lambda | Lower accuracy on degraded scans, and a large binary to maintain. Kept as an offline option for local tests |
| OCR the whole document up front | Classification needs only the first pages. OCR'ing everything wastes money on long documents |

## Consequences

- **Positive.** Born-digital documents cost no OCR at all, and redeliveries
  never pay twice for OCR.
- **Negative.** Two text paths mean two kinds of output to keep consistent,
  which a normalisation test across both covers. A task-token wait adds a
  callback Lambda and an SNS topic.

## Revisit when

- Textract adds a synchronous multi-page text API, or the latency of the
  callback path becomes a problem.
- A large share of documents are images rather than PDFs: add image handling
  to the pre-flight step.
