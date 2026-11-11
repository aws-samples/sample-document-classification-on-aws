# AWS Reference Architecture

A shared, serverless classification service that many teams can call. Each
calling team or application is a **consumer** with its own namespace,
taxonomy, models and quotas, on one deployment. A consumer that needs hard
isolation deploys its own copy from the same module.

Decisions: ADR-0006 (service shape), 0007 (consumers and isolation), 0008
(text acquisition), 0009 (model lifecycle), 0010 (packaging). Requirements:
[`specs/platform/`](../specs/platform/).

---

## 1. Big picture

```
 Consumers (apps, batch jobs, other teams)
   │  OAuth2 client-credentials (Cognito)  or  IAM SigV4
   ▼
 ┌──────────────────────── API Gateway (REST, regional) ─────────────────────────┐
 │  POST /v1/jobs           async: returns jobId + presigned upload URL          │
 │  GET  /v1/jobs/{id}      status + result                                      │
 │  POST /v1/classify       sync: text, or a text-layer PDF ≤ 4 MB               │
 │  POST /v1/labels         human label → training set                           │
 │  API key + usage plan per consumer, checked against the token's client        │
 └──────────────┬───────────────────────────────────────┬────────────────────────┘
                │ async                                 │ sync
                ▼                                       ▼
   S3 inbox (per-namespace prefix, KMS)      Classify Lambda (same code as below)
     │ ObjectCreated → EventBridge
     ▼
 ┌──────────── Step Functions (Standard) ── one execution per document ──────────┐
 │                                                                               │
 │  1. Preflight ────▶ Lambda: sha256, PDF limits (Rejected → failure event)     │
 │  2. Dedup ────────▶ same hash + config version already done? reuse result     │
 │  3. Acquire slot ─▶ DynamoDB lease per execution (per-namespace cap)          │
 │  4. Acquire text ─┬─ text layer? ─▶ Text Lambda (pypdf)                        │
 │                   └─ scanned ────▶ PDF of only those pages → Textract         │
 │                                     lambda:invoke.waitForTaskToken            │
 │                                     ◀─ SNS ◀─ Textract → callback merges      │
 │                                     writes DocumentText JSON to S3            │
 │  5. Classify ─────▶ Classify Lambda: the cascade, in-process                  │
 │                       T1 char n-gram LR ───── artefact from S3 (cold start)   │
 │                       T2 Titan nearest ────── Bedrock InvokeModel + DynamoDB  │
 │                       T3 trained model ────── SageMaker Serverless endpoint   │
 │                     writes the job record (decision + every attempt)          │
 │  6. Release slot, then Notify ──▶ EventBridge "ClassificationCompleted" /     │
 │                                  "NeedsReview" / "ClassificationFailed"       │
 └───────────────────────────────────────────────────────────────────────────────┘
                                         │
              ┌──────────────────────────┼────────────────────────────┐
              ▼                          ▼                            ▼
   consumer's own bus / SQS   API destination (webhook)    optional A2I review loop
                                                            → POST /v1/labels

 Model lifecycle (one training state machine, namespace in input)          Observability
   S3 labelled data ─▶ SageMaker Training Job        CloudWatch EMF metrics per tier
   (T1 sklearn)               ─▶ Model Registry      X-Ray traces across the flow
   Index build job (T2) ─▶ S3 artefacts              Dashboards + alarms: UNKNOWN
   Promotion = reviewed config change                rate, coverage drift, cost
```

**Why the cascade runs in one Lambda and not one state per tier.** Tier 1
takes milliseconds. A Step Functions state per tier would add a state
transition, and its cost and latency, to every decision for no benefit. The
state machine orchestrates the *document* (slow OCR, retries, notification),
and the Lambda runs the *cascade* (ADR-0006).

---

## 2. Per-method AWS integration

### Tier 1: char n-gram TF-IDF + LR (ADR-0003)

| Concern | AWS service | Detail |
|---|---|---|
| Runtime | **Lambda** (Classify function, Python, arm64) | Only the NumPy scorer is shipped. The artefact is loaded once per cold start |
| Artefact storage | **S3** `artefacts/{namespace}/ngram/{model_version}.npz` (versioned bucket) | The active version is named in the namespace config (AppConfig). The artefact is never "latest" |
| Training | **SageMaker Training Job**, scikit-learn framework container, ml.m5.large | Input: `s3://…/labelled/{namespace}/`. Output: `.npz` + report |
| Registry | **SageMaker Model Registry** package group `{namespace}-ngram` | The evaluation report is attached as model metrics |
| Cost model | Lambda ms only. Training costs minutes per run | |

### Tier 2: Titan nearest class (ADR-0004)

| Concern | AWS service | Detail |
|---|---|---|
| Embeddings | **Amazon Bedrock** `amazon.titan-embed-text-v2:0` via `InvokeModel` | Reached through a VPC interface endpoint when deployed in a VPC |
| Index | **S3** `artefacts/{namespace}/titan/{index_version}.npz` | Loaded into memory on cold start, since classes × exemplars is small |
| Embedding cache | DynamoDB (`emb#{namespace}#…` in the shared table) | No text is ever embedded twice |
| Index build | **Lambda** in the training state machine | Reads `exemplars/{namespace}/`, embeds with Bedrock, registers the index with its report |
| Throttling | Bedrock per-account quotas, shared by all consumers | Per-consumer API throttles (ADR-0007) stop one consumer from starving the rest |
| Cost model | Per input token, only on documents tier 1 did not accept | |

### Tier 3: SageMaker Serverless classifier (ADR-0005)

