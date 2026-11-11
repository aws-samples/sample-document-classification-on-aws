# Requirements: Serverless service

- **Status:** Implemented (2026-10-02)
- **ADRs:** [0006](../../adr/0006-serverless-service-shape.md),
  [0007](../../adr/0007-consumers-namespaces-and-isolation.md),
  [0008](../../adr/0008-text-acquisition.md),
  [0009](../../adr/0009-model-lifecycle.md),
  [0010](../../adr/0010-packaging-terraform-module.md)
- **Prefix:** `PLAT`
- **Depends on:** classifier-contract, and each tier spec for its own tier

> **Sensitive data.** The documents this service classifies, such as tax
> forms, bank statements and invoices, routinely contain personal data (PII),
> and may contain health (PHI) or payment card (PCI) data. Anyone who deploys
> it to process regulated data is responsible for meeting the applicable
> requirements, for example HIPAA, PCI DSS or GDPR. See the
> [AWS shared responsibility model](https://aws.amazon.com/compliance/shared-responsibility-model/)
> and [AWS compliance programs](https://aws.amazon.com/compliance/programs/).

## Story 1: submit a document asynchronously

As a **consumer**, I want to submit a document of any size and be told when it
is classified.

- **PLAT-AC-01** WHEN an authenticated consumer calls `POST /v1/jobs` THE API
  SHALL return `202` with a `jobId` and a presigned S3 PUT URL valid for 15
  minutes, scoped to `inbox/{namespace}/{jobId}`.
- **PLAT-AC-02** WHEN an object lands at that key THE service SHALL start exactly
  one Step Functions Standard execution for the job.
- **PLAT-AC-03** WHEN the execution completes THE service SHALL publish one
  EventBridge event, `ClassificationCompleted` (accepted) or `NeedsReview`
  (`UNKNOWN`), carrying `namespace`, `jobId`, `doc_hash`, `class_id`,
  `confidence`, `method` and `model_version`, and no document content.
- **PLAT-AC-04** WHEN a consumer calls `GET /v1/jobs/{id}` THE API SHALL return
  the job's status (`Pending` | `AcquiringText` | `Classifying` | `Completed` |
  `NeedsReview` | `Rejected` | `Failed`) and, once complete, the full
  `CascadeResult`.
- **PLAT-AC-05** IF a job for the same `(namespace, doc_hash)` has already
  completed under the current config version THEN THE service SHALL return
  that result and SHALL NOT run the cascade again.

## Story 2: classify synchronously when text is available

- **PLAT-AC-06** WHEN `POST /v1/classify` receives either text pages, or a PDF ≤
  4 MB whose first `max_pages` pages all have a usable text layer, THE API
  SHALL return the `CascadeResult` in the response.
- **PLAT-AC-07** IF the PDF needs OCR THEN `POST /v1/classify` SHALL return `409`
  with `{"error": "ocr_required", "use": "/v1/jobs"}` without calling Textract.
- **PLAT-AC-08** THE sync and async paths SHALL produce the same `CascadeResult`
  for the same `DocumentText` and configuration. A contract test runs both.

## Story 3: text acquisition (ADR-0008)

- **PLAT-AC-09** THE service SHALL use the PDF text layer for each page with at
  least `min_chars` extractable characters, and Textract only for the other
  pages within the first `max_pages`.
- **PLAT-AC-10** WHEN Textract is needed THE state machine SHALL call
  `StartDocumentTextDetection` and wait on a task token resumed by the
  completion notification. It SHALL NOT poll.
- **PLAT-AC-11** THE resulting `DocumentText` SHALL be stored at
  `text/{namespace}/{doc_hash}.json` with per-page `source`, and reused for any
  later job with the same hash.
- **PLAT-AC-12** IF the file is not a readable PDF, is encrypted, exceeds 500
  MB or exceeds 3,000 pages, THEN the job SHALL end `Rejected` with a reason and
  SHALL NOT be retried.

## Story 4: many consumers, isolated (ADR-0007)

- **PLAT-AC-13** THE service SHALL derive the namespace only from the
  authenticated identity. Any `namespace` field in a request SHALL be ignored.
- **PLAT-AC-14** IF a consumer requests a `jobId` belonging to another namespace
  THEN THE API SHALL return `404`, never `403`, so job ids cannot be probed
  across namespaces.
- **PLAT-AC-15** THE data-access role SHALL allow S3 and DynamoDB access only
  where the object key or partition key starts with the session's
  `namespace` tag. A test SHALL assert that a cross-namespace read made with
  that role is denied by AWS.
- **PLAT-AC-16** THE API SHALL enforce a per-consumer usage plan (rate, burst,
  monthly quota) and SHALL return `429` when it is exceeded.
- **PLAT-AC-17** WHILE a namespace has `max_concurrent_jobs` executions in
  progress, THE state machine SHALL hold further executions for that namespace
  until a slot frees.
- **PLAT-AC-18** WHEN an operator calls `POST /admin/consumers` THE service
  SHALL create the app client, usage plan, registry entry and a default
  AppConfig profile (tier 2 only). Calling it again with the same name
  SHALL change nothing.

## Story 5: per-namespace configuration and models (ADR-0009)

- **PLAT-AC-19** THE Classify Lambda SHALL load the cascade configuration for the
  namespace from AppConfig, and the referenced artefact versions from S3.
- **PLAT-AC-20** IF a proposed AppConfig version fails the CORE configuration
  schema THEN AppConfig validation SHALL reject it before deployment.
- **PLAT-AC-21** THE training workflow SHALL refuse to register an artefact with
  no evaluation report.
- **PLAT-AC-22** WHEN a model package is approved THE service SHALL produce a
  *proposed* AppConfig version for human approval. It SHALL NOT deploy it
  directly.
- **PLAT-AC-23** WHILE an AppConfig deployment is in progress, IF the namespace's
  `UnknownRate` alarm fires THEN AppConfig SHALL roll the deployment back.
- **PLAT-AC-24** WHEN `POST /v1/labels` is called THE service SHALL record the
  label in `labelled/{namespace}/`, and SHALL reject with 400 a `doc_hash`
  that is not 64 lowercase hexadecimal characters.

## Story 6: operate and observe

- **PLAT-AC-25** THE Classify Lambda SHALL emit EMF metrics `Attempts`,
  `Accepted`, `Abstained` (with a `reason` dimension), `LatencyMs`,
  `UnknownRate` and `EstimatedCostUSD`, dimensioned by `namespace` and `tier`.
- **PLAT-AC-26** No log line SHALL contain document text. Logs SHALL carry only
  `namespace`, `jobId`, `doc_hash` and decision fields. A test scans
  structured log output for page text from a fixture.
- **PLAT-AC-27** THE deployment SHALL create alarms for `UnknownRate`, a drop in
  tier coverage, Bedrock and SageMaker throttling, and the state machine
  failure rate, sent to `alarm_sns_topic_arn` when provided.
- **PLAT-AC-28** Every execution SHALL be traced with X-Ray from API Gateway
  through the Lambdas to the Bedrock and SageMaker calls.

## Story 7: security baseline

- **PLAT-AC-29** Every bucket SHALL have Block Public Access, SSE-KMS, versioning
  and a TLS-only policy. `inbox/` and `text/` SHALL expire after 30 days.
- **PLAT-AC-30** Only the Classify Lambda and the tier-2 index builder SHALL hold
  `bedrock:InvokeModel` (on the Titan model ARN), and only the Classify Lambda
  SHALL hold `sagemaker:InvokeEndpoint` (on the deployment's endpoints). A
  policy test asserts that no other role has them.
- **PLAT-AC-31** WHERE `vpc_mode = "byo"`, every Lambda SHALL run in the given
  subnets, with interface endpoints for every AWS service it calls, and no
  route to the internet.

## Story 8: reusable packaging (ADR-0010)

- **PLAT-AC-32** THE module SHALL deploy with only `name` and `region` set,
  giving tiers 1 and 2, no VPC, and a module-created customer-managed KMS key.
- **PLAT-AC-33** `examples/pooled` and `examples/silo` SHALL both pass `tofu
  validate` in CI, and `tofu plan` in the CI account.
- **PLAT-AC-34** THE module SHALL reference Lambda code by `release_version`. A
  plan that changes only `release_version` SHALL update only Lambda code and
  version-tagged resources.
- **PLAT-AC-35** WHERE `enable_tier3 = false`, the module SHALL create no
  SageMaker resources.

## Non-functional

- **PLAT-AC-36** Sync `POST /v1/classify` SHALL respond in under 1.5 s at p95
  when tiers 1 and 2 decide, measured warm.
- **PLAT-AC-37** The async path's own overhead, excluding Textract and model
  time, SHALL be under 3 s at p95 from upload to event.

## Story 9: review additions

- **PLAT-AC-38** IF the API key on a request does not belong to the consumer
  whose token authenticated it THEN THE API SHALL return `403` and do nothing.
- **PLAT-AC-39** WHEN the reaper runs THE service SHALL release every semaphore
  lease held by an execution that is no longer running.
- **PLAT-AC-40** WHEN a job ends `Rejected` or `Failed` THE service SHALL publish
  one `ClassificationFailed` event carrying `namespace`, `jobId`, `status` and
  `reason`, and no document content.
- **PLAT-AC-41** WHEN a consumer is onboarded THE admin route SHALL create that
  namespace's AppConfig profile with the CORE configuration schema as its
  validator, an environment monitored by the namespace's `UnknownRate` alarm,
  and the alarm itself. The alarm SHALL be in the `OK` state before the first
  deployment starts.
- **PLAT-AC-42** WHILE a namespace's configuration is not yet available to the
  caller (including during a gradual AppConfig rollout), THE sync API SHALL
  answer `503 namespace_not_configured`, and THE document workflow SHALL retry
  its dedup and classify steps for at least 10 minutes before failing the job.

## Review log

2026-10-02, before implementation:

1. Step Functions SDK integrations cannot use `.waitForTaskToken`, so Textract
   is started by a Lambda invoked with `lambda:invoke.waitForTaskToken`. The
   SNS-subscribed callback resumes the execution (AC-10 unchanged).
2. Textract async has no page-range parameter. The text-layer step writes a
   PDF of only the pages that need OCR, and Textract reads that (AC-09).
3. The content hash is unknown until the upload is read, so dedup (AC-05) runs
   after preflight, and the semaphore is acquired after dedup.
4. API Gateway's Lambda proxy payload limit is 6 MB, and base64 of a 5 MB PDF
   exceeds it. The sync PDF limit is now 4 MB (AC-06).
5. A Cognito authorizer cannot select a per-consumer usage plan. Consumers send
   an API key issued at onboarding, and the handler checks that it belongs to
   the token's client (AC-38).
6. The S3 policy's `*/<ns>/*` wildcard spans `/`, so `inbox/b/a/x` would match
   namespace `a`. The policy enumerates prefixes instead.
7. A shared counter cannot recover from killed executions. The semaphore holds
   one lease per execution id, and a scheduled reaper frees stale ones (AC-39).
8. Namespaces are created at run time, so per-namespace resources (app client,
   API key, usage plan, AppConfig profile, environment, alarm) are created by
   the admin route, not Terraform (AC-41). There is one training state
   machine, which takes the namespace as input.
9. AppConfig is read with the `appconfigdata` API and cached in memory. No
   extension layer.
10. The default KMS key is module-created (AC-32). `tofu plan` needs
    credentials, so it runs only in the CI account (AC-33).
11. In VPC mode, S3 and DynamoDB use gateway endpoints (`route_table_ids`),
    and everything else uses interface endpoints (AC-31).
12. Rejected and failed jobs had no event (AC-40).
13. Semaphore keys are `ns#<ns>#slots`, so they fall under the same
    `LeadingKeys` pattern as the jobs table.
14. Building the Titan index embeds exemplars, so the index builder also needs
    `bedrock:InvokeModel` (AC-30). The sync API runs inside the Classify
    Lambda (one function, two event shapes), so no API function needs model
    access.
15. A Textract idempotency token built from the content hash would let two
    namespaces share one Textract job id. The token is derived from
    `(namespace, jobId)`.

2026-10-12, after the first deployment to a test account:

16. A new alarm starts in `INSUFFICIENT_DATA`, which AppConfig treats as
    failing, so every first deployment rolled back. Onboarding sets the alarm
    to `OK`, which is what `notBreaching` evaluates to with no data (AC-41).
17. DynamoDB tables use the module key, so every function with table access
    needs `kms:Decrypt` via DynamoDB (AC-32).
18. Security scan hardening: image pulls, SageMaker logs and the AppConfig
    monitor are scoped to their own repositories, log groups and alarms; the
    WAF web ACL logs to CloudWatch with credentials redacted; the tier-3 base
    model is pinned to a commit. Remaining scanner checks are waived inline
    with the reason (AC-29, AC-35).
19. PyMuPDF is AGPL-3.0 and shipped in the Lambda package, so the text layer
    and page subsetting now use pypdf (BSD-3-Clause) (ADR-0011, AC-09).
20. During a new consumer's gradual AppConfig rollout, sessions outside the
    rollout cohort get `ResourceNotFoundException`. It escaped the provider,
    so sync calls failed unhandled and async jobs failed at dedup. The
    provider now raises `NamespaceNotConfigured`, which the workflow retries
    (AC-42).

2026-10-14, after the security scan and a teardown of the test account:

21. The module-created KMS key granted `kms:*` to the account root. Its policy
    now separates administration, given to `kms_admin_role_arns` or the root
    when that is empty, from use through IAM and from grants for AWS services
    (AC-29).
22. The data bucket allowed cross-origin PUTs from any origin. CORS is now off
    unless `cors_allowed_origins` lists specific origins, which only browser
    uploads need (AC-29).
23. The default scikit-learn training container (1.2-1) runs Python 3.9, which
    cannot install the pinned scikit-learn 1.7.2, so tier 1 training failed.
    The default is now 1.4-2 (Python 3.10).
