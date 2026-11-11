# Threat model

This is the threat model for the sample as published. It covers the Terraform
module in `modules/doc-classification`, the Lambda code in `service/` and
`docclass/`, and the optional tier 3 training and inference code in
`training/sagemaker/`. It uses STRIDE, one table per trust boundary.

## Scope and assumptions

- The deployer owns the AWS account and runs Terraform with administrator
  credentials. Threats from that role are out of scope.
- Consumers are machine clients of the deploying organisation, or of its
  customers, each onboarded by an operator. There are no end-user logins.
- Documents are untrusted input. They routinely contain PII and financial
  data, for example tax forms, bank statements and invoices.
- The sample is not production-ready. The README says so, and it lists the
  deployer's PCI DSS, HIPAA and GDPR responsibilities.

## Assets

| Asset | Where it lives | Protection |
|---|---|---|
| Uploaded PDFs | S3 data bucket `inbox/{namespace}/` | SSE-KMS (CMK), TLS-only bucket policy, public access blocked, expires after 30 days |
| Extracted text | S3 data bucket `text/{namespace}/` | As above, expires after 30 days |
| Labels and exemplars, which contain document text | S3 data bucket `labelled/`, `exemplars/` | SSE-KMS, versioned. **Kept until the deployer deletes them** |
| Job records and decisions | DynamoDB `jobs`, `decisions` | SSE-KMS (CMK). Results hold the class, confidence and hash, not text |
| Consumer credentials | Cognito app client secret, API Gateway API key | Returned once, in the onboarding response. Never logged |
| Model artefacts | S3 artefacts bucket `artefacts/{namespace}/` | SSE-KMS, versioned. Loaded with `allow_pickle=False` |
| Per-namespace configuration | AWS AppConfig hosted profiles | JSON Schema validator. Deployments roll back on the namespace alarm |

## Data flow

```
Consumer --(Cognito M2M token + API key, TLS)--> WAF --> API Gateway
   |                                                     |-- POST /v1/classify --> Classify Lambda (sync)
   |                                                     |-- POST /v1/jobs     --> presigned S3 PUT URL (15 min)
   '--(presigned PUT, TLS)--> S3 inbox --ObjectCreated--> EventBridge --> Starter --> Step Functions
        Step Functions: Preflight -> Dedup -> Text layer (pypdf) | Textract -> Classify -> Finalize
        Classify: tier 1 (in-process) -> tier 2 (Bedrock Titan embeddings) -> tier 3 (SageMaker Serverless)
        Finalize --> EventBridge bus (class, confidence, hash: no text)
Operator --(IAM SigV4)--> POST /admin/consumers --> Cognito, API Gateway, AppConfig, CloudWatch
```

## Threats and mitigations

### Boundary 1: consumer to API

| # | STRIDE | Threat | Mitigation in the sample | Residual risk and the deployer's action |
|---|---|---|---|---|
| 1.1 | Spoofing | A caller impersonates a consumer | Every `/v1` route needs a Cognito access token (client credentials) **and** the API key bound to the same client. `identity.resolve` rejects a key that doesn't match the token's client (`api_key_mismatch`) | Rotate a client secret that leaks by removing the consumer (`scripts/remove_consumer.py`) and onboarding again |
| 1.2 | Elevation | A consumer calls an operation it lacks the scope for | Per-route OAuth scopes (`classify:write`, `jobs:read`, `labels:write`), checked by API Gateway and again in the handler | None |
| 1.3 | Info disclosure | One consumer reads another's jobs, documents or results | The namespace comes from the server-side `client#` record, never from the request. All data access goes through a role assumed with a `namespace` session tag. Its policy limits S3 to `*/${aws:PrincipalTag/namespace}/*` and DynamoDB to namespace-prefixed `LeadingKeys`. Job IDs are random UUIDs, and the job record's namespace is checked again on read | None in the code. A deployer who needs stronger isolation can use the `silo` example, one deployment per consumer |
| 1.4 | Tampering | Path traversal or injection through request fields | The namespace is matched against `^[a-z][a-z0-9-]{1,30}$`, job IDs are parsed as UUIDs, and `doc_hash` must be 64 lowercase hex characters. S3 keys are built from these values only. DynamoDB uses expression attribute names and values | None |
| 1.5 | DoS | Request floods or oversized bodies | WAF: AWS managed common and known-bad-inputs rule sets, plus a per-IP rate rule. Per-consumer usage plans (rate, burst, monthly quota), a stage-level throttle, and a per-namespace concurrent-job semaphore. Sync PDFs are capped (`sync_pdf_limit`); larger ones are sent to `/v1/jobs` | Tune `waf_rate_limit` and the per-consumer limits to expected load |
| 1.6 | Info disclosure | Credentials or document text end up in logs | WAF logs redact `authorization` and `x-api-key`. API Gateway data trace is off, and access logs hold no bodies. Lambda logs go through an allowlist of fields (`service/common/logs.py`), so document text is never logged. Step Functions logging excludes execution data. Log groups are KMS-encrypted | None |
| 1.7 | Repudiation | A consumer denies submitting a document or a label | API Gateway access logs record the request ID, source IP, API key ID and client ID. Labels record the principal that wrote them. Log retention defaults to 365 days | None |

### Boundary 2: presigned upload to S3

