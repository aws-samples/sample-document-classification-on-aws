# 0007. Consumers, namespaces and isolation

- **Status:** Accepted (2026-10-02)
- **Date:** 2026-10-02
- **Spec:** [../specs/platform/](../specs/platform/)
- **Related:** 0006, 0010

## Context

Many teams will use the service, each with different classes, data and volume.
They need:

- their own taxonomy, thresholds and models;
- no visibility into each other's documents or decisions;
- protection from a neighbour's batch eating the shared quotas (Textract,
  Bedrock, SageMaker);
- self-service onboarding, without a new deployment per team.

Some teams, for regulatory or contractual reasons, will need their data in an
account they control.

## Decision

1. **A consumer is a namespace.** Each consumer gets a Cognito app client
   (OAuth2 client-credentials) or an IAM role mapping. The API resolves the
   namespace **only** from the authenticated identity, using a `consumers`
   registry in DynamoDB. A namespace in a request body or path is ignored.
2. **Everything is keyed by namespace:** S3 prefixes, DynamoDB partition keys
   (jobs, embeddings), artefact paths, AppConfig profiles, SageMaker
   endpoints, metric dimensions.
3. **Enforce isolation in IAM, not only in code.** The Classify and Text
   functions do their data access through a `data-access` role assumed with a
   session tag `namespace=<ns>`. The role's policies scope S3 and DynamoDB by
   `${aws:PrincipalTag/namespace}`, so a code bug cannot read another
   namespace's objects.
4. **Fair use by quota.** There is an API Gateway usage plan per consumer, and
   a per-namespace concurrency cap on running executions, enforced by a
   DynamoDB semaphore in the state machine. Account-wide service quotas are
   shared, and the caps keep any one consumer's share of them bounded.
5. **Configuration per namespace in AppConfig**, validated against the cascade
   configuration schema (CORE-AC-13) as an AppConfig validator. A bad
   configuration never deploys.
6. **Pooled by default, siloed on demand.** The same module (ADR-0010) can be
   deployed into a consumer's own account. A silo is a deployment with one
   namespace, and nothing in the code distinguishes the two.

## Alternatives considered

| Alternative | Why it lost |
|---|---|
| One deployment per consumer, always | Multiplies operations and cost, and onboarding then takes an infrastructure change. Kept as the silo option |
| Namespace from a request header | Any caller could claim any namespace |
| Isolation in application code only | A single missing prefix check leaks data. IAM session tags make the boundary enforced by AWS |
| A separate DynamoDB table per consumer | Table count grows with consumers, and partition-key isolation with IAM conditions gives the same protection |

## Consequences

- **Positive.** Onboarding is an API call. One consumer cannot read another's
  data even if the code is wrong, and noisy neighbours are bounded.
- **Negative.** Session-tag role assumption adds an STS call per cold start per
  namespace, which is cached. The semaphore adds a state and a DynamoDB write
  per document.
- **Neutral.** Account-level service quotas become a capacity-planning item
  for the operator, not the consumers.

## Revisit when

- A consumer's volume dominates the pool: move it to a silo.
- The consumer count makes per-namespace SageMaker endpoints costly: consider
  multi-model endpoints, under a new ADR.
