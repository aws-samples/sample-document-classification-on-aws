# Requirements: Char n-gram TF-IDF + logistic regression

- **Status:** Implemented (2026-10-02)
- **ADR:** [0003](../../adr/0003-char-ngram-tfidf-logistic-regression.md)
- **Prefix:** `NGRAM`
- **Depends on:** classifier-contract

## Story 1: train a model from labelled documents

As a **model owner**, I want to train the tier from a folder of labelled
documents in seconds.

- **NGRAM-AC-01** WHEN given a labelled set of `(DocumentText, class_id)` pairs
  THE trainer SHALL fit a `char_wb` TF-IDF vectoriser (n = 3–5, sublinear TF)
  and a multinomial logistic regression with balanced class weights.
- **NGRAM-AC-02** THE trainer SHALL use only the first `max_pages` pages
  (default 2) of each document, Unicode NFKC-normalised, lower-cased, with
  runs of whitespace collapsed to one space, and with tokens made up only of
  digits removed.
- **NGRAM-AC-03** IF any class in the taxonomy has fewer than `min_per_class`
  examples (default 10) THEN THE trainer SHALL exclude that class from the
  model and list it in the training report, rather than fail.
- **NGRAM-AC-04** WHERE every included class has at least `calibration_min`
  examples (default 30), THE trainer SHALL calibrate the probabilities with
  sigmoid calibration fitted on a held-out calibration split (prefit, one
  calibrator per class), not on the data the model was fitted on.
- **NGRAM-AC-05** THE split between training and validation data SHALL be by
  source document, so no document contributes pages to both sides.
- **NGRAM-AC-06** THE trainer SHALL compute one TF-IDF centroid per class,
  L2-normalised, from the training vectors.
- **NGRAM-AC-07** THE training report SHALL include per-class precision and
  recall at the chosen thresholds, coverage, selective accuracy, and the
  five most-confused class pairs.

## Story 2: thresholds chosen by evidence

- **NGRAM-AC-08** WHEN given a target precision THE trainer SHALL choose the
  lowest `τ` on the validation set at which selective precision meets the
  target, and SHALL record `τ`, `δ` and `ρ` in the artefact.
- **NGRAM-AC-09** IF no `τ` reaches the target precision THEN THE trainer SHALL
  mark the artefact `enabled = false` and say so in the report.

## Story 3: a light artefact that scores identically

As an **operator**, I want inference without scikit-learn in the runtime.

- **NGRAM-AC-10** THE exporter SHALL write one artefact containing
  `model_version`, `taxonomy_version`, the n-gram range, vocabulary, IDF,
  coefficients, intercepts, calibration parameters, class order, centroids and
  thresholds.
- **NGRAM-AC-11** THE scorer SHALL depend only on NumPy and the standard
  library.
- **NGRAM-AC-12** FOR every document in the validation set, the scorer's class
  probabilities SHALL match scikit-learn's `predict_proba` within an absolute
  difference of 1e-6.
- **NGRAM-AC-13** IF the artefact's `taxonomy_version` differs from the
  cascade's THEN loading the tier SHALL raise `ConfigError`.

## Story 4: classify, or abstain

- **NGRAM-AC-14** WHEN the top probability ≥ `τ`, the top-two margin ≥ `δ`,
  and the cosine similarity to the predicted class's centroid ≥ `ρ`, THE tier
  SHALL return an accepted decision with `method = "char_ngram_lr"`.
- **NGRAM-AC-15** IF the top probability < `τ` THEN THE tier SHALL abstain with
  `reason = "low confidence"`.
- **NGRAM-AC-16** IF the margin < `δ` THEN THE tier SHALL abstain with `reason =
  "ambiguous: <a> vs <b>"`.
- **NGRAM-AC-17** IF the centroid similarity < `ρ` THEN THE tier SHALL abstain
  with `reason = "out of distribution"`.
- **NGRAM-AC-18** IF the normalised text has fewer than `min_chars` characters
  (default 50) THEN THE tier SHALL abstain with `reason = "insufficient text"`.
- **NGRAM-AC-19** WHERE a per-class `τ` is configured, THE tier SHALL apply it to
  that predicted class (CORE-AC-12).

## Robustness

- **NGRAM-AC-20** ON a validation set with synthetic OCR noise (5% character
  substitution), selective precision at the chosen `τ` SHALL fall by no more
  than 2 percentage points from the clean set.

## Added in review

- **NGRAM-AC-23** IF the artefact has `enabled = false` THEN THE tier SHALL
  abstain on every document with `reason = "tier disabled"`.
- **NGRAM-AC-24** IF fewer than two classes remain after exclusion THEN THE
  trainer SHALL raise `TrainingError` rather than produce an artefact.
- **NGRAM-AC-25** IF the artefact names a class outside the cascade's taxonomy
  THEN loading the tier SHALL raise `ConfigError`.
- **NGRAM-AC-26** WHERE the cascade configuration sets `tau`, `delta` or `rho`
  for the tier, THE tier SHALL use it in place of the artefact's swept value,
  read through `TierSettings.threshold(key, class_id)`.

## Review log

2026-10-02, before implementation:

1. **Calibration folding replaced.** `CalibratedClassifierCV(cv=5)` averages
   five models, and the folding the design described could not reproduce
   `predict_proba`, so AC-12 was unachievable for a calibrated model. The model
   is now one logistic regression, calibrated prefit on a held-out split
   (AC-04). Its probabilities are reproduced exactly.
2. **Scoring formula corrected.** Sigmoid calibration applies to the logits,
   not to softmax probabilities. Uncalibrated: `softmax(z)`. Calibrated:
   `expit(-(a·z + b))` per class, renormalised (binary: positive class only).
3. **Artefact size.** Logistic-regression coefficients are dense, so CSR
   storage saves nothing, and 200 classes × 200,000 features is 320 MB. The
   default `max_features` is now 20,000, and AC-22 states it.
4. **Thresholds.** The artefact's swept values are defaults, and configuration
   overrides them through `TierSettings` (AC-26), which is how CORE-AC-12
   reaches this tier.
5. **Missing behaviour.** A disabled artefact (AC-23), too few classes (AC-24),
   and an artefact class outside the taxonomy (AC-25).
6. **Tooling.** The import-linter contract and `pytest-benchmark` are replaced
   by an AST import test and timing assertions, so no extra dependencies are
   needed.

## Non-functional

- **NGRAM-AC-21** Scoring one document SHALL take under 50 ms at p95 on a single
  vCPU, with the artefact already loaded.
- **NGRAM-AC-22** Loading the artefact SHALL take under 1 s for up to 200
  classes at the default `max_features` (20,000), and the artefact SHALL be
  under 50 MB.
