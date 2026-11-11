# 0001. Classifier cascade and shared contract

- **Status:** Accepted (2026-10-02)
- **Date:** 2026-10-02
- **Spec:** [../specs/classifier-contract/](../specs/classifier-contract/)
- **Related:** 0003, 0004, 0005

## Context

Documents must be assigned to one of a closed set of classes before fields can
be extracted. The available methods differ by orders of magnitude in cost,
latency, training needs and accuracy:

- a hash lookup costs nothing but only recognises documents seen before;
- a small in-process model costs nothing per call but needs labelled data;
- an embedding service needs almost no training but charges per call;
- a hosted trained model is the most accurate and has the most to operate.

No single method is best on every axis. Every method sometimes faces a document
it cannot classify reliably: a class it was never trained on, a degraded scan,
or a pair of near-identical form editions. A confident wrong class is the worst
outcome, because extraction then reads plausible-looking values off the wrong
form, and nothing downstream notices.

## Decision

1. **Classify through a cascade.** Tiers are ordered by cost. Each runs only if
   every cheaper tier abstained. The first accepted decision wins.
2. **Every tier implements one contract**: `classify(DocumentText) ->
   ClassDecision`. A decision carries `class_id`, a calibrated `confidence`
   in 0..1, `accepted`, `method`, `model_version`, top-k `scores` and, on
   abstention, a `reason`.
3. **Abstention is a first-class outcome.** Each tier applies its own
   thresholds: a minimum confidence `τ`, a minimum top-two margin `δ`, and an
   optional out-of-distribution check. A tier that cannot meet them returns
   `accepted = false`, never a forced guess.
4. **All tiers abstaining resolves to `UNKNOWN`**, a routable result for human
   review, not an error.
5. **Confidence is calibrated in every tier**, so thresholds mean the same
   thing whichever tier produced the score. A raw similarity is not a
   probability and must pass through a fitted calibration first.
6. **The cascade is configuration**: the tier order, the enabled tiers and the
   thresholds (per tier, optionally per class) are data, not code.
7. **Every attempt is recorded**, abstentions included, along with the
   taxonomy version and the input hash.
8. **Text is acquired once**, before the cascade, and all tiers share the
   same `DocumentText`.

## Alternatives considered

| Alternative | Why it lost |
|---|---|
| A single best model for every document | Pays the top tier's cost and latency on every document, including the large share a hash or a small model resolves for free |
| Run all tiers and vote | Removes the cost advantage entirely. Kept only as an optional *agreement mode* for classes that are expensive to get wrong |
| Each tier returns its own native score | Thresholds stop being comparable across tiers, and reordering tiers silently changes accuracy |
| Force a class when nothing is confident ("best guess") | Produces confident-looking wrong extractions. `UNKNOWN` plus review is cheaper than finding those errors later |

## Consequences

- **Positive.** Average cost per document tracks the cheapest tier able to
  answer. Tiers can be added, removed or reordered without changing callers.
  Every decision can be explained and replayed.
- **Negative.** Each tier needs a calibration step and a threshold sweep on
  labelled data before it can be enabled (`docs/overview.md` §6). A document
  that falls through every tier pays every tier's latency.
- **Neutral.** Human review is part of the design, not a failure path. Its
  volume, the `UNKNOWN` rate, is a metric to tune.

## Revisit when

- One tier resolves nearly every document and the others add cost without
  coverage: collapse the cascade.
- Latency on documents that fall through every tier breaches its target: run
  the expensive tiers in parallel instead of in sequence.
