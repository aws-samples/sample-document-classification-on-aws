# Using the service

How to call a deployed service, how to get a consumer from `UNKNOWN` to real
answers, how to add tier 3, and how to remove a consumer. The commands assume
`examples/pooled` (module `name = "docclass"`), are run from
`examples/pooled`, and use bash, the AWS CLI v2 and `python3`.

Set the Region once, to the Region you deployed to, so every CLI call uses it:

```bash
export AWS_REGION=eu-west-1
```

## 1. API reference

All routes are under one API Gateway REST API, stage `live`.
`terraform output -raw api_url` is the `/v1` base URL, and
`terraform output -raw admin_url` is the full `/admin/consumers` URL.

### Authentication

Every `/v1` route needs two headers:

| Header | Value |
|---|---|
| `Authorization` | A Cognito access token from the client credentials grant, sent as the bare token |
| `x-api-key` | The consumer's API key |

The handler also checks that the API key belongs to the same consumer as the
token, so a key and a token from different consumers are rejected.

Get a token with the `client_id` and `client_secret` from onboarding. Tokens
are valid for an hour by default, so fetch a new one when it expires:

```bash
TOKEN=$(curl -s -u "$CLIENT_ID:$CLIENT_SECRET" -d grant_type=client_credentials \
  "$(terraform output -raw token_endpoint)" | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')
API=$(terraform output -raw api_url)
```

Every onboarded client is granted all three scopes. Each route needs one:

| Route | Scope |
|---|---|
| `POST /v1/classify` | `docclass/classify:write` |
| `POST /v1/jobs` | `docclass/classify:write` |
| `GET /v1/jobs/{id}` | `docclass/jobs:read` |
| `POST /v1/labels` | `docclass/labels:write` |
| `POST /admin/consumers` | None. IAM (SigV4) authentication |

The namespace comes only from the token. A namespace in a body or path is
ignored.

API Gateway rejects some requests before they reach the service: 401 for a
missing or invalid token, 403 for a missing or unknown API key or a WAF
block, and 429 when the consumer's usage plan rate or monthly quota is used
up. The service's own errors are JSON bodies of the form
`{"error": "<code>", ...}`:

| Status | `error` | Meaning |
|---|---|---|
| 403 | `unknown_consumer` | The token's client is not in the consumers registry |
| 403 | `api_key_mismatch` | The API key is not the one issued with this client |
| 403 | `insufficient_scope` | The token lacks the route's scope |
| 400 | `invalid_json` | The body is not a JSON object |

### POST /v1/classify

Synchronous classification. Send either the page text or a PDF that has a
text layer.

Text:

```bash
curl -s -X POST "$API/classify" \
  -H "Authorization: $TOKEN" -H "x-api-key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"pages": [{"number": 1, "text": "Form W-2 Wage and Tax Statement"}]}'
```

`pages` is a non-empty list. Each page needs a string `text`. `number` is
optional and defaults to the page's position, starting at 1. `doc_hash` is the
SHA-256 of the page texts joined with a form feed.

PDF:

```bash
curl -s -X POST "$API/classify" \
  -H "Authorization: $TOKEN" -H "x-api-key: $API_KEY" -H "Content-Type: application/json" \
  -d "{\"pdf_base64\": \"$(base64 < w2.pdf | tr -d '\n')\"}"
```

The decoded PDF may be at most 4 MiB (4,194,304 bytes). Only the first 2
pages are read, and each of them needs at least 50 non-whitespace characters
of embedded text. A PDF that fails this needs OCR, which only the async route
does. `doc_hash` is the SHA-256 of the PDF bytes.

