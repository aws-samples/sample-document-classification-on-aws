# Requirements: Titan embeddings, nearest class

- **Status:** Implemented (2026-10-02). Every criterion has a passing test,
  except TITAN-AC-19, whose test is written but skipped until `BEDROCK_IT=1`.
- **ADR:** [0004](../../adr/0004-titan-embedding-nearest-class.md)
- **Prefix:** `TITAN`
- **Depends on:** classifier-contract

## Story 1: build an index from a few examples

As a **model owner**, I want to make a class recognisable from a handful of
exemplars without training a model.

- **TITAN-AC-01** WHEN given exemplars `(DocumentText, class_id)` THE index
  builder SHALL embed each one and store, per class, its exemplar vectors and an
  L2-normalised centroid.
- **TITAN-AC-02** THE index SHALL record `embedding_model_id`, `dimensions`,
  `normalize`, `index_version` and `taxonomy_version`.
- **TITAN-AC-03** IF a class has fewer than `min_exemplars` (default 1)
  exemplars THEN THE builder SHALL leave it out of the index and report it.
- **TITAN-AC-04** WHEN exemplars are added to or removed from a class THE
  builder SHALL produce a new `index_version` and SHALL NOT modify the previous
  index.

## Story 2: calibrated, comparable confidence

- **TITAN-AC-05** THE builder SHALL fit a monotonic calibration from
  similarity to probability on a labelled validation set, and SHALL store it in
  the index.
- **TITAN-AC-06** IF the validation set has fewer than 20 documents THEN THE
  builder SHALL mark the index `enabled = false`.
- **TITAN-AC-07** THE builder SHALL choose `τ` for a target precision using
  the method of NGRAM-AC-08: sweep `τ` over [0.50, 0.99] in steps of 0.01 on
  the validation set's calibrated top-1 scores, with `δ` fixed (default 0.02),
  and keep the smallest `τ` whose selective precision meets the target.

## Story 3: classify, or abstain

- **TITAN-AC-08** WHEN classifying THE tier SHALL embed the prepared text
  (NGRAM-AC-02 normalisation, first `max_pages` pages, truncated to
  `max_input_chars`) with the index's model id and dimension.
- **TITAN-AC-09** THE tier SHALL score each class as the similarity-weighted
  mean of its top-`k` exemplar cosine similarities (default k = 3, or fewer if
  the class has fewer exemplars).
- **TITAN-AC-10** WHEN the calibrated top score ≥ `τ` and the raw margin (top
  class score minus runner-up score, before calibration) ≥ `δ` THE tier SHALL
  return an accepted decision with `method = "titan_nn"`, `confidence` = the
  calibrated top score, and `model_version` = the `index_version`.
- **TITAN-AC-11** IF the calibrated top score < `τ` THEN THE tier SHALL abstain
  with `reason = "low confidence"`. IF the raw margin < `δ` THEN it SHALL abstain
  with `reason = "ambiguous: <a> vs <b>"`.
- **TITAN-AC-12** IF the index's `embedding_model_id` or `dimensions` differ from
  the tier's configured embedding client THEN loading SHALL raise
  `ConfigError`.
- **TITAN-AC-13** IF the index's `taxonomy_version` differs from the cascade's,
  or the index names a class outside the cascade's taxonomy, THEN loading SHALL
  raise `ConfigError`.

## Story 4: never pay twice, never hang

- **TITAN-AC-14** WHEN the same prepared text is classified again THE tier SHALL
  reuse the cached embedding, keyed by `(namespace, model_id,
  dimensions, sha256(prepared text))`, and SHALL NOT call Bedrock.
- **TITAN-AC-15** IF the Bedrock call fails, is throttled, or exceeds
  `timeout_ms` THEN THE tier SHALL abstain with `reason = "embedding
  unavailable: <cause>"` after at most `max_retries` (default 2) retries with
  jittered backoff.
- **TITAN-AC-16** IF the prepared text has fewer than `min_chars` characters THEN
  THE tier SHALL abstain with `reason = "insufficient text"` without calling
  Bedrock.

