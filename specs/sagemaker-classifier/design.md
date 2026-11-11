# Design: SageMaker Serverless classifier

- **Status:** Approved (2026-10-02)
- **Requirements:** [requirements.md](requirements.md)

## Components

```
docclass/methods/sagemaker/
  client.py     EndpointClient protocol; SageMakerRuntimeClient; FakeEndpoint
  contract.py   request builder + response validator          (AC-04..07, 14)
  tier.py       SageMakerTier (Classifier)                     (AC-01..03, 08..13)
training/sagemaker/                                            (not shipped in runtime)
  train.py      fine-tune a distilled encoder
  inference.py  model server handler: text → probabilities only
  evaluate.py   held-out report vs tiers 1 + 2                 (AC-16)
  promote.py    registry gate                                  (AC-15, 17)
infra/
  sagemaker_serverless.*   endpoint config: MemorySizeInMB, MaxConcurrency  (AC-21)
```

## Gating (AC-01..03)

The tier needs to see what earlier tiers scored. The cascade therefore passes a
read-only view of the attempts so far through a context argument:

```python
class SageMakerTier:
    def classify(self, doc, ctx: CascadeContext) -> ClassDecision: ...
```

`CascadeContext` is part of the classifier contract (CORE-AC-22). It is
optional: a tier that does not declare `ctx` is called without it.

The gate passes when `CascadeContext.top_classes(gate_top_k)` (the union of
each earlier attempt's top-k scored classes and its decided class) intersects
`enabled_for`. A tier with no earlier attempts is never gated in,
so tier 3 cannot be configured as the first tier.

## Endpoint contract

**Request** (`application/json`):

```json
{"text": "<prepared text>", "pages": [1, 2], "model_version": "smkr-2026-10-01"}
```

**Response:**

```json
{"probabilities": {"w2": 0.91, "w2c": 0.08, "form_1040": 0.01},
 "model_version": "smkr-2026-10-01"}
```

The validator checks each criterion in order: JSON parses, keys exist and have
the right types (AC-05), values ≥ 0, sum within 1 ± 1e-3, every key is in the
taxonomy or is `OTHER` (AC-06), and the version matches a pin if one is set
(AC-07).

`inference.py` on the model side returns softmax probabilities and nothing
else. It contains no threshold logic (AC-08).

## Client

`SageMakerRuntimeClient` wraps `sagemaker-runtime` `InvokeEndpoint`, with a
botocore config of `read_timeout = endpoint_timeout_ms / 1000` and `retries =
{"mode": "standard", "total_max_attempts": 1}` (botocore counts `max_attempts` as retries, so `total_max_attempts` is the setting that means one call). Retries are done in the tier, so
throttling (retried) can be told apart from model errors (not retried):

| botocore error | Tier action |
|---|---|
| `ThrottlingException`, HTTP 429 | retry ≤ `max_retries`, full-jitter backoff (base 200 ms), then abstain "endpoint throttled" (AC-11) |
| `ReadTimeoutError`, `ConnectTimeoutError` | abstain "endpoint timeout", no retry (AC-12) |
| `ModelError`, 5xx | abstain "endpoint error: <code>", no retry (AC-13) |
| `ValidationError` (4xx, not 429) | abstain "endpoint error: <code>". Also log at error level, because it means a contract bug |

The client translates botocore errors into `EndpointThrottled`,
`EndpointTimeout` and `EndpointFailure(code)`, so the tier's retry logic does
not depend on botocore and `FakeEndpoint` can raise the same three types.

## Tier options

| Option | Default | Meaning |
|---|---|---|
| `enabled_for` | required | Gated classes (AC-23) |
| `gate_top_k` | 3 | How many earlier top classes count for the gate |
| `model_version` | unpinned | Pin (AC-07) |
| `max_retries` | 2 | Throttle retries (AC-11) |
| `backoff_base_ms` | 200 | Full-jitter backoff base |
| `max_payload_bytes` | 4,000,000 | Request limit (AC-14) |
| `max_pages` | 2 | Pages sent |
| `thresholds.tau`, `thresholds.delta` | 0.9, 0.1 | Read through `TierSettings.threshold` (AC-24) |

## Payload size (AC-14)

The tier serialises the request and, if it exceeds `max_payload_bytes`, trims
`text` from the end in UTF-8-safe steps until it fits. It then sets
`truncated = true` in the decision's metadata. The first pages carry the
identifying boilerplate, so trimming from the end loses the least.

## Model and serving constraints

- **Model:** a distilled encoder (for example a 6-layer, ~66M-parameter model),
  fine-tuned for sequence classification over the gated classes plus an
  `OTHER` class. `OTHER` is trained on documents from non-gated classes, so the
  model can say "none of mine". The tier maps `OTHER` to an abstention.
- **Serverless config:** `MemorySizeInMB` 4096 (the cap is 6144) and
  `MaxConcurrency` set explicitly per expected burst, built by
  `training/sagemaker/promote.serverless_config` (AC-21). CPU only.
- **Cold starts:** expected to take seconds. The default `timeout_ms` of 8,000
  absorbs a typical cold start, and an `endpoint timeout` abstention covers the
  rest.

## Promotion flow (AC-15..17)

```
train → evaluate (held-out, same split as tiers 1/2) → report → registry
promote(model_version):
  report missing → refuse                                     AC-15
  for c in proposed enabled_for:
      if precision_smkr(c, τ₃) ≤ max(precision_t1(c), precision_t2(c)): drop c   AC-17
  write enabled_for + τ₃/δ₃ into the cascade config proposal (reviewed change)
```

Promotion produces a configuration proposal for review. It never edits live
configuration directly.

## Test strategy

`FakeEndpoint(responses=…, raises=…, sleeps=…)` covers Stories 1–4. The
promotion logic is tested with synthetic reports. The single real-endpoint
contract test (AC-19) is gated by an environment variable.
