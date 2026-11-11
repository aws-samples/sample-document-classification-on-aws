# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Turn a folder of labelled PDFs into training data for a namespace.

The input has one folder per class, named by class id:

    my-documents/W2/employer-a-2024.pdf
    my-documents/INVOICE/supplier-b-0042.pdf

Each PDF's text layer is read the way the service reads it (first
--max-pages pages, pages under --min-chars characters count as empty), and
written to the data bucket twice:

    exemplars/<namespace>/<doc_hash>.json   for the tier 2 (Titan) index build
    labelled/<namespace>/<doc_hash>.json    for tier 1 (n-gram) training

PDFs with no usable text layer are skipped: send those through the async API
instead, so Textract reads them. Every --holdout-every'th file per class is
kept out and listed in --holdout-file, to evaluate the trained tiers on.

    python3 scripts/make_exemplars.py my-documents --namespace team-a \\
        --bucket "$(terraform -chdir=examples/pooled output -raw data_bucket)"

Requires the `pdf` and `aws` extras: pip install -e '.[aws,pdf]'.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from docclass.core.types import DocumentText, PageText  # noqa: E402
from service.common.storage import label_key, text_key  # noqa: E402
from service.text.pdf import PdfError, inspect_pdf  # noqa: E402

logging.getLogger("pypdf").setLevel(logging.ERROR)


def read(path: Path, max_pages: int, min_chars: int) -> DocumentText | None:
    body = path.read_bytes()
    info = inspect_pdf(body, max_pages=max_pages, min_chars=min_chars)
    if info.needs_ocr:
        return None
    pages = tuple(PageText(n, t, "text_layer") for n, t in info.pages)
    return DocumentText(hashlib.sha256(body).hexdigest(), pages)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", type=Path, help="directory with one folder of PDFs per class")
    ap.add_argument("--namespace", required=True)
    ap.add_argument("--bucket", help="the data bucket; required unless --dry-run")
    ap.add_argument("--region")
    ap.add_argument("--taxonomy-version", default="tx-1", help="must match the namespace configuration")
    ap.add_argument("--max-pages", type=int, default=2)
    ap.add_argument("--min-chars", type=int, default=50)
    ap.add_argument("--holdout-every", type=int, default=5, help="keep every Nth file per class out; 0 keeps none")
    ap.add_argument("--holdout-file", type=Path, default=Path("holdout.json"))
    ap.add_argument("--dry-run", action="store_true", help="read and report, upload nothing")
    args = ap.parse_args()
    if not args.dry_run and not args.bucket:
        ap.error("--bucket is required unless --dry-run")

    s3 = None
    if not args.dry_run:
        import boto3
        s3 = boto3.client("s3", region_name=args.region)

    holdout: dict[str, str] = {}
    totals: dict[str, dict[str, int]] = {}
    for folder in sorted(p for p in args.src.iterdir() if p.is_dir()):
        class_id = folder.name
        counts = totals.setdefault(class_id, {"exemplars": 0, "held_out": 0, "no_text": 0, "unreadable": 0})
        for i, pdf in enumerate(sorted(folder.glob("*.pdf")), 1):
            if args.holdout_every and i % args.holdout_every == 0:
                holdout[str(pdf.resolve())] = class_id
                counts["held_out"] += 1
                continue
            try:
                doc = read(pdf, args.max_pages, args.min_chars)
            except PdfError:
                counts["unreadable"] += 1
                continue
            if doc is None:
                counts["no_text"] += 1
                continue
            counts["exemplars"] += 1
            if s3 is None:
                continue
            record = {"class_id": class_id, "document": doc.to_dict()}
            s3.put_object(Bucket=args.bucket, Key=f"exemplars/{args.namespace}/{doc.doc_hash}.json",
                          Body=json.dumps(record).encode(), ContentType="application/json")
            label = {"doc_hash": doc.doc_hash, "class_id": class_id, "taxonomy_version": args.taxonomy_version,
                     "labelled_at": int(time.time()), "principal": "scripts/make_exemplars.py",
                     "text_key": text_key(args.namespace, doc.doc_hash), "document": doc.to_dict()}
            s3.put_object(Bucket=args.bucket, Key=label_key(args.namespace, doc.doc_hash),
                          Body=json.dumps(label).encode(), ContentType="application/json")

    args.holdout_file.write_text(json.dumps(holdout, indent=1))
    for class_id, c in totals.items():
        print(f"{class_id}: {c['exemplars']} exemplars, {c['held_out']} held out, "
              f"{c['no_text']} without a text layer, {c['unreadable']} unreadable")
    n = sum(c["exemplars"] for c in totals.values())
    print(f"{n} exemplars{' (dry run, nothing uploaded)' if args.dry_run else ''}; "
          f"{len(holdout)} held out, listed in {args.holdout_file}")
    if n < 100:
        print("note: the tier 2 index stays disabled with fewer than about 100 exemplars")
    return 0


if __name__ == "__main__":
    sys.exit(main())
