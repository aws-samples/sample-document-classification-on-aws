# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Iterable

OTHER = "OTHER"


def build_label_map(enabled_for: Iterable[str]) -> list[str]:
    labels = sorted(set(enabled_for))
    if not labels:
        raise ValueError("enabled_for must not be empty")
    if OTHER in labels:
        raise ValueError("OTHER is reserved")
    return labels + [OTHER]


def relabel(examples: Iterable[tuple[str, str]], labels: list[str]) -> list[tuple[str, int]]:
    index = {c: i for i, c in enumerate(labels)}
    other = index[OTHER]
    return [(text, index.get(cls, other)) for text, cls in examples]


def load_examples(path: Path) -> list[tuple[str, str]]:
    out = []
    with open(path) as f:
        for line in f:
            if line.strip():
                row = json.loads(line)
                out.append((str(row["text"]), str(row["class_id"])))
    return out


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--enabled-for", required=True)
    p.add_argument("--model-version", required=True)
    # The pretrained encoder to fine-tune, and the exact revision (commit) to
    # download, so the training input is reproducible.
    p.add_argument("--base-model", required=True)
    p.add_argument("--base-model-revision", required=True)
    p.add_argument("--epochs", type=int, default=3)
    p.add_argument("--batch-size", type=int, default=16)
    p.add_argument("--learning-rate", type=float, default=5e-5)
    p.add_argument("--max-length", type=int, default=512)
    p.add_argument("--train", default=os.environ.get("SM_CHANNEL_TRAIN", "data/train"))
    p.add_argument("--model-dir", default=os.environ.get("SM_MODEL_DIR", "model"))
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer, Trainer, TrainingArguments

    args = parse_args(argv)
    labels = build_label_map(args.enabled_for.split(","))
    rows = relabel(load_examples(Path(args.train) / "train.jsonl"), labels)
    tokenizer = AutoTokenizer.from_pretrained(args.base_model, revision=args.base_model_revision)
    model = AutoModelForSequenceClassification.from_pretrained(args.base_model, revision=args.base_model_revision,
                                                               num_labels=len(labels))

    class Dataset(torch.utils.data.Dataset):
        def __init__(self, items):
            self.enc = tokenizer([t for t, _ in items], truncation=True, max_length=args.max_length, padding=True)
            self.labels = [y for _, y in items]

        def __len__(self):
            return len(self.labels)

        def __getitem__(self, i):
            item = {k: torch.tensor(v[i]) for k, v in self.enc.items()}
            item["labels"] = torch.tensor(self.labels[i])
            return item

    trainer = Trainer(
        model=model,
        args=TrainingArguments(output_dir=tempfile.mkdtemp(prefix="docclass-train-"), num_train_epochs=args.epochs,
                               per_device_train_batch_size=args.batch_size, learning_rate=args.learning_rate,
                               save_strategy="no", report_to=[]),
        train_dataset=Dataset(rows),
    )
    trainer.train()
    out = Path(args.model_dir)
    out.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(out)
    tokenizer.save_pretrained(out)
    (out / "docclass.json").write_text(json.dumps(
        {"labels": labels, "model_version": args.model_version, "max_length": args.max_length}))


if __name__ == "__main__":
    main()