| # | STRIDE | Threat | Mitigation in the sample | Residual risk and the deployer's action |
|---|---|---|---|---|
| 2.1 | Spoofing / Tampering | Someone else uses the URL, or it's reused to overwrite a document | The URL is signed by the namespace-scoped role, for one key `inbox/{namespace}/{job_id}`, a fixed `Content-Type`, and 15 minutes. The starter only runs a job whose status is `Pending`, and Step Functions uses the job ID as the execution name, so a second upload doesn't start a second run | Anyone holding the URL can upload during those 15 minutes. Treat it as a bearer secret |
| 2.2 | DoS / cost | An upload that is far too large | Preflight reads the size from `HeadObject` and rejects anything over 500 MB before downloading it. The inbox expires after 30 days, and incomplete multipart uploads are aborted after 1 day | A presigned PUT can't cap size in advance, so storage for one oversized object can exist until it expires |
| 2.3 | Info disclosure | Data in transit or at rest | The bucket policy denies requests without TLS. Default SSE-KMS with a CMK. Public access is blocked and ownership is enforced | None |

### Boundary 3: untrusted document content to the processing pipeline

| # | STRIDE | Threat | Mitigation in the sample | Residual risk and the deployer's action |
|---|---|---|---|---|
| 3.1 | Tampering / Elevation | A crafted PDF exploits the parser | pypdf is pure Python and pinned to 6.21.4, which fixes GHSA-5hx5-vpf9-5wcv and the later parser advisories up to that release. Pillow isn't packaged, so image decoders aren't reachable. Encrypted PDFs are rejected. Parsing runs in a Lambda with a least-privilege role | Keep pypdf patched |
| 3.2 | DoS | Decompression bombs or PDFs with very many pages | 500 MB and 3,000-page limits. Text is read from the first `max_pages` only. Lambda timeouts and memory are bounded. Textract gets at most the first 2 pages | Each document costs at most one Lambda timeout |
| 3.3 | Tampering | Document text tries to steer a model (prompt injection) | No tier generates text. Tier 2 sends text to Titan **embeddings** and compares vectors. Every tier returns a class from the closed set or abstains. Low-confidence results go to `UNKNOWN` for human review | Someone could craft a document to land in a wrong class. The calibrated thresholds and human review of `NeedsReview` limit the impact |
| 3.4 | Info disclosure | Document content leaves the account | Text goes only to Amazon Textract, Amazon Bedrock and, if enabled, the deployer's own SageMaker endpoint, all in the same Region. The `byo` VPC mode routes calls through interface endpoints. Bedrock doesn't use the inputs to train models | Deployers must confirm these services are approved for their data classification |
| 3.5 | Spoofing | Forged Textract completion callbacks | Textract publishes to an SNS topic through a role it can assume only from this account (`aws:SourceAccount`). The callback looks up a stored `textract#{job_id}` mapping and resumes that execution by task token | None |

### Boundary 4: operator and admin plane

| # | STRIDE | Threat | Mitigation in the sample | Residual risk and the deployer's action |
|---|---|---|---|---|
| 4.1 | Elevation | A non-operator onboards a consumer or maps an IAM principal to a namespace | `POST /admin/consumers` uses `AWS_IAM` authorisation, and the handler rejects requests that have no IAM identity. The admin Lambda's role can only create clients in this user pool, keys and plans, alarms with the module's prefix, and profiles in this AppConfig application | Any principal in the account with `execute-api:Invoke` on the API can onboard. Grant it only to operator roles |
| 4.2 | Tampering | A bad configuration change sends documents to the wrong classes | AppConfig validates every version against a JSON Schema. Deployments roll out gradually and roll back if the namespace's `UnknownRate` alarm fires. `propose_config` only creates a version; a human starts the deployment | None |
| 4.3 | Tampering | A poisoned model artefact | Artefacts are written only by the training-register Lambda to `artefacts/{namespace}/`. NumPy loads them with `allow_pickle=False`, so no code runs on load. SageMaker model packages need explicit approval before `propose_config` uses them | A labeller with `labels:write` could mislabel on purpose. Review label sets before you train |
| 4.4 | Info disclosure | Key misuse | A CMK with rotation on. Key administration is separate from key use (`kms_admin_role_arns`). Use is granted through IAM, and most grants are limited with `kms:ViaService` | None |

### Boundary 5: optional tier 3 supply chain

| # | STRIDE | Threat | Mitigation in the sample | Residual risk and the deployer's action |
|---|---|---|---|---|
| 5.1 | Tampering | A malicious base model or container | The base model is pinned with `--base-model-revision`. Inference loads only the local model directory (`local_files_only=True`). The deployer chooses the serving image, and the examples use AWS Deep Learning Containers | The deployer must choose base models they trust |
| 5.2 | Tampering | Known CVEs in training and inference libraries | Tier 3 is off by default (`enable_tier3 = false`). Nothing in the Lambda package depends on PyTorch or Transformers. The training pins (PyTorch 2.14.1, Transformers 5.20.2, Accelerate 1.15.0) have no known vulnerabilities. The example serving image is the newest Hugging Face inference DLC (PyTorch 2.6.0, Transformers 5.5.3). Its two HIGH Transformers advisories cover `generate()` custom code (GHSA-x9r9-c232-4q39) and `save_pretrained` chat-template paths (GHSA-xrqw-3rrv-vx5w). `inference.py` calls neither, and it loads only the local model directory (`local_files_only=True`) | The deployer chooses the serving image. Move to a newer DLC when one is released |

## Residual risks

1. **Retention of labelled data.** `labelled/` and `exemplars/` hold document
   text and have no expiry. A deployer handling regulated data should set a
   lifecycle rule or delete them after training.
2. **No code signing on Lambda, and no S3 server access logs.** Both are
   outside the scope of this sample (Checkov CKV_AWS_272, CKV_AWS_18).
3. **No mutual TLS.** Clients authenticate with OAuth2 client credentials
   plus an API key (CKV2_AWS_51).
