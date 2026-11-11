# Requirements: SageMaker Serverless classifier

- **Status:** Implemented (2026-10-02; SMKR-AC-19 is written but runs only with SAGEMAKER_IT=1)
- **ADR:** [0005](../../adr/0005-sagemaker-serverless-classifier.md)
- **Prefix:** `SMKR`
- **Depends on:** classifier-contract

## Story 1: gated adoption

As an **operator**, I want tier 3 used only where cheaper tiers have been shown
to fall short, so that its cost and operating burden stay bounded.

- **SMKR-AC-01** THE tier SHALL call the endpoint only for documents where a
  class in the configured `enabled_for` set is among the earlier tiers' top
  `gate_top_k` (default 3) scored or decided classes.
- **SMKR-AC-02** IF no earlier tier scored any class in `enabled_for` THEN THE
  tier SHALL abstain with `reason = "not gated"` without calling the endpoint.
- **SMKR-AC-03** THE tier SHALL accept a decision only for a class in
  `enabled_for`. Any other top class SHALL produce an abstention with `reason =
  "outside gated set"`.

## Story 2: a strict endpoint contract

- **SMKR-AC-04** THE client SHALL send `{"text", "pages", "model_version"}` as
  JSON, with `model_version` omitted when unpinned.
- **SMKR-AC-05** THE client SHALL accept a response only if it is JSON with
  `probabilities` (a map from class id to number) and `model_version` (a
  string).
- **SMKR-AC-06** IF the probabilities are negative, sum to anything outside
  1 ± 1e-3, or name a class that is neither in the taxonomy nor the reserved
  `OTHER` THEN THE tier SHALL abstain with `reason = "invalid response"`.
- **SMKR-AC-07** IF the response's `model_version` differs from a pinned
  `model_version` THEN THE tier SHALL abstain with `reason = "model version
  mismatch"`.
- **SMKR-AC-08** THE endpoint SHALL return probabilities only. Acceptance SHALL
  be decided by the tier, using `τ₃` and `δ₃` from the cascade configuration.

## Story 3: classify, or abstain

- **SMKR-AC-09** WHEN the top probability ≥ `τ₃` and the margin ≥ `δ₃` THE tier
  SHALL return an accepted decision with `method = "sagemaker"` and the
  response's `model_version`.
- **SMKR-AC-10** IF the thresholds are not met THEN THE tier SHALL abstain with
  `reason = "low confidence"` or `"ambiguous: <a> vs <b>"`.

## Story 4: endpoint failure never fails the document

- **SMKR-AC-11** IF the endpoint returns a throttling error (HTTP 429 /
  `ThrottlingException`) THEN THE tier SHALL retry at most `max_retries`
  (default 2) times with jittered backoff, then abstain with `reason =
  "endpoint throttled"`.
- **SMKR-AC-12** IF the call exceeds `timeout_ms` (default 8,000, to allow for
  a cold start) THEN THE tier SHALL abstain with `reason = "endpoint timeout"`.
- **SMKR-AC-13** IF the endpoint returns a model error (`ModelError`, any 5xx)
  THEN THE tier SHALL abstain with `reason = "endpoint error: <code>"` and SHALL
  NOT retry.
- **SMKR-AC-14** THE request body SHALL be truncated so the payload stays under
  the serverless request limit (4 MB, configurable), and truncation SHALL be
  recorded in the decision's `metadata` as `truncated = true`.

## Story 5: models are promoted on evidence

As a **model owner**, I want a model version to reach the endpoint only with an
evaluation behind it.

- **SMKR-AC-15** THE promotion script SHALL refuse to promote a model version
  with no evaluation report in the model registry.
- **SMKR-AC-16** THE evaluation report SHALL include, for each class in
  `enabled_for`, the precision and coverage at the proposed `τ₃`, compared
  against tiers 1 and 2 on the same held-out set.
- **SMKR-AC-17** IF the candidate does not beat tiers 1 and 2 on selective
  precision for a gated class THEN THE promotion script SHALL remove that
  class from the proposed `enabled_for`.

## Story 6: testable offline

- **SMKR-AC-18** THE tier SHALL take its endpoint client as an injected
  `EndpointClient` protocol, so every criterion in Stories 1–4 can be
  verified with a fake.
- **SMKR-AC-19** A contract test SHALL invoke the real endpoint with a fixed
  document and assert the response shape (SMKR-AC-05). It runs only when
  `SAGEMAKER_IT=1` is set.

- **SMKR-AC-22** IF the top class in the response is `OTHER` THEN THE tier SHALL
  abstain with `reason = "other"`.
- **SMKR-AC-23** IF `enabled_for` is missing, empty, or names a class outside the
  taxonomy THEN building the tier SHALL raise `ConfigError`.
- **SMKR-AC-24** THE tier SHALL read `τ₃` and `δ₃` through
  `TierSettings.threshold("tau" | "delta", class_id)`, so per-class overrides
  (CORE-AC-12) apply.

## Review log

Reviewed 2026-10-02 against the implemented contract:

1. AC-01 said "top-scoring class" while the design gated on the top 3. The
   criterion now names `gate_top_k` (default 3) and includes classes earlier
   tiers decided.
2. The model is trained with a reserved `OTHER` class, which the old AC-06
   would have rejected as outside the taxonomy. AC-06 now allows it, and the
   new AC-22 makes it an abstention.
3. AC-14 said "on the decision". `ClassDecision` has no `truncated` field, so
   it goes in `metadata`.
4. AC-21 asked for infrastructure code in a Python spec. The Python half is
   now a validated `serverless_config` helper.
5. Added AC-23 for config validation, which was unspecified.
6. Added AC-24, tying the thresholds to the contract's per-class mechanism.
7. Timeouts: the client's read timeout is the tier option
   `endpoint_timeout_ms` (default 8,000). The cascade's own `timeout_ms`, if
   set, must exceed `endpoint_timeout_ms × (max_retries + 1)` plus backoff, or
   the cascade abandons the tier first and the reason reads `timeout` rather
   than `endpoint throttled`.
8. 2026-10-15: `train.py` no longer defaults to a named pretrained model.
   `--base-model` and `--base-model-revision` are required, so the deployer
   chooses the encoder, pins its exact revision, and reviews its licence.

## Non-functional

- **SMKR-AC-20** Warm-path latency through the tier, excluding model time,
  SHALL add under 20 ms at p95.
- **SMKR-AC-21** THE serverless endpoint configuration SHALL be built by
  `serverless_config(memory_mb, max_concurrency)`, which SHALL reject a missing
  value, a memory size other than 1024–6144 MB in 1024 MB steps, or a
  concurrency outside 1–200.