| Concern | AWS service | Detail |
|---|---|---|
| Inference | **SageMaker Serverless Inference** endpoint per namespace that opts in | `MemorySizeInMB` and `MaxConcurrency` are set explicitly (SMKR-AC-21) |
| Training | Bring your own model: a SageMaker Training Job (Hugging Face container) fine-tuning a distilled encoder, run by hand from `training/sagemaker/` | The training state machine does not run it. See `docs/usage.md` |
| Registry and promotion | The `sagemaker` tier is added to the namespace configuration by hand, as a reviewed AppConfig change | Never auto-deployed |
| Cost model | Per ms of compute while invoked, and only for gated classes | |

### Shared: text acquisition (ADR-0008)

| Concern | AWS service | Detail |
|---|---|---|
| Text layer | Lambda (pypdf) | Free. Used when every page has ≥ `min_chars` of embedded text |
| OCR | **Amazon Textract** `StartDocumentTextDetection` (async) on a PDF of only the pages without text | Started by a Lambda invoked with `.waitForTaskToken`. The SNS-subscribed callback merges the OCR lines and resumes the execution |
| Output | S3 `text/{namespace}/{doc_hash}.json` (the `DocumentText`) | Keyed by hash, so a redelivered document reuses it: OCR is not paid twice |

---

## 3. Many consumers on one deployment (ADR-0007)

| Concern | Mechanism |
|---|---|
| Identity | **Cognito** user pool app client per consumer (OAuth2 client-credentials, scopes `classify:write`, `jobs:read`, `labels:write`). IAM SigV4 for AWS-internal callers |
| Namespace | Derived **only** from the authenticated client id → a `consumers` registry item. Never taken from the request body |
| Isolation of data | Every S3 key, DynamoDB key and artefact path is prefixed by the namespace. The Lambda scopes reads with an IAM session policy built from the namespace (ABAC on `${aws:PrincipalTag/namespace}`) |
| Quotas | An API Gateway **usage plan** per consumer (rate, burst, monthly quota). A per-namespace lease semaphore in DynamoDB (one lease per execution, freed by a reaper if an execution dies) keeps one consumer's batch from monopolising Textract and Bedrock |
| Configuration | **AppConfig** profile per namespace: taxonomy, tier order, thresholds, artefact versions, `enabled_for`. Validated against the CORE config schema before deployment (CORE-AC-13) |
| Onboarding | `POST /admin/consumers` (operator only, IAM auth) creates the app client, API key, usage plan, registry item, AppConfig profile (schema validator) and environment, and the namespace's `UnknownRate` alarm that monitors it. A consumer starts with tier 2 only, until there is data to train tier 1 |
| Hard isolation | The same module deployed into the consumer's own account ("silo"). No code differences |

---

## 4. Security baseline

- Every bucket: Block Public Access, SSE-KMS with a customer-managed key,
  versioning, TLS-only bucket policy, 30-day lifecycle on `inbox/` and `text/`.
- Every function has its own least-privilege role. Only the Classify function
  (which also serves the sync API) and the tier-2 index builder may call
  `bedrock:InvokeModel` (on the one model ARN), and only the Classify function
  may call `sagemaker:InvokeEndpoint` (on the deployment's endpoints).
- Optional VPC mode (`vpc_mode = "byo"`): private subnets, with gateway
  endpoints for S3 and DynamoDB (`route_table_ids`) and interface endpoints for
  STS, Bedrock runtime, SageMaker runtime, Textract, Step Functions,
  EventBridge, AppConfig, Cognito, API Gateway, CloudWatch, SNS, X-Ray and KMS.
  No NAT gateway is required, and the module creates no internet route.
- No document content in logs. Logs carry `doc_hash`, `namespace` and the
  decision only.
- AWS WAF on the API, and CloudTrail data events on the artefact bucket.

---

## 5. Observability

The Classify Lambda writes CloudWatch Embedded Metric Format (EMF) metrics with
dimensions `namespace` and `tier`:

- `Attempts`, `Accepted`, `Abstained{reason}`, `LatencyMs`
- `UnknownRate`: the human review workload
- `MeanAcceptedConfidence`: the drift signal (`overview.md` §7)
- `EstimatedCostUSD`: Textract pages, Bedrock tokens and SageMaker ms, priced
  from config

Alarms: `UnknownRate` above the baseline, a fall in tier coverage, Bedrock or
SageMaker throttling, and the Step Functions failure rate. X-Ray traces span
API → state machine → Lambda → Bedrock/SageMaker.

---

## 6. Cost shape (per 1,000 documents)

The bill is dominated by **how many documents reach the paid steps**, not by
the cascade itself:

| Item | Paid when | Driver |
|---|---|---|
| Textract | Scanned pages only | Pages × per-page price. Hash-keyed text reuse avoids paying twice |
| Lambda, DynamoDB, Step Functions | Every document | Small: ms, a few reads/writes, ~6 state transitions |
| Bedrock Titan | Documents tier 1 did not accept | Input tokens |
| SageMaker Serverless | Gated classes only | Compute ms |

Price these from current AWS pricing for the target region before committing a
budget.

---

## 7. Packaging (ADR-0010)

One **Terraform module** (OpenTofu-compatible), `modules/doc-classification`,
with inputs `name`, `vpc_mode`, `enable_tier3`, `kms_key_arn?` and
`alarm_sns_topic?`. The same module serves pooled and silo deployments. The
Lambda code ships as a versioned artefact that the module references by
version, so a consumer upgrades by changing one number.
