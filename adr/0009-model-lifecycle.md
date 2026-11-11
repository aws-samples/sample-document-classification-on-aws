# 0009. Model lifecycle: train, register, promote by reviewed config

- **Status:** Accepted (2026-10-02)
- **Date:** 2026-10-02
- **Spec:** [../specs/platform/](../specs/platform/)
- **Related:** 0003, 0004, 0005, 0007

## Context

Three tiers have learned artefacts (the n-gram model, the Titan index and the
SageMaker model), each per namespace, each retrained as labels accumulate. If
an artefact can reach production without evidence, accuracy can regress
silently. If promotion is manual and ad hoc, nobody can say which model made a
given decision.

## Decision

1. **Labelled data lives in S3** at `labelled/{namespace}/`. It is fed by
   `POST /v1/labels`, by review outcomes, and by bulk uploads.
2. **Training runs as SageMaker Training Jobs**, using the scikit-learn
   container for tier 1 and the Hugging Face container for tier 3. The Titan
   index build (tier 2) runs in a Lambda, or a Processing job for large
   exemplar sets. A Step Functions *training* workflow per namespace runs
   them on a schedule, or when N new labels arrive.
3. **Every artefact is registered in the SageMaker Model Registry**, under a
   package group per namespace and tier, with its evaluation report attached.
   An artefact without a report cannot be registered.
4. **Promotion is a reviewed configuration change.** Approving a model package
   triggers a job that writes a *proposed* AppConfig version (artefact
   version + swept thresholds). A human approves it, and AppConfig deploys it
   with a gradual rollout and automatic rollback on the `UnknownRate` alarm.
5. **Artefacts are immutable and versioned.** Configuration names exact
   versions. "latest" is never used.

## Alternatives considered

| Alternative | Why it lost |
|---|---|
| Auto-promote when metrics improve | A metric can improve on the validation set and still regress on traffic the set does not represent. Keep a human in the loop until there is trust |
| SageMaker Pipelines for all training | Adds a second orchestrator next to Step Functions. Revisit if training workflows grow beyond a few steps |
| Artefacts bundled into the Lambda package | Every retrain would need a code deployment, and artefacts could not vary per namespace |

## Consequences

- **Positive.** Every decision traces back to a registered artefact and its
  evidence. Rollback is an AppConfig revert.
- **Negative.** Promotion involves a human step. A namespace with frequent
  retrains needs a responsive reviewer.

## Revisit when

- Promotion reviews become routine approvals: automate them for tier 1, behind
  a stricter metric gate.
