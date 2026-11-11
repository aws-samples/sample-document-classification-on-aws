# Design: Classifier contract and cascade

- **Status:** Approved (2026-10-02)
- **Requirements:** [requirements.md](requirements.md)

## Module layout

```
docclass/
  core/
    types.py       DocumentText, PageText, ClassDecision, CascadeResult, UNKNOWN
    protocol.py    Classifier protocol
    errors.py      InvalidDecisionError, ConfigError
    config.py      CascadeConfig loader + validation
    cascade.py     Cascade
```

`core/` imports only the standard library (CORE-AC-20).

## Types

```python
UNKNOWN: Final = "UNKNOWN"

@dataclass(frozen=True)
class PageText:
    number: int                     # 1-based
    text: str
    source: Literal["text_layer", "ocr"]

@dataclass(frozen=True)
class DocumentText:
    doc_hash: str                   # sha256 hex of source bytes
    pages: tuple[PageText, ...]

@dataclass(frozen=True)
class ClassDecision:
    class_id: str
    confidence: float
    accepted: bool
    method: str
    model_version: str
    taxonomy_version: str
    scores: Mapping[str, float]     # top-k, k ≤ 5
    reason: str | None = None

    def __post_init__(self): ...    # validation for CORE-AC-03/04/05

@dataclass(frozen=True)
class TierAttempt:
    tier: str
    decision: ClassDecision | None
    error: str | None
    elapsed_ms: float

@dataclass(frozen=True)
class CascadeResult:
    decision: ClassDecision         # the winner, or the UNKNOWN decision
    attempts: tuple[TierAttempt, ...]
    doc_hash: str
    taxonomy_version: str
    config_version: str
```

`ClassDecision.__post_init__` validates the confidence range and the reason
rule (CORE-AC-03, 05). It cannot check taxonomy membership (CORE-AC-04),
because a dataclass has no taxonomy to check against. Tiers therefore build
decisions through `DecisionFactory(taxonomy)`, which the cascade hands to each
tier at construction and which performs that check. There is no global mutable
taxonomy.

## Protocol

```python
class Classifier(Protocol):
    name: str
    def classify(self, doc: DocumentText) -> ClassDecision: ...
```

Structural typing satisfies CORE-AC-01: tiers do not inherit from a base class.

## Cascade algorithm

```python
def classify(self, doc) -> CascadeResult:
    attempts, pending = [], None            # pending: first accept awaiting agreement
    for tier in self.tiers:                 # CORE-AC-06
        try:
            d = run_with_timeout(tier.classify, doc, self.cfg.timeout_for(tier))
        except Timeout:
            attempts.append(abstain(tier, "timeout")); continue      # AC-10
        except Exception as e:
            attempts.append(abstain(tier, f"error: {type(e).__name__}")); continue  # AC-09
        attempts.append(TierAttempt(tier.name, d, None, ms))
        if not d.accepted:
            continue
        if not self.cfg.requires_agreement(d.class_id):
            return self._result(d, attempts)                         # AC-07
        if pending is None:
            pending = d; continue                                    # AC-17
        if pending.class_id == d.class_id:
            return self._result(pending, attempts)
        return self._result(unknown("tier disagreement"), attempts)  # AC-18
    return self._result(unknown(join_reasons(attempts)), attempts)   # AC-08
```

**Context (CORE-AC-22).** Arity is inspected once at construction with
`inspect.signature`. Two-parameter tiers receive `CascadeContext(attempts)`, a
tuple snapshot, so a tier cannot mutate the cascade's own record.

**Agreement exhausted (CORE-AC-17).** When the loop is exhausted with
`pending` set, the reason is `agreement not reached: <class>`.

**Timeouts.** With no timeout configured, the tier is called directly
(CORE-AC-10). With one, the call runs on a fresh daemon thread, and the cascade
waits on `future.result(timeout=…)`. A tier that times out
is abandoned, not killed. Tiers that make network calls must also set their own
client timeouts, so abandoned threads do not pile up.

## Configuration

```json5
// shown as JSON5 for comments; the loader takes strict JSON or a dict
config_version: "2026-10-02.1"
taxonomy_version: "tx-7"
classes: [w2, w2c, form_1040, ...]
tiers:
  - name: char_ngram_lr
    thresholds: { tau: 0.90, delta: 0.15, rho: 0.30 }
    per_class:  { w2c: { tau: 0.97 } }            # CORE-AC-12
  - name: titan_nn
    timeout_ms: 2000
    thresholds: { tau: 0.85, delta: 0.10 }
  - name: sagemaker
    timeout_ms: 8000
    enabled_for: [w2, w2c]                        # gated adoption, ADR-0005
    thresholds: { tau: 0.92, delta: 0.15 }
agreement: [form_1120s]                           # CORE-AC-17
```

Tier names resolve through a `TierRegistry` (`name → factory(settings,
decisions) -> Classifier`). Each factory receives:

- **`TierSettings`**: `name`, `timeout_ms`, `threshold(key, class_id=None)`
  (per-class value if one is configured, else the tier default, CORE-AC-12),
  and `options`, holding the tier's other keys (for example `enabled_for`)
  uninterpreted;
- **`DecisionFactory`**: bound to the taxonomy, for building decisions
  (CORE-AC-04).

Load-time validation (CORE-AC-13) covers: an unknown or duplicate tier name, a
threshold outside [0, 1], a non-positive `timeout_ms`, and `per_class` or
`agreement` classes not in `classes`.

## Error handling

| Situation | Result |
|---|---|
| Invalid decision built by a tier | `InvalidDecisionError` is raised in the tier, caught by the cascade, and recorded as an `error:` abstention (AC-09) |
| Invalid configuration | `ConfigError` at load. The cascade is never constructed |
| All tiers error | `UNKNOWN` with every error listed in `reason` |

## Test strategy

Use fake tiers (`FakeTier(returns=…, raises=…, sleeps=…)`) for every
cascade criterion. No real tier is needed to verify this spec.
