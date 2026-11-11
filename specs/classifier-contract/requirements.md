# Requirements: Classifier contract and cascade

- **Status:** Implemented (2026-10-02)
- **ADR:** [0001](../../adr/0001-classifier-cascade-and-contract.md)
- **Prefix:** `CORE`

## Glossary

| Term | Meaning |
|---|---|
| Tier | One classifier in the cascade |
| Decision | A `ClassDecision` returned by a tier |
| Accepted | A decision that passed its tier's thresholds |
| Taxonomy | The closed set of class ids in force, identified by `taxonomy_version` |

## Story 1: one contract for every tier

As a **pipeline maintainer**, I want every classifier to share one interface
and one result type, so that tiers can be added, removed or reordered without
changing callers.

- **CORE-AC-01** THE cascade SHALL accept as a tier any object exposing `name`
  and `classify(DocumentText) -> ClassDecision`.
- **CORE-AC-02** THE `ClassDecision` SHALL carry `class_id`, `confidence`,
  `accepted`, `method`, `model_version`, `taxonomy_version`, `scores` and
  `reason`.
- **CORE-AC-03** IF a decision's `confidence` lies outside the closed interval
  [0, 1] THEN constructing it SHALL raise `InvalidDecisionError`.
- **CORE-AC-04** IF a decision has `accepted = true` and its `class_id` is
  `UNKNOWN` or is not in the taxonomy THEN constructing it SHALL raise
  `InvalidDecisionError`.
- **CORE-AC-05** IF a decision has `accepted = false` THEN it SHALL carry a
  non-empty `reason`.

## Story 2: cheapest confident answer wins

As an **operator**, I want the cheapest tier able to classify a document
confidently to be the one that does, so that cost tracks difficulty.

- **CORE-AC-06** WHEN `classify` is called THE cascade SHALL invoke the tiers
  in configured order, one at a time.
- **CORE-AC-07** WHEN a tier returns an accepted decision THE cascade SHALL
  return that decision and SHALL NOT invoke any later tier.
- **CORE-AC-08** WHEN every tier abstains THE cascade SHALL return a decision
  with `class_id = UNKNOWN`, `accepted = false`, `method = "cascade"`,
  `model_version` equal to the configuration version, and a `reason` listing
  each tier's reason as `<tier>: <reason>`.
- **CORE-AC-09** IF a tier raises an exception THEN THE cascade SHALL record it
  as an abstention with `reason = "error: <type>"` and SHALL continue with the
  next tier.
- **CORE-AC-10** WHERE a tier's per-call timeout is configured, IF the tier
  exceeds it THEN THE cascade SHALL record an abstention with `reason =
  "timeout"` and continue. WHERE no timeout is configured, THE cascade SHALL
  call the tier directly, on the calling thread.

## Story 3: configuration, not code

As a **pipeline maintainer**, I want the tier order and thresholds to be data,
so that tuning does not need a code change.

- **CORE-AC-11** THE cascade SHALL build its tier order, the enabled tiers and
  each tier's thresholds from a configuration document: a JSON object, or the
  equivalent mapping.
- **CORE-AC-12** WHERE a per-class threshold is configured for a tier,
  `TierSettings.threshold(name, class_id)` SHALL return it in place of the tier
  default for that class, so a tier that reads its thresholds through
  `TierSettings` applies it.
- **CORE-AC-13** IF the configuration names an unknown tier, names a tier
  twice, has a threshold outside [0, 1], a non-positive `timeout_ms`, or a
  `per_class` or `agreement` class not in `classes`, THEN loading it SHALL
  raise `ConfigError` before any document is classified.

## Story 4: every decision is explainable

As an **auditor**, I want every attempt recorded, so that I can explain and
replay any classification.

- **CORE-AC-14** WHEN the cascade finishes THE result SHALL include the ordered
  list of every tier attempt (decision or error), not only the winner.
- **CORE-AC-15** THE result SHALL record `doc_hash`, `taxonomy_version` and the
  configuration version used.
- **CORE-AC-16** WHEN the same `DocumentText` is classified twice with the
  same configuration, the same model versions, and tiers that return the same
  decisions, THE cascade SHALL return the same `class_id` and `accepted`
  value. (Timeouts and errors are environmental and outside this guarantee.)

## Story 5: optional agreement mode

As a **risk owner**, I want high-stakes classes confirmed by two independent
tiers.

- **CORE-AC-17** WHERE agreement mode is enabled for a class, WHEN a tier
  accepts that class THE cascade SHALL accept it only once a second, different
  tier also accepts the same class. Otherwise it SHALL continue down the
  cascade. IF the cascade is exhausted with no second acceptance, THEN it
  SHALL return `UNKNOWN` with `reason = "agreement not reached: <class>"`.
- **CORE-AC-18** WHERE agreement mode is enabled, IF two tiers accept different
  classes THEN THE cascade SHALL return `UNKNOWN` with `reason = "tier
  disagreement"`.

## Story 6: tiers may see earlier attempts

As the **author of a gated tier** (SMKR-AC-01), I want to see what earlier
tiers scored.

- **CORE-AC-22** WHERE a tier's `classify` accepts a second parameter, THE
  cascade SHALL pass it a read-only `CascadeContext` holding the attempts made
  so far. A tier that declares only `classify(doc)` SHALL be called without
  it.

## Review log

Issues found in review on 2026-10-02 and folded in above: (1) YAML config vs
the stdlib-only rule, so JSON is used (AC-11); (2) the agreement-exhausted
outcome was undefined (AC-17); (3) the gated tier needed context (AC-22,
previously SMKR task 0); (4) a thread for every tier call even without a
timeout (AC-10); (5) the UNKNOWN decision's `model_version` was undefined
(AC-08); (6) determinism claimed under timeouts (AC-16); (7) no mechanism for
per-class thresholds (AC-12).

## Non-functional

- **CORE-AC-19** THE cascade's own overhead, excluding tier time, SHALL be
  under 5 ms at p95 for a 4-tier configuration.
- **CORE-AC-20** THE contract module SHALL have no dependency on any cloud SDK.
