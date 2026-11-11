# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _hyper(path: Path) -> dict:
    if not path.exists():
        return {}
    out = {}
    for k, v in json.loads(path.read_text()).items():
        try:
            out[k] = json.loads(v) if isinstance(v, str) else v
        except ValueError:
            out[k] = v
    return out


def load_dataset(directory: Path):
    from docclass.core.types import DocumentText
    data = []
    for f in sorted(directory.rglob("*.json")):
        rec = json.loads(f.read_text())
        if rec.get("document") and rec.get("class_id"):
            data.append((DocumentText.from_dict(rec["document"]), rec["class_id"]))
    return data


def run(input_dir: Path, model_dir: Path, hyper: dict) -> dict:
    from docclass.methods.char_ngram_lr.export import export
    from docclass.methods.char_ngram_lr.train import TrainConfig, train
    classes = hyper["classes"]
    if isinstance(classes, str):
        classes = json.loads(classes)
    cfg = TrainConfig(taxonomy=classes, taxonomy_version=str(hyper["taxonomy_version"]),
                      target_precision=float(hyper.get("target_precision", 0.99)))
    model = train(load_dataset(input_dir), cfg)
    model_dir.mkdir(parents=True, exist_ok=True)
    export(model, model_dir / "artefact.npz")
    (model_dir / "report.json").write_text(json.dumps(model.report, default=str))
    return model.report


if __name__ == "__main__":
    base = Path(os.environ.get("SM_BASE", "/opt/ml"))
    run(base / "input" / "data" / "train", base / "model", _hyper(base / "input" / "config" / "hyperparameters.json"))
