# Design: Titan embeddings, nearest class

- **Status:** Implemented (2026-10-02)
- **Requirements:** [requirements.md](requirements.md)

## Components

```
docclass/methods/titan_nn/
  embedder.py   Embedder protocol; BedrockTitanEmbedder; FakeEmbedder
  emb_cache.py  EmbeddingCache protocol; InMemory / store-backed
  index.py      build_index(), ClassIndex.load(), ClassIndex.score()
  calibrate.py  isotonic fit + apply (numpy), threshold sweep
  tier.py       TitanNearestTier (Classifier)
```

## Embedder

```python
class Embedder(Protocol):
    model_id: str
    dimensions: int
    def embed(self, text: str) -> np.ndarray: ...   # unit-norm float32 [dimensions]
```

**`BedrockTitanEmbedder`** calls `bedrock-runtime` `InvokeModel` with
`modelId = "amazon.titan-embed-text-v2:0"` and the body `{"inputText": text,
"dimensions": D, "normalize": true}`. Allowed `D` values are 256, 512 and
1024, and the default is 512. The botocore config sets `connect_timeout`,
`read_timeout = timeout_ms`, and `retries = {"mode": "standard",
"total_max_attempts": max_retries + 1}` (TITAN-AC-15). botocore's
`max_attempts` key counts *retries*, not calls, so `max_attempts: max_retries
+ 1` would allow one retry too many.

The model's input limit is counted in tokens, not characters. The preprocessing
step truncates to `max_input_chars` (default 20,000), which stays safely below
the limit. Confirm the current limit and price against Bedrock documentation
before production sizing.

**`FakeEmbedder`** hashes character trigrams into `D` buckets and normalises
the result. It is deterministic, and similar texts get similar vectors, which is
enough to test ranking, thresholds and abstention offline (TITAN-AC-17).

## Index

The index is a `.npz` archive (no pickle) containing:

| Key | Content |
|---|---|
| `meta` | JSON: `embedding_model_id`, `dimensions`, `normalize`, `index_version`, `taxonomy_version`, `k`, `tau`, `delta`, `enabled`, class order, skipped classes |
| `vectors` | float32 `[N, D]` exemplar vectors |
| `labels` | int32 `[N]` class index per exemplar |
| `centroids` | float32 `[C, D]` |
| `calib_x`, `calib_y` | isotonic breakpoints |

`index_version` is `sha256(meta without index_version + vectors + labels)[:12]`.
Content addressing gives TITAN-AC-04: any change in exemplars produces a new
version, and an old index file is never rewritten.

## Scoring

```
s     = vectors @ q                         # cosine; all unit-norm
per class c: top-k of s[labels == c] → Σ sᵢ² / Σ sᵢ   # similarity-weighted mean
raw margin = raw[top] − raw[second]         # δ check on raw scores (AC-10)
confidence = calibrate(raw[top])            # τ check on calibrated score
```

Similarities are clamped to `max(s, 0)` before weighting. If every top-k value
is 0, the class score is 0, which avoids dividing by zero.

Classes are padded to the largest exemplar count, so the per-class top-k is one
`np.sort` over a `[C, max_n]` matrix rather than a Python loop.

At 200 × 10 × 512 this is a single 2,000 × 512 matrix-vector product, well
within TITAN-AC-18.

## Calibration

Isotonic regression maps a raw class score to P(correct). It is fitted on the
validation set's top-1 scores, using only NumPy (a pool-adjacent-violators
implementation, ~30 lines), so neither the runtime nor the builder needs
scikit-learn. With fewer than 20 validation documents the fit is meaningless,
so the index is disabled (TITAN-AC-06).

**Thresholds.** `τ` = `settings.threshold("tau", class, default=index.tau)`, and
`δ` and `ρ` likewise (AC-21). The decision order is: index disabled →
insufficient text → embedding unavailable → `raw < ρ` "out of distribution"
(AC-26) → `conf < τ` "low confidence" → `margin < δ` "ambiguous" → accept.
The calibration map gets an anchor point at (0, 0) when the lowest validation
score is above 0 (AC-25). If calibration has fewer than 20 validation documents,
the stored map is the identity clipped to [0, 1], and the index is disabled.

## Embedding cache

Key: `f"emb#{namespace}#{model_id}#{dimensions}#{sha256(prepared)}"`. The namespace
is in the key so that one consumer cannot learn whether another has seen the same text. Value: float32 bytes.
`DynamoDBEmbeddingCache` stores it in a DynamoDB table under the `emb#` key prefix.

## Classification flow

```
text = prepare(doc)                                   # shared preprocess
len(text) < min_chars → abstain("insufficient text")  # AC-16, no API call
q = emb_cache.get(key) or embedder.embed(text) → emb_cache.put
    on failure → abstain("embedding unavailable: …")  # AC-15
scores = index.score(q) → decision rule               # AC-09..11
```

## Bedrock errors

`BedrockTitanEmbedder.embed` raises `EmbeddingUnavailable(cause)`:
`throttled` for `ThrottlingException` / `ServiceQuotaExceededException`
(after botocore's own standard-mode retries), `timeout` for read or connect
timeouts, and `error: <code>` otherwise. The tier turns it into the reason
`embedding unavailable: <cause>` (AC-15).

## Registration

`register(registry, embedder, emb_cache, load_index, namespace)` registers the
name `titan_nn`. The factory calls `load_index(settings)` and builds the tier,
which checks the index against the embedder and the taxonomy (AC-12, 13).
Settings `options` accepted: `k`, `max_pages` (2), `max_input_chars` (20,000)
and `min_chars` (50).

## Test strategy

All unit tests use `FakeEmbedder` and an in-memory cache. A `FailingEmbedder`
raises throttling and timeout errors for TITAN-AC-15. The single real-Bedrock
test (TITAN-AC-19) is gated by an environment variable and needs model access
in the test account.