## Story 5: testable offline

- **TITAN-AC-17** THE tier SHALL take its embedding client as an injected
  `Embedder` protocol, so every criterion except TITAN-AC-19 can be verified
  with a deterministic fake.

- **TITAN-AC-20** IF no `τ` in the sweep reaches the target precision THEN THE
  builder SHALL mark the index `enabled = false` and say so in the report.
- **TITAN-AC-21** WHERE the cascade configuration sets `tau` or `delta` for this
  tier (default or per class), THE tier SHALL use it in place of the value
  stored in the index, read through `TierSettings.threshold(key, class_id)`.
- **TITAN-AC-22** WHILE the loaded index has `enabled = false`, THE tier SHALL
  abstain with `reason = "index disabled"` without calling Bedrock.
- **TITAN-AC-23** IF the embedding cache fails on read or write THEN THE tier
  SHALL treat it as a miss (read) or ignore it (write), and SHALL still
  classify.
- **TITAN-AC-24** IF a validation document's `doc_hash` is also an exemplar's
  THEN THE builder SHALL raise `ValueError`, because the calibration would be
  fitted on documents the index already contains.
- **TITAN-AC-25** THE calibration SHALL map every input to [0, 1], and SHALL be
  non-decreasing in the raw score. Below the lowest validation score it SHALL
  fall linearly to 0 at a raw score of 0, rather than holding its lowest fitted
  value.
- **TITAN-AC-26** THE builder SHALL store `rho`, the 1st percentile of the raw
  top-1 scores of correctly classified validation documents, and IF a
  document's raw top score < `rho` (or the configured `rho`) THEN THE tier
  SHALL abstain with `reason = "out of distribution"`.

## Review log

2026-10-02, review against the implemented contract:

1. **The margin moved to raw scores** (AC-10, 11). Isotonic calibration is
   piecewise constant, so two classes on one plateau get a calibrated margin of
   0 however far apart their raw similarities are. `τ` stays on the calibrated
   score, and `δ` defaults to 0.02 because Titan cosine similarities cluster
   tightly.
2. **AC-07 now states the sweep itself** rather than only citing NGRAM-AC-08,
   and AC-20 adds the "no `τ` reaches the target" outcome that NGRAM-AC-09 has.
3. **Precedence between config and index thresholds** was undefined (AC-21).
4. **A disabled index** (AC-06, 20) was never consulted at classification time
   (AC-22).
5. **Embedding-cache failure** could have failed the tier (AC-23). The cache is
   an optimisation.
6. **Calibration leakage**: validation documents that are also exemplars score
   ~1.0 against themselves (AC-24).
7. **Calibration range and monotonicity** were stated only in prose (AC-25).
   AC-13 now also rejects an index whose classes fall outside the taxonomy,
   since an accepted decision for such a class would fail CORE-AC-04.
8. `model_version` on a decision is the `index_version` (AC-10).
9. **Found while implementing: out-of-distribution documents.** With a
   validation set the index classifies perfectly, isotonic calibration is 1.0
   at every observed score, and plain clamping extends that 1.0 to every lower
   score too, so a document unlike any class was accepted at full confidence.
   AC-25 now anchors the low end at (0, 0), and AC-26 adds a raw-similarity
   floor `rho`, the same guard NGRAM-AC-17 gives tier 1.
10. **Found while implementing: retry count.** The design set botocore
    `retries.max_attempts = max_retries + 1`, but botocore reads `max_attempts`
    as retries excluding the first call, so it allowed 3 retries, not 2. The
    design now uses `total_max_attempts`.

## Non-functional

- **TITAN-AC-18** Scoring a document against the index, with the embedding
  already computed, SHALL take under 10 ms at p95 for 200 classes × 10
  exemplars.
- **TITAN-AC-19** An integration test SHALL embed a fixed string through the
  real Bedrock model and assert the vector's dimension and unit norm. It runs
  only when the `BEDROCK_IT=1` variable is set.
