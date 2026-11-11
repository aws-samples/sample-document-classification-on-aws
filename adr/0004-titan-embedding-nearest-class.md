# 0004. Titan embeddings, nearest class, as tier 2

- **Status:** Accepted (2026-10-02)
- **Date:** 2026-10-02
- **Spec:** [../specs/titan-nearest-class/](../specs/titan-nearest-class/)
- **Related:** 0001, 0003, 0005

## Context

Tier 1 (ADR-0003) needs tens of labelled examples per class and a retrain. That
leaves two gaps:

- **New classes.** When a class is introduced there may be only one to five
  examples, and it would wait weeks before tier 1 could recognise it.
- **Unusual text.** Short documents, letters, or forms with little boilerplate
  give character n-grams little to work with, while their *meaning* is still
  distinctive.

A pretrained embedding model maps text to vectors where meaning is preserved,
so classes can be recognised by similarity to a handful of examples, with no
training at all.

## Decision

1. **Embed** normalised text, truncated to the model's input limit, with Amazon
   Titan Text Embeddings through Amazon Bedrock. The model id, dimension and
   normalisation are fixed per index and recorded in it.
2. **Index per class:** the exemplar vectors, plus a re-normalised centroid.
   Scoring uses k-nearest-neighbours over exemplars (default k = 3, weighted by
   similarity), so a class with several distinct layouts is not averaged into
   one blurred centroid.
3. **Calibrate** similarity into a probability with a fitted one-dimensional
   mapping (isotonic or Platt), as ADR-0001 §5 requires.
4. **Accept** only when calibrated confidence ≥ `τ₂` and the top-two margin ≥
   `δ₂`.
5. **Cache embeddings by text hash.** The same text is never embedded twice.
6. **Refuse to compare vectors whose model id or dimension differ** from the
   index. A model change means re-embedding every exemplar, never mixing
   vectors from two models.
7. **Adding a class means adding exemplars.** No retrain is needed. The index
   version is bumped, and calibration is re-checked before the class is
   enabled.

## Alternatives considered

| Alternative | Why it lost |
|---|---|
| Wait for enough data for tier 1 | New classes go unrecognised, and fill human review, for weeks |
| An LLM zero-shot prompt ("which of these classes is this?") | Costs far more per call and adds latency. Its output is not a calibrated score, and the answer drifts with prompt wording and model versions |
| A self-hosted open-source embedding model | Another model to host and patch. The cost advantage only appears at volumes a managed service handles anyway |
| A vector database | Unnecessary while the index is classes × a few exemplars, which fits in memory. Revisit at scale |
| Centroid only | Collapses classes that have several layouts. k-NN over exemplars handles them at no extra cost |

## Consequences

- **Positive.** A new class becomes usable from a handful of examples, on the
  same day. Complements tier 1 on text where meaning matters more than
  boilerplate.
- **Negative.** Costs money per embedded token, and adds a network call and a
  Bedrock dependency to the cascade. Weak at separating near-identical editions
  of the same form (same meaning, different version), which is left to tier 3.
  Needs model access in the target account and region.
- **Neutral.** The exemplar set becomes a curated asset, and needs review like
  training data.

## AWS integration

- **Embeddings:** Amazon Bedrock `InvokeModel` on
  `amazon.titan-embed-text-v2:0`. Grant `bedrock:InvokeModel` on that model
  ARN only.
- **Index:** a versioned `.npz` file, held in memory once loaded.
- **Embedding cache:** optional, in memory or as DynamoDB `emb#{namespace}#…`
  items.
- **Quotas:** Bedrock throughput quotas are per account, so callers that share
  an account share them.

## Revisit when

- Embedding spend becomes material next to the review time it saves.
- Tier 1 coverage of new classes catches up quickly enough that tier 2 rarely
  decides anything.
- The index outgrows memory: introduce a vector store, under its own ADR.
