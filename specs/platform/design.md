# Design: Serverless service

- **Status:** Approved (2026-10-02)
- **Requirements:** [requirements.md](requirements.md)
- **Architecture:** [../../docs/aws-architecture.md](../../docs/aws-architecture.md)

## Repository layout

```
docclass/                 the library (core + methods), see the tier specs
service/
  api/                    jobs, classify, labels, admin handlers (one Lambda per route group)
  text/                   text-layer Lambda, Textract callback Lambda
  classify/               Classify Lambda: config + artefact loading, cascade, EMF
  workflow/               state machine definition (ASL, JSON)
  training/               training workflow ASL + job entry points
modules/doc-classification/   Terraform module (ADR-0010)
examples/{pooled,silo}/
```

## Resources

| Resource | Name pattern | Notes |
|---|---|---|
| API Gateway REST API | `{name}-api` | Cognito authorizer + IAM auth. WAF attached |
| Cognito user pool | `{name}-consumers` | One app client per consumer, client-credentials. Resource server `docclass` with three scopes |
| S3 data bucket | `{name}-data-{account}` | `inbox/`, `text/`, `labelled/`, `exemplars/` prefixes, each under `{namespace}/` |
| S3 artefact bucket | `{name}-artefacts-{account}` | `artefacts/{namespace}/{tier}/{version}.npz` |
| DynamoDB `jobs` | `{name}-jobs` | `pk = ns#{namespace}#job#{jobId}`; GSI `ns#{namespace}#hash#{doc_hash}` for PLAT-AC-05 |
| DynamoDB `decisions` | `{name}-decisions` | Embedding cache (`emb#` key prefix). TTL attribute `expires_at` |
| DynamoDB `consumers` | `{name}-consumers` | `client_id → namespace, limits` |
| DynamoDB `semaphore` | `{name}-semaphore` | `pk = ns#{namespace}#slots`, a `slots` counter plus one lease attribute per execution (PLAT-AC-17, 39) |
| Step Functions | `{name}-document`, `{name}-training` (namespace in input) | Standard. Logging at ERROR plus execution data off (PLAT-AC-26) |
| EventBridge bus | `{name}-events` | Consumers add their own rules |
| SNS topic | `{name}-textract` | Textract completion → callback Lambda |
| AppConfig | application `{name}`, profile per namespace | JSON-Schema validator = the CORE config schema (PLAT-AC-20) |
| SageMaker | Model package groups per namespace/tier. Serverless endpoint per opted-in namespace | Only when `enable_tier3` (PLAT-AC-35) |
| KMS | `alias/{name}` unless `kms_key_arn` is given | |

## Document state machine

```
StartAt: Preflight (Lambda)    read upload, sha256, PDF parse, limits         PLAT-AC-12
       rejected → Mark Rejected → Notify failed                              PLAT-AC-40
  → Dedup (Lambda)             jobs GSI by doc_hash + config_version         PLAT-AC-05
       hit → Notify completed
  → Acquire slot               DynamoDB lease: ADD slots :1, SET #exec        PLAT-AC-17
                               condition slots < :limit, else Wait 5 s → retry
  → Text layer (Lambda)        per-page text, OCR-input PDF of the rest      PLAT-AC-09
  → Needs OCR? ── no ─────────────────────────────────┐
       yes → Textract start (lambda:invoke.waitForTaskToken)              PLAT-AC-10
             stores token, StartDocumentTextDetection → SNS → callback
             callback merges pages, writes text/{ns}/{hash}.json, SendTaskSuccess
  → Classify (Lambda) ◀───────────────────────────────┘   cascade
  → Release slot               ADD slots :-1, REMOVE #exec
  → Notify (events:putEvents)  ClassificationCompleted | NeedsReview         PLAT-AC-03
Catch on every state after Acquire slot → Release slot → Mark Failed → Notify failed
```

**Exactly one execution per upload (PLAT-AC-02).** S3 → EventBridge delivery is
at-least-once. The starter Lambda calls `StartExecution` with `name = jobId`.
Standard workflows reject a duplicate name with `ExecutionAlreadyExists`, which
the starter treats as success. The starter also ignores objects whose job
record is missing or not `Pending`.

**Leases.** The semaphore item `ns#<ns>#slots` holds a `slots` counter and one
attribute per running execution id. The reaper runs every 15 minutes. It frees
the lease of any execution id that `DescribeExecution` reports as not
`RUNNING` (PLAT-AC-39).

## Textract

`textract_start` receives `{input, token}`. It writes a mapping item
`textract#<TextractJobId>` → `{namespace, job_id, doc_hash, token, ocr_pages}`
to the jobs table, then calls `StartDocumentTextDetection` on the OCR-input
PDF, with `NotificationChannel` = the SNS topic plus a publish role that
Textract assumes, and `JobTag = jobId`. The callback Lambda reads the SNS
message, loads the mapping, pages through `GetDocumentTextDetection`, merges
the LINE blocks into the text-layer pages, writes the `DocumentText`, and calls
`SendTaskSuccess` (or `SendTaskFailure` when Textract failed).

