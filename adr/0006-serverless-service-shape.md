# 0006. Serverless service shape: API, orchestration and results

- **Status:** Accepted (2026-10-02)
- **Date:** 2026-10-02
- **Spec:** [../specs/platform/](../specs/platform/)
- **Related:** 0001, 0007, 0008

## Context

The cascade (ADR-0001) is a library. To serve many teams it has to become a
service with an API, a way to handle slow work, and a way to tell callers the
result. The forces involved:

- **OCR is slow.** Multi-page Textract runs asynchronously and can take
  minutes. API Gateway integrations time out after 29 s by default.
- **Some callers already have text** (born-digital PDFs, or text extracted
  upstream) and want an answer in one call.
- **Traffic is bursty**, from batch drops to idle nights, so always-on
  capacity would be mostly wasted.
- **The cascade's cheap tiers take milliseconds.** Orchestration overhead per
  tier would dominate their cost.

## Decision

1. **Two entry points over one code path.**
   - **Async:** `POST /v1/jobs` returns a `jobId` and a presigned S3 upload
     URL. The upload triggers a **Step Functions Standard** execution for that
     document. `GET /v1/jobs/{id}` returns status and result.
   - **Sync:** `POST /v1/classify` accepts text, or a PDF ≤ 5 MB with a text
     layer. It runs the same Classify Lambda directly. Anything needing OCR is
     rejected with `409 use async`, never attempted inside the timeout.
2. **Step Functions orchestrates the document; one Lambda runs the cascade.**
   The state machine runs text acquisition → classify → persist → notify.
   All three tiers run in-process inside the Classify Lambda, calling
   DynamoDB, Bedrock and SageMaker directly.
3. **Standard, not Express, workflows.** Textract waits can exceed Express's
   5-minute limit. Standard also gives exactly-once execution and a 90-day
   execution history for audit.
4. **Results are pushed and pullable.** Every completion emits an EventBridge
   event (`ClassificationCompleted` or `NeedsReview`) on the service bus.
   Consumers subscribe with a rule targeting their own SQS queue, bus, or an
   API destination (webhook). The job record stays pollable for those that
   cannot subscribe.
5. **Idempotent by content.** A job for a `(namespace, doc_hash)` that has
   already completed under the current config version returns the existing
   result rather than starting a new execution.

## Alternatives considered

| Alternative | Why it lost |
|---|---|
| One Step Functions state per tier | Adds a state transition and its latency to millisecond tiers, and splits one decision across many states, which makes the audit harder |
| Lambda only, polling Textract inside the function | Pays for idle wait time, and a long OCR job eventually hits the 15-minute limit |
| SQS + Lambda workers instead of Step Functions | Workable, but retries, waits and audit history would all have to be built by hand. Step Functions provides them |
| Containers on ECS/Fargate | Always-on cost for bursty traffic, and more to operate. Revisit only for sustained high volume |
| Webhooks only | Many consumers cannot expose an endpoint. EventBridge plus polling covers everyone |

## Consequences

- **Positive.** Pay-per-use end to end. Slow OCR never blocks an API call.
  Consumers can choose between push and pull. A full audit trail exists for
  each document.
- **Negative.** Two entry points must be kept behaviourally identical, which a
  shared handler and contract tests take care of. Standard workflows cost per
  state transition, so the state count must stay small (about 6).
- **Neutral.** The Classify Lambda's memory and timeout are sized for the
  worst case of all three tiers in sequence (CORE-AC-10 timeouts bound it).

## Revisit when

- Sustained volume makes per-transition and per-invocation pricing more
  expensive than provisioned containers.
- A consumer needs sub-second responses on scanned documents, which would need
  a synchronous OCR path for single pages.
