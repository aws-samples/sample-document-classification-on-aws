# Design: Char n-gram TF-IDF + logistic regression

- **Status:** Approved (2026-10-02)
- **Requirements:** [requirements.md](requirements.md)

## Components

```
docclass/methods/char_ngram_lr/
  preprocess.py   prepare(doc, max_pages) -> str       shared by train + score
  train.py        train(dataset, cfg) -> (sk_model, report)        [sklearn]
  export.py       export(sk_model, ...) -> artefact file           [sklearn]
  scorer.py       Scorer.load(path); Scorer.proba(text)            [numpy only]
  tier.py         CharNgramTier (Classifier)                       [numpy only]
```

Train-time code (`train.py`, `export.py`) installs as the extra
`docclass[train]`. The runtime imports only `preprocess`, `scorer` and `tier`
(NGRAM-AC-11), and an AST import test enforces this.

## Preprocessing (shared)

```
text = " ".join(p.text for p in doc.pages[:max_pages])
text = normalise_text(text)                          # NFKC, lower, collapse ws
text = " ".join(t for t in text.split() if not t.isdigit())
```

Training and scoring call the same `prepare()`. A difference between the two
would show up as a parity failure in NGRAM-AC-12.

## Training

```python
vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), lowercase=False,
                      sublinear_tf=True, min_df=2, max_features=20_000,
                      dtype=np.float64)
lr  = LogisticRegression(C=10, max_iter=2000, class_weight="balanced")
cal = CalibratedClassifierCV(FrozenEstimator(lr), method="sigmoid")   # if calibrating
```

- **Split** (NGRAM-AC-05): each `doc_hash` is assigned to exactly one side,
  per class, deterministically from `seed`. The sides are train/val 80/20, or
  train/calibration/val 60/20/20 when calibrating (NGRAM-AC-04).
- **Exclusion** (NGRAM-AC-03, 24): classes with fewer than `min_per_class`
  documents are dropped and reported. Fewer than two remaining classes raises
  `TrainingError`.
- **Centroids** (NGRAM-AC-06): the mean training vector per class,
  L2-normalised.
- **Threshold sweep** (NGRAM-AC-08): over `τ ∈ [0.50, 0.99]` in steps of 0.01,
  with `δ` fixed (default 0.10) and `ρ` set to the 1st percentile of the true
  class's centroid similarity on the training data. Pick the smallest `τ`
  meeting the target precision. If none does, record the `τ` with the best
  precision and set `enabled = false` (NGRAM-AC-09).

## Artefact format

A single `.npz` file (NumPy archive, loaded with `allow_pickle=False`)
containing:

| Key | Shape / type |
|---|---|
| `meta` | JSON string: `model_version`, `taxonomy_version`, `ngram_range`, `max_pages`, `thresholds`, `enabled`, `calibrated`, `classes` |
| `vocab_keys`, `vocab_idx` | unicode n-grams, int32 column indices |
| `idf` | float64 `[V]` |
| `coef`, `intercept` | float64 `[K, V]`, `[K]`, where K = 1 for two classes, else C |
| `calib_a`, `calib_b` | float64 `[K]` sigmoid parameters (empty if uncalibrated) |
| `cent_data`, `cent_indices`, `cent_indptr` | CSR triplets of the `[C, V]` centroids |

`model_version` defaults to `ngram-` + the first 12 hex digits of the SHA-256
of the arrays.

## Scoring

```
grams  = char_wb_ngrams(text, 3, 5)          # re-implements sklearn's char_wb exactly
tf     = counts over vocab → 1 + log(tf)     # sublinear
x      = tf * idf ; x /= ||x||₂
z      = coef @ x + intercept
p      = softmax(z)                           uncalibrated, C > 2
       = [1 − σ(z), σ(z)]                     uncalibrated, C = 2
       = expit(−(a·z + b)) per class, ÷ Σ     calibrated, C > 2
       = [1 − p₁, p₁], p₁ = expit(−(a·z + b)) calibrated, C = 2
sim    = centroids[top] · x
```

`char_wb_ngrams` mirrors scikit-learn's `_char_wb_ngrams`: it pads each
whitespace token with one space on both sides and emits n-grams within the
padded token. A word shorter than n is emitted once. A test fixes the exact
output against scikit-learn's analyser on a reference string.

## Decision rule

The checks run in this order, and the first failing one gives the reason:

0. artefact disabled → "tier disabled" (AC-23)
1. text shorter than `min_chars` → "insufficient text" (AC-18)
2. `p_top < τ_class` → "low confidence" (AC-15, 19, 26)
3. `p_top − p_2nd < δ` → "ambiguous: a vs b" (AC-16)
4. `sim < ρ` → "out of distribution" (AC-17)
5. otherwise accept (AC-14)

Thresholds are read as `settings.threshold(key, top_class, default=artefact
value)` (AC-26). The tier loads its artefact once, in its factory. A
`taxonomy_version` mismatch (AC-13), or an artefact class outside the
taxonomy (AC-25), raises `ConfigError` there.

## Test strategy

- **Fixtures:** synthetic form-like corpora (fixed boilerplate per class plus
  variable fields) built in the tests, plus a smoke test over the public
  sample forms when available. A noise generator (substitution, deletion,
  `rn↔m`, `0↔O`, `1↔l`) serves NGRAM-AC-20.
- **Parity:** the validation set plus random strings, for NGRAM-AC-12.
- **Benchmarks** for AC-21 and AC-22 are plain timing assertions.