## Classify Lambda

- Python 3.12, arm64, 2,048 MB, timeout 60 s (bounded by the tier timeouts in
  the CORE config).
- **Per namespace, cached in memory across invocations:** the AppConfig config
  (through `appconfigdata` `StartConfigurationSession` /
  `GetLatestConfiguration`, honouring the poll interval), the artefacts from
  S3, and STS credentials for the data-access role with session tag
  `namespace` (refreshed before expiry).
- Tiers come from `build_registry(namespace, deps)`. `deps` supplies the
  store, embedder, embedding cache, artefact loaders and SageMaker client, so
  tests inject fakes.
- Builds the `Cascade` from config, runs it, and writes EMF metrics
  (PLAT-AC-25). `EstimatedCostUSD` uses unit prices from config, not the
  Pricing API.
- The sync API handler imports the **same** `handle(document_text, namespace)`
  function. Only the input adapter differs (PLAT-AC-08).

## Isolation policy (PLAT-AC-15)

```json
{
  "Effect": "Allow",
  "Action": ["s3:GetObject", "s3:PutObject"],
  "Resource": ["arn:aws:s3:::{data}/inbox/${aws:PrincipalTag/namespace}/*",
               "arn:aws:s3:::{data}/text/${aws:PrincipalTag/namespace}/*",
               "arn:aws:s3:::{data}/labelled/${aws:PrincipalTag/namespace}/*",
               "arn:aws:s3:::{data}/exemplars/${aws:PrincipalTag/namespace}/*",
               "arn:aws:s3:::{artefacts}/artefacts/${aws:PrincipalTag/namespace}/*"]
},
{
  "Effect": "Allow",
  "Action": ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:Query", "dynamodb:UpdateItem"],
  "Resource": ["{jobs}", "{jobs}/index/*", "{decisions}"],
  "Condition": {"ForAllValues:StringLike": {
    "dynamodb:LeadingKeys": ["ns#${aws:PrincipalTag/namespace}#*",
                             "${aws:PrincipalTag/namespace}#*",
                             "emb#${aws:PrincipalTag/namespace}#*"]}}
}
```

Embedding-cache keys carry the namespace too (`emb#{namespace}#…`). The
vectors hold no document text, but without the namespace, whether a key
exists would reveal that another consumer had seen the same text.

The test for PLAT-AC-15 assumes the role with `namespace=a` and asserts
`AccessDenied` on a `b/` object and on a `ns#b#…` key, against a real
deployment in the CI account.

## API

| Route | Auth scope | Handler |
|---|---|---|
| `POST /v1/jobs` | `classify:write` | Creates the job record and the presigned PUT. `Content-Type` is fixed to `application/pdf` |
| `GET /v1/jobs/{id}` | `jobs:read` | Looks up `ns#{caller}#job#{id}`. A miss for any reason returns `404` (PLAT-AC-14) |
| `POST /v1/classify` | `classify:write` | Body: `{pages:[{number,text}]}` or a base64 PDF ≤ 4 MB |
| `POST /v1/labels` | `labels:write` | `{doc_hash, class_id}` |
| `POST /admin/consumers` | IAM, operator role | Idempotent by consumer name (PLAT-AC-18). Creates app client, API key, usage plan, registry item, AppConfig profile + environment + `UnknownRate` alarm (PLAT-AC-41) |

Every consumer route also requires an API key. The handler rejects a request
whose `apiKeyId` differs from the consumer's registered key with `403`
(PLAT-AC-38).

## Terraform module interface

```hcl
module "docclass" {
  source              = "…/modules/doc-classification"
  name                = "docclass"
  region              = "us-east-1"
  release_version     = "1.0.0"
  vpc_mode            = "none"
  vpc_id              = null
  subnet_ids          = []
  route_table_ids     = []
  artifact_dir        = "../../dist"
  tier3_endpoints     = {}
  enable_tier3        = false
  kms_key_arn         = null
  alarm_sns_topic_arn = null
}
```

## Test strategy

| Level | Covers |
|---|---|
| Unit (moto; ASL checked structurally) | API handlers, starter idempotency, callback, state machine paths |
| Policy tests (IAM Policy Simulator / `iam:SimulatePrincipalPolicy`) | PLAT-AC-30 |
| Deployed integration (CI account, `examples/pooled`) | PLAT-AC-02, 10, 15, 16, 23, 28, 36, 37 |
| `tofu validate` / `plan` on the examples | PLAT-AC-32..35 |