The sync route does not store the text, so a `doc_hash` from this route
cannot be used to train (see [Labels](#post-v1labels)).

Response 200 is the cascade result:

```json
{
  "decision": {"class_id": "W2", "confidence": 0.97, "accepted": true, "method": "titan_nn",
               "model_version": "...", "taxonomy_version": "tx-1", "scores": {"W2": 0.97},
               "reason": null, "metadata": {}},
  "attempts": [{"tier": "titan_nn", "decision": {"...": "..."}, "error": null, "elapsed_ms": 112.4}],
  "doc_hash": "...",
  "taxonomy_version": "tx-1",
  "config_version": "team-a-initial"
}
```

`decision.accepted` is `false` and `class_id` is `UNKNOWN` when every tier
abstained. Each attempt's `decision.reason` says why that tier abstained.

| Status | `error` | Cause |
|---|---|---|
| 400 | `pages_or_pdf_required` | The body has neither `pages` nor `pdf_base64` |
| 400 | `pages_required` | `pages` is empty or not a list |
| 400 | `invalid_page` | A page is not an object with a string `text`. `index` names it |
| 400 | `invalid_base64` | `pdf_base64` is not valid base64 |
| 400 | `unreadable_pdf` | The PDF cannot be opened, or is encrypted. `reason` says which |
| 409 | `ocr_required` | A page needs OCR. Use `/v1/jobs` |
| 413 | `pdf_too_large` | The PDF is over `limit_bytes`. Use `/v1/jobs` |
| 503 | `namespace_not_configured` | No configuration has reached this caller yet, or the configuration cannot be loaded (for example a model whose `taxonomy_version` differs from the configuration's) |

A new consumer's first configuration rolls out over about 10 minutes, so
calls in that window can get 503. Retry with backoff.

### Async jobs

For scanned PDFs, PDFs over 4 MiB, and anything you want stored for training.

1. Create a job. There is no request body:

   ```bash
   JOB=$(curl -s -X POST "$API/jobs" -H "Authorization: $TOKEN" -H "x-api-key: $API_KEY")
   JOB_ID=$(echo "$JOB" | python3 -c 'import json,sys; print(json.load(sys.stdin)["jobId"])')
   URL=$(echo "$JOB" | python3 -c 'import json,sys; print(json.load(sys.stdin)["uploadUrl"])')
   ```

   Response 202: `jobId`, `uploadUrl` (a presigned S3 PUT URL), `expiresIn`
   (900 seconds) and `uploadHeaders` (`{"Content-Type": "application/pdf"}`).

2. Upload the PDF to `uploadUrl` within `expiresIn` seconds, with exactly the
   headers in `uploadHeaders`:

   ```bash
   curl -s -X PUT -H "Content-Type: application/pdf" --upload-file w2.pdf "$URL"
   ```

   The upload starts the workflow. Upload once: an upload to a job that is no
   longer `Pending` is ignored.

3. Poll the job:

   ```bash
   curl -s "$API/jobs/$JOB_ID" -H "Authorization: $TOKEN" -H "x-api-key: $API_KEY"
   ```

Statuses:

| Status | Terminal | Meaning |
|---|---|---|
| `Pending` | No | Created, waiting for the upload |
| `AcquiringText` | No | Preflight passed. Reading the text layer, or running Textract on the pages without one |
| `Classifying` | No | Running the cascade |
| `Completed` | Yes | A tier accepted. `result` holds the decision |
| `NeedsReview` | Yes | Every tier abstained (`UNKNOWN`). `result` holds the attempts |
| `Rejected` | Yes | The file failed preflight: over 500 MiB, over 3,000 pages, no pages, encrypted or unreadable. `reason` says which |
| `Failed` | Yes | The workflow failed. `reason` holds the error name |

`GET /v1/jobs/{id}` returns `jobId`, `status`, `createdAt` and `updatedAt`
(Unix seconds), `docHash` once preflight has hashed the file, `result` (the
same shape as the sync response) for `Completed` and `NeedsReview`, and
`reason` for `Rejected` and `Failed`. An unknown id, or another consumer's,
returns 404 `not_found`.

As in the sync route, only the first 2 pages are read, and Textract runs only
on those of them with less than 50 characters of text. A document with the
same hash that was already classified under the same `config_version`
reuses that result.

If the namespace's configuration has not reached the workflow yet, the
workflow retries for about 13 minutes before it marks the job `Failed` with
reason `NamespaceNotConfigured`.

### POST /v1/labels

Records a human label for a document:

```bash
curl -s -X POST "$API/labels" \
  -H "Authorization: $TOKEN" -H "x-api-key: $API_KEY" -H "Content-Type: application/json" \
  -d "{\"doc_hash\": \"$DOC_HASH\", \"class_id\": \"W2\"}"
```

Response 201: `{"doc_hash": ..., "class_id": ...}`. The label is written to
`s3://<data bucket>/labelled/<namespace>/<doc_hash>.json` with the
`class_id`, the `taxonomy_version`, the time, the caller and, when the service
still holds the document's text, the `document` itself.

The text is held only for documents that went through an async job, and only
for 30 days (the `text/` lifecycle rule). A label for a sync `doc_hash`, or
for an older document, is stored with `"document": null`, and training skips
it.

| Status | `error` | Cause |
|---|---|---|
| 400 | `doc_hash_and_class_id_required` | `doc_hash` is not a 64-character string, or `class_id` is missing |
| 400 | `unknown_class` | `class_id` is not in the namespace's classes |
| 503 | `namespace_not_configured` | No configuration has reached this caller yet |

### Events

Async jobs publish to the EventBridge bus `docclass-events`
(`terraform output -raw event_bus_arn`) with source `docclass`. The bus is
shared by all consumers, so filter on `detail.namespace`. The module creates
no rules or cross-account policy on it. Add your own rule, for example with
the pattern
`{"source": ["docclass"], "detail": {"namespace": ["team-a"]}}`.

| `detail-type` | When | `detail` fields |
|---|---|---|
| `ClassificationCompleted` | A tier accepted | `namespace`, `jobId`, `doc_hash`, `class_id`, `confidence`, `method`, `model_version` |
| `NeedsReview` | Every tier abstained | The same fields. `class_id` is `UNKNOWN` |
| `ClassificationFailed` | The job ended `Rejected` or `Failed` | `namespace`, `jobId`, `status`, `reason` |

The sync route publishes no events.

### POST /admin/consumers

Onboards a consumer. It uses IAM authentication, so call it with `awscurl`
and credentials allowed to invoke the API:

```bash
awscurl --service execute-api --region "$AWS_REGION" -X POST \
  -d '{"name": "team-a", "classes": ["W2", "INVOICE", "BANK_STATEMENT"], "rate": 10, "burst": 20}' \
  "$(terraform output -raw admin_url)"
```

| Field | Required | Default | Meaning |
|---|---|---|---|
| `name` | Yes | | The namespace. 2–31 characters matching `^[a-z][a-z0-9-]{1,30}$` |
| `classes` | Yes | | Non-empty list of class ids. `UNKNOWN` is reserved |
| `rate` | No | 20 | Usage plan steady rate, requests per second |
| `burst` | No | 40 | Usage plan burst |
| `quota` | No | 100000 | Usage plan requests per month |
| `max_concurrent_jobs` | No | 10 | Async documents the namespace may have in flight at once |
| `iam_principals` | No | | IAM role ARNs to map to the namespace. See below |

The limits must be positive numbers.

Onboarding creates a Cognito app client, an API key and usage plan named
`docclass-<name>`, the alarm `DocClass-<name>-unknown-rate`, and an AppConfig
configuration profile and environment named `<name>`. It then deploys the
first configuration with the module's gradual strategy, which takes about 10
minutes plus a 15-minute bake.

Response 201:

| Field | Meaning |
|---|---|
| `namespace` | The namespace |
| `created` | `true` |
| `client_id` | The Cognito app client id |
| `client_secret` | The app client secret. Returned only once |
| `api_key` | The API key value. Returned only once |
| `api_key_id` | The API key's id (not the value) |
| `usage_plan_id` | The usage plan id |

Store `client_secret` and `api_key` when you get them. Calling again with the
same `name` after onboarding finished returns 200 with `namespace`,
`client_id`, `api_key_id`, `usage_plan_id` and `"created": false`, and changes
nothing: new limits or principals in that call are ignored. If an earlier call
failed part-way, a repeat call finishes the remaining steps, but secrets
created by the earlier call are not returned again.

Errors: 400 `invalid_name`, `classes_required`, `invalid_classes` or
`invalid_limit` (with `field`), and 403 `iam_auth_required`.

`iam_principals` writes a mapping from each role ARN to the namespace, and the
identity code can resolve SigV4 callers through it. But every `/v1` route uses
the Cognito authorizer and requires an API key, so API Gateway rejects a SigV4
request without a token before it reaches that code. In this module
`iam_principals` has no effect on the `/v1` routes.

## 2. From UNKNOWN to real answers: train and promote

### Why a new consumer gets UNKNOWN

Onboarding writes a configuration with one tier, `titan_nn` (tier 2), and no
index:

```json
{"config_version": "team-a-initial", "taxonomy_version": "tx-1",
 "classes": ["W2", "INVOICE", "BANK_STATEMENT"],
 "tiers": [{"name": "titan_nn", "timeout_ms": 3000, "thresholds": {"tau": 0.9, "delta": 0.1}}],
 "unit_prices": {}}
```

With no `index_version`, tier 2 abstains with `not provisioned: index`, so
every document is `UNKNOWN`. To get answers, build a tier 2 index from
exemplars, then train tier 1 from labels, and promote each through the model
registry and AppConfig.

Set up the variables the steps use:

```bash
NS=team-a
DATA=$(terraform output -raw data_bucket)
ARTEFACTS=$(terraform output -raw artefact_bucket)
TRAINING_SM=$(terraform output -raw training_state_machine_arn)
APP=$(aws appconfig list-applications --query "Items[?Name=='docclass'].Id" --output text)
STRATEGY=$(aws appconfig list-deployment-strategies --query "Items[?Name=='docclass-gradual'].Id" --output text)
read -r PROFILE ENV < <(aws dynamodb get-item --table-name docclass-consumers \
  --key "{\"pk\": {\"S\": \"ns#$NS#consumer\"}}" \
  --query 'Item.[appconfig_profile_id.S, appconfig_environment_id.S]' --output text)
```

Your credentials need read and write access to the data bucket and use of the
deployment's KMS key.

### Exemplars for tier 2

Tier 2 is built from exemplar files at
`s3://<data bucket>/exemplars/<namespace>/*.json`. Each file is one document:

```json
{"class_id": "W2", "document": {"doc_hash": "<64 hex characters>",
  "pages": [{"number": 1, "text": "Form W-2 Wage and Tax Statement ...", "source": "text_layer"}]}}
```

`document` is a `DocumentText` (`docclass/core/types.py`): a `doc_hash` and a
list of pages, each with a 1-based `number`, the `text`, and a `source` of
`text_layer` or `ocr`. `doc_hash` must be unique per document.

There are two ways to get one.

1. **From the service.** Run the document through an async job, so the
   service extracts the text, OCR included. Then copy the stored text, within
   30 days:

   ```bash
   aws s3 cp "s3://$DATA/text/$NS/$DOC_HASH.json" - \
     | python3 -c 'import json,sys; print(json.dumps({"class_id": sys.argv[1], "document": json.load(sys.stdin)}))' W2 \
     | aws s3 cp - "s3://$DATA/exemplars/$NS/$DOC_HASH.json"
   ```

   Or, once the document is labelled, copy the label's `document`:

   ```bash
   aws s3 cp "s3://$DATA/labelled/$NS/$DOC_HASH.json" - \
     | python3 -c 'import json,sys; r=json.load(sys.stdin); assert r["document"]; print(json.dumps({"class_id": r["class_id"], "document": r["document"]}))' \
     | aws s3 cp - "s3://$DATA/exemplars/$NS/$DOC_HASH.json"
   ```

2. **From a folder of PDFs**, one folder per class, named by class id.
   `scripts/make_exemplars.py` reads each PDF's text layer the way the
   service does and writes both the exemplar and the tier 1 label record. It
   skips PDFs without a text layer (send those through the async API), and
   keeps every fifth file out for evaluation, listed in `holdout.json`:

   ```bash
   python3 scripts/make_exemplars.py my-documents --namespace "$NS" --bucket "$DATA"
   ```

3. **From text you already have.** From the repository root, with the
   `docclass` package importable:

   ```bash
   python3 -c 'import json,sys
   from docclass.core.types import DocumentText
   d = DocumentText.from_texts([open(p).read() for p in sys.argv[2:]])
   print(json.dumps({"class_id": sys.argv[1], "document": d.to_dict()}))' W2 page1.txt page2.txt > exemplar.json
   ```

How many: the index builder holds out every fifth exemplar to calibrate the
threshold, and needs at least 20 held out. With fewer than 100 exemplars in
total, or if no threshold reaches 99% precision on the held-out set, the index
is still built and registered, but disabled: tier 2 then abstains with
`index disabled`. The build report says which.

### Labels for tier 1

Tier 1 trains from `s3://<data bucket>/labelled/<namespace>/`, the records
`POST /v1/labels` writes. Only labels that carry a `document` count, which
means labels for documents from async jobs within 30 days. Training needs at
least 10 documents for each of at least two classes, and leaves out classes
with fewer. Classes with 30 or more are calibrated.

### Start training

The training state machine takes:

| Field | Tiers | Value |
|---|---|---|
| `tier` | Both | `titan` or `ngram` |
| `namespace` | Both | The namespace |
| `taxonomy_version` | Both | The namespace configuration's `taxonomy_version` (`tx-1` unless you changed it) |
| `job_name` | Both | A unique name. For `ngram` it is the SageMaker training job name: up to 63 letters, digits and hyphens |
| `classes` | `ngram` | The classes to train, as a JSON-encoded **string**, for example `"[\"W2\",\"INVOICE\"]"`. SageMaker hyperparameters must be strings |

`taxonomy_version` must match the configuration exactly. A model built for
another taxonomy version stops the namespace's configuration from loading, and
every call returns 503 `namespace_not_configured`. `classes` must be a subset
of the configuration's classes for the same reason.

Tier 2:

```bash
JOB="$NS-titan-$(date +%Y%m%d%H%M%S)"
aws stepfunctions start-execution --state-machine-arn "$TRAINING_SM" --name "$JOB" \
  --input "{\"tier\": \"titan\", \"namespace\": \"$NS\", \"taxonomy_version\": \"tx-1\", \"job_name\": \"$JOB\"}"
```

Tier 1:

```bash
JOB="$NS-ngram-$(date +%Y%m%d%H%M%S)"
INPUT=$(python3 -c 'import json,sys; ns, job = sys.argv[1:3]
print(json.dumps({"tier": "ngram", "namespace": ns, "taxonomy_version": "tx-1",
                  "classes": json.dumps(["W2", "INVOICE", "BANK_STATEMENT"]), "job_name": job}))' "$NS" "$JOB")
aws stepfunctions start-execution --state-machine-arn "$TRAINING_SM" --name "$JOB" --input "$INPUT"
```

`titan` runs the index builder Lambda. `ngram` runs a SageMaker training job
on one `ml.m5.large` for at most an hour, with the scikit-learn container and
`training-source.tar.gz` from the release. Wait for the execution to finish:

```bash
aws stepfunctions describe-execution --query status --output text \
  --execution-arn "${TRAINING_SM/:stateMachine:/:execution:}:$JOB"
```

The state machine logs errors only, without execution data. If it fails, read
the execution history in the Step Functions console, the training job's logs
in `/aws/sagemaker/TrainingJobs`, or the Lambda logs.

### What registration does

The last step, `service/training/register.py`, puts the artefact at
`s3://<artefact bucket>/artefacts/<namespace>/<ngram|titan>/<model_version>.npz`
with its evaluation report next to it as `<model_version>.report.json`. It
then creates a model package in the SageMaker Model Registry group
`<namespace>-ngram` or `<namespace>-titan` (created if missing), with status
`PendingManualApproval`. The package's metadata holds the namespace, tier,
model version, artefact key, report URI and the report's thresholds, and the
report is attached as model quality metrics.

Read the report before you approve:

```bash
aws s3 cp "s3://$ARTEFACTS/artefacts/$NS/titan/<model_version>.report.json" -
```

### Approve and deploy

1. Approve the newest package in the group:

   ```bash
   PKG=$(aws sagemaker list-model-packages --model-package-group-name "$NS-titan" \
     --sort-by CreationTime --sort-order Descending --max-results 1 \
     --query 'ModelPackageSummaryList[0].ModelPackageArn' --output text)
   aws sagemaker update-model-package --model-package-arn "$PKG" --model-approval-status Approved
   ```

2. The approval fires the `docclass-model-approved` EventBridge rule, which
   invokes the `docclass-propose-config` Lambda. It reads the namespace's
   latest hosted configuration version, sets `index_version` (tier 2) or
   `artefact_version` (tier 1, added as the first tier if absent), merges the
   report's thresholds, appends to `config_version`, validates the result and
   saves it as a new hosted configuration version. It does **not** deploy it.
   If no new version appears, check `/aws/lambda/docclass-propose-config`.

3. Review the proposal:

   ```bash
   VERSION=$(aws appconfig list-hosted-configuration-versions --application-id "$APP" \
     --configuration-profile-id "$PROFILE" --max-results 1 --query 'Items[0].VersionNumber' --output text)
   aws appconfig get-hosted-configuration-version --application-id "$APP" \
     --configuration-profile-id "$PROFILE" --version-number "$VERSION" proposed.json
   cat proposed.json
   ```

4. Check the namespace's alarm. AppConfig rolls a deployment back if the
   alarm is in `ALARM`, and it will be if you have been sending documents
   that all came back `UNKNOWN`. It returns to `OK` after about 15 minutes
   without traffic, because missing data counts as not breaching.

   ```bash
   aws cloudwatch describe-alarms --alarm-names "DocClass-$NS-unknown-rate" \
     --query 'MetricAlarms[0].StateValue' --output text
   ```

5. Deploy:

   ```bash
   aws appconfig start-deployment --application-id "$APP" --environment-id "$ENV" \
     --deployment-strategy-id "$STRATEGY" --configuration-profile-id "$PROFILE" \
     --configuration-version "$VERSION"
   ```

6. Wait. The strategy rolls out over 10 minutes, then bakes for 15. During the
   rollout some calls still get the previous configuration. Watch the state
   until it is `COMPLETE`, and do not start the next deployment before then:

   ```bash
   aws appconfig get-deployment --application-id "$APP" --environment-id "$ENV" \
     --deployment-number <DeploymentNumber from start-deployment> --query State --output text
   ```

Repeat for tier 1 with `$NS-ngram`. Each proposal builds on the latest hosted
version, so approve, review and deploy one model at a time.

## 3. Tier 3: bring your own model

The service does not train tier 3. `training/sagemaker/train.py` is a
fine-tuning script you run yourself. The training state machine handles only
`ngram` and `titan`, and `propose_config` handles only those two tiers, so a
tier 3 model is deployed by Terraform and enabled in the configuration by
hand. `training/sagemaker/evaluate.py` and `promote.py` are helpers for
building an evaluation report and a proposed tier entry. The service does not
call them.

1. Build the training set. `train.py` reads `train.jsonl` from its `--train`
   directory: one JSON object per line with `text` and `class_id`. The tier
   sends the endpoint the first 2 pages, normalised by `docclass.text.prepare`,
   so prepare the training text the same way. From the repository root:

   ```bash
   aws s3 sync "s3://$DATA/labelled/$NS/" labelled/
   mkdir -p data/train
   python3 - <<'EOF'
   import json, pathlib
   from docclass.core.types import DocumentText
   from docclass.text import prepare
   with open("data/train/train.jsonl", "w") as out:
       for f in sorted(pathlib.Path("labelled").glob("*.json")):
           rec = json.loads(f.read_text())
           if rec.get("document"):
               text = prepare(DocumentText.from_dict(rec["document"]), 2)
               out.write(json.dumps({"text": text, "class_id": rec["class_id"]}) + "\n")
   EOF
   ```

   This copies labelled documents to your machine. Delete `labelled/` and
   `data/` when you finish.

2. Train. `--enabled-for` is the comma-separated classes the model should
   answer for. Every other class is trained as `OTHER`. `--model-version` is
   written into the model and returned with every prediction. The other
   required arguments are `--base-model`, the pretrained encoder to fine-tune
   (a small, distilled BERT-style text encoder is the intended size, so it
   fits a serverless endpoint), and `--base-model-revision`, the exact commit
   to download. Choose the model, and review its licence and terms, yourself.
   The optional arguments are `--epochs` (3), `--batch-size` (16),
   `--learning-rate` (5e-5), `--max-length` (512), `--train` (default
   `$SM_CHANNEL_TRAIN` or `data/train`) and `--model-dir` (default
   `$SM_MODEL_DIR` or `model`). It needs PyTorch and Hugging Face
   Transformers, and runs on your machine or in a SageMaker training job you
   start yourself. The pinned versions below have no known vulnerabilities
   at the time of writing, and a model trained with them loads and scores
   unchanged in the `huggingface-pytorch-inference:2.6.0-transformers5.5.3`
   serving image used in step 4. Transformers 5 loads only fast tokenizers,
   so the base model must include a `tokenizer.json`.

   ```bash
   pip install torch==2.14.1 transformers==5.20.2 accelerate==1.15.0
   python3 training/sagemaker/train.py --enabled-for W2,INVOICE \
     --base-model "$BASE_MODEL" --base-model-revision "$BASE_MODEL_REVISION" \
     --model-version smkr-20261014 --train data/train --model-dir model
   ```

   The model directory holds the model, the tokenizer and `docclass.json`
   (labels, model version, maximum length).

3. Package. `inference.py` defines `model_fn`, `input_fn`, `predict_fn` and
   `output_fn`, and its `model_fn` reads `docclass.json` from the model
   directory, so the archive holds the model files at its root and the
   handler under `code/`, the layout the Hugging Face inference container
   expects. A model packaged this way (`config.json`, `model.safetensors`,
   the tokenizer files, `docclass.json` and `code/inference.py`) was deployed
   to a tier 3 endpoint that reached `InService` in testing:

   ```bash
   mkdir -p model/code
   cp training/sagemaker/inference.py model/code/
   tar -C model -czf model.tar.gz .
   aws s3 cp model.tar.gz "s3://$ARTEFACTS/training/$NS/smkr-20261014/model.tar.gz"
   ```

   The endpoint role can read only `training/*` and `artefacts/*` in the
   artefact bucket.

4. Deploy the endpoint. In `examples/pooled/terraform.tfvars`, keyed by
   namespace:

   ```hcl
   tier3_endpoints = {
     "team-a" = {
       image_uri         = "763104351884.dkr.ecr.eu-west-1.amazonaws.com/huggingface-pytorch-inference:2.6.0-transformers5.5.3-cpu-py312-ubuntu22.04"
       model_data_url    = "s3://docclass-artefacts-ACCOUNT_ID/training/team-a/smkr-20261014/model.tar.gz"
       memory_size_in_mb = 4096
       max_concurrency   = 5
     }
   }
   ```

   Use an image from your Region. `memory_size_in_mb` is 1024 to 6144 in steps
   of 1024, and `max_concurrency` is 1 to 200. Then apply from
   `examples/pooled`, with the same Region as before:

   ```bash
   terraform apply
   terraform output -json tier3_endpoints
   ```

   A non-empty `tier3_endpoints` turns on `enable_tier3`, which creates the
   endpoint `docclass-<namespace>` and lets the Classify function invoke it.

5. Add the tier to the namespace's configuration. Start from the latest
   hosted version, append a `sagemaker` tier, and change `config_version`,
   because the Classify function caches a namespace's cascade by
   `config_version`:

   ```bash
   VERSION=$(aws appconfig list-hosted-configuration-versions --application-id "$APP" \
     --configuration-profile-id "$PROFILE" --max-results 1 --query 'Items[0].VersionNumber' --output text)
   aws appconfig get-hosted-configuration-version --application-id "$APP" \
     --configuration-profile-id "$PROFILE" --version-number "$VERSION" current.json
   python3 - "$NS" <<'EOF'
   import json, sys
   ns = sys.argv[1]
   cfg = json.load(open("current.json"))
   cfg["tiers"] = [t for t in cfg["tiers"] if t["name"] != "sagemaker"]
   cfg["tiers"].append({"name": "sagemaker", "endpoint_name": f"docclass-{ns}",
                        "enabled_for": ["W2", "INVOICE"], "model_version": "smkr-20261014",
                        "thresholds": {"tau": 0.9, "delta": 0.1}})
   cfg["config_version"] = (cfg["config_version"] + "+sagemaker-smkr-20261014")[:120]
   json.dump(cfg, open("proposed.json", "w"))
   EOF
   aws appconfig create-hosted-configuration-version --application-id "$APP" \
     --configuration-profile-id "$PROFILE" --content fileb://proposed.json \
     --content-type application/json --latest-version-number "$VERSION" created.json
   ```

   | Field | Meaning |
   |---|---|
   | `endpoint_name` | `docclass-<namespace>`, the endpoint from step 4 |
   | `enabled_for` | Non-empty list of classes, all in the configuration's classes |
   | `model_version` | Optional. If set, a response with another version is rejected |
   | `thresholds` | `tau` (default 0.9) and `delta` (default 0.1). `per_class` overrides are allowed |
   | `gate_top_k` | Optional, default 3. See below |
   | `endpoint_timeout_ms` | Optional, default 8000 |

   Then check the alarm, start the deployment with the new version number
   from `created.json`, and wait, as in steps 4 to 6 of
   [Approve and deploy](#approve-and-deploy).

Tier 3 runs only for documents where an earlier tier put an `enabled_for`
class among its top `gate_top_k` scores. Until tier 1 or tier 2 is
provisioned and scoring, tier 3 abstains with `not gated`.

## 4. Removing a consumer

Onboarding creates resources outside Terraform, so `terraform destroy` does
not remove them. `scripts/remove_consumer.py` removes them for one namespace.
It lists what it would delete unless you pass `--apply`:

```bash
python3 scripts/remove_consumer.py --namespace team-a
python3 scripts/remove_consumer.py --namespace team-a --apply
```

If AppConfig refuses because the namespace was used in the last hour (see
step 5), wait, or add `--bypass-deletion-protection`. To do it by hand
instead, for each namespace, from `examples/pooled`:

1. Read the consumer's ids:

   ```bash
   NS=team-a
   read -r CLIENT_ID API_KEY_ID PLAN_ID PROFILE ENV < <(aws dynamodb get-item --table-name docclass-consumers \
     --key "{\"pk\": {\"S\": \"ns#$NS#consumer\"}}" \
     --query 'Item.[client_id.S, api_key_id.S, usage_plan_id.S, appconfig_profile_id.S, appconfig_environment_id.S]' \
     --output text)
   APP=$(aws appconfig list-applications --query "Items[?Name=='docclass'].Id" --output text)
   POOL=$(aws cognito-idp list-user-pools --max-results 60 \
     --query "UserPools[?Name=='docclass-consumers'].Id" --output text)
   API_ID=$(aws apigateway get-rest-apis --query "items[?name=='docclass-api'].id" --output text)
   ```

2. Remove the API key and usage plan:

   ```bash
   aws apigateway delete-usage-plan-key --usage-plan-id "$PLAN_ID" --key-id "$API_KEY_ID"
   aws apigateway update-usage-plan --usage-plan-id "$PLAN_ID" \
     --patch-operations op=remove,path=/apiStages,value="$API_ID:live"
   aws apigateway delete-usage-plan --usage-plan-id "$PLAN_ID"
   aws apigateway delete-api-key --api-key "$API_KEY_ID"
   ```

3. Remove the Cognito app client:

   ```bash
   aws cognito-idp delete-user-pool-client --user-pool-id "$POOL" --client-id "$CLIENT_ID"
   ```

4. Stop any AppConfig deployment in progress, then delete the hosted
   configuration versions:

   ```bash
   for n in $(aws appconfig list-deployments --application-id "$APP" --environment-id "$ENV" \
       --query "Items[?State=='DEPLOYING' || State=='BAKING'].DeploymentNumber" --output text); do
     aws appconfig stop-deployment --application-id "$APP" --environment-id "$ENV" --deployment-number "$n"
   done
   for v in $(aws appconfig list-hosted-configuration-versions --application-id "$APP" \
       --configuration-profile-id "$PROFILE" --query 'Items[].VersionNumber' --output text); do
     aws appconfig delete-hosted-configuration-version --application-id "$APP" \
       --configuration-profile-id "$PROFILE" --version-number "$v"
   done
   ```

5. Delete the environment and the configuration profile. AppConfig deletion
   protection refuses to delete an environment or profile that has been
   polled in the last 60 minutes (the account default). The Classify, Labels
   and Dedup functions poll on every call for the namespace, so wait an hour
   after the consumer's last call. Or add `--deletion-protection-check BYPASS`
   to both commands, knowing that it skips that check:

   ```bash
   aws appconfig delete-environment --application-id "$APP" --environment-id "$ENV"
   aws appconfig delete-configuration-profile --application-id "$APP" --configuration-profile-id "$PROFILE"
   ```

6. Delete the alarm:

   ```bash
   aws cloudwatch delete-alarms --alarm-names "DocClass-$NS-unknown-rate"
   ```

7. Delete the registry items. Delete any `iam#<role ARN>` items the same way
   if you onboarded with `iam_principals`:

   ```bash
   aws dynamodb delete-item --table-name docclass-consumers --key "{\"pk\": {\"S\": \"ns#$NS#consumer\"}}"
   aws dynamodb delete-item --table-name docclass-consumers --key "{\"pk\": {\"S\": \"client#$CLIENT_ID\"}}"
   ```

These are left behind and are yours to delete or keep:

- SageMaker model package groups `<NS>-ngram` and `<NS>-titan`. Delete their
  model packages (`aws sagemaker list-model-packages`, then
  `delete-model-package`) before `delete-model-package-group`.
- Training job logs in the log group `/aws/sagemaker/TrainingJobs`, in streams
  that start with each `job_name`. SageMaker creates this log group, so
  Terraform does not remove it, and it has no retention set.
- The namespace's data: `inbox/<NS>/`, `text/<NS>/`, `labelled/<NS>/` and
  `exemplars/<NS>/` in the data bucket, `artefacts/<NS>/` and
  `training/<NS>/` in the artefact bucket, and its items in the
  `docclass-jobs` and `docclass-decisions` tables.
- A tier 3 endpoint: remove the namespace from `tier3_endpoints` and apply.
