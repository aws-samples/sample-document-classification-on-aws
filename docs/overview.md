# Document Classification and Extraction: Abstract Design

## Abstract

This document describes a document classification and extraction pipeline that
does not depend on any one product. A document arrives (usually a PDF, often
scanned), its text is obtained once, and a **cascade of interchangeable
classifiers** decides what kind of document it is. Each classifier either
answers with a calibrated confidence or **abstains**. The first confident answer
wins. The resolved class then selects an **extraction recipe** that reads the
class's fields from the same text.

> **Sensitive data.** The documents this pipeline is meant for, such as tax
> forms, bank statements and invoices, routinely contain personal data (PII),
> and may contain health (PHI) or payment card (PCI) data. Anyone who deploys
> it to process regulated data is responsible for meeting the applicable
> requirements, for example HIPAA, PCI DSS or GDPR. See the
> [AWS shared responsibility model](https://aws.amazon.com/compliance/shared-responsibility-model/)
> and [AWS compliance programs](https://aws.amazon.com/compliance/programs/).

Three classification methods are specified behind one interface, ordered
from cheapest to most expensive:

1. **Character n-gram TF-IDF + logistic regression.** A small trained model
   that runs inside the function and tolerates OCR noise.
2. **Embedding nearest-class (Amazon Titan).** Needs no training, and adding a
   new class takes a few labelled examples.
3. **Trained classifier on SageMaker Serverless.** The highest-capacity option,
   for classes the cheaper tiers cannot separate.

Nothing in this design assumes a particular tenant model, storage layout,
workflow engine or UI. Those belong to whatever system adopts it, through the
interfaces in §4.

---

## 1. Problem and scope

**In scope**

- Assigning a document, or a page range within a bundle, to one of a closed set
  of classes, or to `UNKNOWN`.
- Extracting a declared set of typed fields for the resolved class.
- Reporting confidence and provenance for every decision, so a downstream
  system can route low-confidence work to a human.

**Out of scope**

- Splitting a multi-document bundle into separate documents. This design takes
  page ranges as input. A splitter can be built from the same classifiers run
  per page, but that is a separate design.
- Business validation of extracted values, such as cross-field rules or
  reconciliation against external records.
- Storage, identity, authorisation and review UI.

**Design goals**

| Goal | Consequence |
|---|---|
| Cheap by default | Most documents should resolve in the zero-cost tiers. Paid tiers run only on what the cheaper tiers abstain from |
| Abstain rather than guess | Every method must be able to say "not sure". A wrong confident class is worse than `UNKNOWN`, because it extracts plausible fields off the wrong form |
| Swappable methods | Methods share one interface and one result type, so a tier can be added, removed or reordered by configuration |
| Reproducible | A decision records the method, the model version and the input hash, so it can be explained and replayed |

---

## 2. Pipeline overview

```
            ┌──────────────┐
 document ─▶│ Text         │  text per page + layout (optional)
            │ acquisition  │  input hash (file bytes)
            └──────┬───────┘
                   ▼
            ┌──────────────────────────────────────────────────────┐
            │ Classification cascade                               │
            │  1. Char n-gram TF-IDF + LR ──p ≥ τ₁─────────────┐   │
            │  2. Titan embedding nearest-class ──s ≥ τ₂───────┤   │
            │  3. SageMaker Serverless classifier ──p ≥ τ₃─────┤   │
            │  all abstain ──▶ UNKNOWN (human review)          │   │
            └──────────────────────────────────────────────────┼───┘
                                                class → recipe ▼
                                                ┌──────────────────┐
                                                │ Extraction       │
                                                │ (per-class       │
                                                │  recipe)         │
                                                └────────┬─────────┘
                                                         ▼
                                            fields + confidence + provenance
```

**Text acquisition happens once and every later stage shares it.** Every
classifier and extractor works on the same `DocumentText`. Running OCR again
per stage costs money and lets the stages disagree about what the page says.

- **Born-digital PDFs:** take the embedded text layer (for example `pdftotext`
  or pypdf). This is free and exact.
- **Scanned or image-only pages:** use OCR (for example Amazon Textract
  `DetectDocumentText`, or Tesseract offline). Decide per page: a page with no
  text layer, or with a layer under a minimum character count, goes to OCR.
- **Normalisation for classification:** lower-case, collapse whitespace, drop
  digits-only tokens, and keep the first N pages (default 2). Most forms
  identify themselves in the header and footer of page 1, and the variable
  content further down only adds noise.

---

## 3. Classification methods

### 3.1 Comparison

| Method | How it works | Accuracy (indicative) | Cost per document | Latency | Training data | Adding a class |
|---|---|---|---|---|---|---|
| Char n-gram TF-IDF + LR | Character 3–5-grams, TF-IDF weighting, multinomial logistic regression | ~95–98% on visually distinct forms | ~$0 (in-process) | ms | ~10–50 labelled docs per class | Retrain (seconds) |
| Titan embeddings, nearest class | Embed the text and compare by cosine similarity to per-class centroids or exemplars | High on semantically distinct classes. Weaker on near-identical form variants | Per-token embedding charge | ~100 ms | 1–10 exemplars per class | Add exemplars. No retrain |
| SageMaker Serverless classifier | Fine-tuned text (or text + layout) model behind a serverless endpoint | Highest ceiling, especially for look-alike variants | Per-ms compute while invoked | ms warm, seconds cold | Hundreds+ labelled docs | Retrain and redeploy |

The accuracy figures are planning estimates. §6 says how to replace them with
measured numbers for a given class set.

### 3.2 Character n-gram TF-IDF + logistic regression

**Why character n-grams.** Word tokenisation breaks on OCR errors (`Borrovver`,
`1O40`) and on form numbers. Character 3–5-grams still share most of their
features with the clean spelling, so the model degrades gracefully. It is the
same family of features as language identification, and it is strong at
recognising boilerplate, which is exactly what makes a form a form.

**Model**

```
TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5),
                sublinear_tf=True, min_df=2, max_features=200_000)
LogisticRegression(C=10, max_iter=2000, class_weight="balanced")
CalibratedClassifierCV(method="sigmoid")   # when per-class data allows
```

**Abstention.** Abstain when the top probability is below `τ₁`, **or** the
margin between the top two classes is below `δ₁`. The margin test catches
look-alike pairs (a W-2 against a W-2c, a 1120 against an 1120-S) where both
probabilities are moderate.

**Out-of-distribution guard.** Logistic regression is forced to choose among
known classes, so a document from a class it has never seen can still come back
confident. Add an outlier check: the cosine similarity between the document's
TF-IDF vector and its predicted class centroid must exceed `ρ₂`, otherwise
abstain.

**Running in a function runtime (Lambda).** scikit-learn plus SciPy and NumPy
sits close to the 250 MB unzipped package limit. There are two options:

- **Container image, or a layer with the scientific stack.** Simplest, and
  inference uses the real library.
- **Export for dependency-light inference (recommended).** At inference time
  the model is just a vocabulary map, an IDF vector, a coefficient matrix and
  an intercept vector. Export them, then score with NumPy, or even pure Python
  over sparse dicts. This keeps the package small and cold starts fast. Train
  offline with scikit-learn, export the artefact, and assert in a test that the
  exported scorer matches `predict_proba` on a held-out set.

**Artefact.** `{model_version, taxonomy_version, vocabulary, idf, coef,
intercept, classes, thresholds, centroids}`, stored versioned and loaded once
per cold start.

### 3.3 Titan embeddings, nearest class

**Purpose.** This tier classifies with few or no labelled examples. It is useful
right after a new class is introduced, before there is enough data to retrain
§3.2, and as a semantic check when the text is short or unusual.

**Method**

1. Embed the normalised text, truncated to the model's input limit, with Amazon
   Titan Text Embeddings (for example V2, with dimension and normalisation
   chosen once and recorded in the artefact).
2. For each class, keep either a **centroid** (the mean of its exemplar
   embeddings, re-normalised) or the **exemplars themselves** (k-nearest
   neighbours, k = 3–5). Exemplars handle classes with several distinct
   layouts better than one centroid does.
3. Score each class by cosine similarity. Accept the top class when similarity
   is at least `τ₂` and the margin over the runner-up is at least `δ₂`.

**Notes**

- **Embedding the same text twice wastes money.** Cache embeddings by text hash.
- **Near-identical variants are the known weakness.** Embeddings capture
  meaning, and two editions of the same form mean the same thing. Use this tier
  to recognise the family, and leave variant discrimination to §3.2 or §3.4.
- **Changing the embedding model or the dimension invalidates every stored
  vector.** Record `embedding_model_id` and `dimensions` in the artefact and
  refuse to compare vectors whose metadata differs.
- Pricing and limits change. Check current Bedrock pricing and the model's
  token limit when sizing.

### 3.4 Trained classifier on SageMaker Serverless

**Purpose.** This is the last automated tier, for documents that the cheaper
tiers abstain on. It is the place for a fine-tuned transformer (text-only, or a
layout-aware model if bounding boxes are available) that can separate
look-alike classes.

**Deployment shape**

- **SageMaker Serverless Inference.** No instances to manage, and charges
  accrue only while a request is running. It has a memory cap (up to 6 GB),
  no GPU, and bounded concurrency per endpoint, so pick a model that runs on
  CPU within those limits. A distilled encoder is the usual fit.
- **Cold starts can take seconds.** That is acceptable for an asynchronous
  pipeline and wrong for an interactive one. Use provisioned concurrency, or a
  real-time endpoint, if latency matters.
- **Contract.** Request: `{text, pages?, model_version?}`. Response:
  `{probabilities: {class_id: p}, model_version}`. Abstention is decided by the
  caller using `τ₃` / `δ₃`, not by the endpoint, so the thresholds stay in
  configuration alongside the other tiers.

**When to adopt it.** Only when evaluation (§6) shows a class pair that §3.2
and §3.3 cannot separate at the target precision. Until then it adds a
training pipeline, a model registry and an endpoint to operate, and it brings
no accuracy the cheaper tiers do not already provide.

### 3.5 Cascade policy

```
for method in cascade:                 # ordered cheapest → most expensive
    result = method.classify(doc)
    record(result)                     # every attempt is logged, not just the winner
    if result.accepted:
        return result
return Decision.unknown(attempts)
```

- **Order** is by cost, and secondarily by precision. A tier that is cheap but
  imprecise belongs after a precise one, or should be dropped.
- **Thresholds are per tier and optionally per class.** A class that is
  expensive to get wrong can demand a higher `τ`.
- **Optional agreement mode.** For high-stakes classes, require two
  independent tiers to agree before accepting, rather than taking the first
  confident answer.
- **`UNKNOWN` is a valid, routable output**, not an error. It goes to human
  review, and the human's label becomes a training example (§7).

---

## 4. Abstract interfaces

Interfaces are written in language-neutral pseudo-types. A Python rendering
would use `Protocol` or `ABC` with frozen dataclasses.

```text
DocumentText
  doc_hash        : str              # sha256 of the source bytes
  pages           : list[PageText]
  source          : "text_layer" | "ocr" | "mixed"

PageText
  number          : int              # 1-based
  text            : str
  lines           : list[Line]?      # optional layout: text + bbox + confidence

ClassDecision
  class_id        : str | UNKNOWN
  confidence      : float            # calibrated, 0..1
  accepted        : bool             # passed this method's thresholds
  method          : str              # "char_tfidf_lr" | "titan_nn" | "sagemaker" | "human"
  model_version   : str
  scores          : map[class_id, float]   # top-k, for audit and margin checks
  reason          : str?             # why it abstained, if it did

Classifier                           # every tier implements this
  name            : str
  classify(doc: DocumentText) -> ClassDecision

FieldSpec
  name            : str
  type            : "string" | "number" | "date" | "currency" | "boolean"
  pages           : list[int] | "*"
  strategy        : KeyValue(aliases) | Query(question) | Model(hint) | Pattern(regex)
  required        : bool

ExtractionRecipe
  class_id        : str
  version         : int
  mode            : "rules" | "hybrid" | "model"
  fields          : list[FieldSpec]

FieldResult
  name, value, confidence
  source          : "key_value" | "query" | "model" | "pattern"
  evidence        : {page, text_span, bbox?}   # where on the page the value was read

Extractor
  extract(doc: DocumentText, recipe: ExtractionRecipe) -> list[FieldResult]
```

**Invariants the interfaces encode**

- A classifier never mutates the document.
- `confidence` means the same thing in every tier (a calibrated probability).
  Embedding similarity is mapped through a fitted calibration curve, not passed
  through raw, so thresholds are comparable across tiers.
- An `ExtractionRecipe` is looked up by `class_id` from **one** registry. The
  thing that defines a class and the thing that defines its fields must not
  live in two places that can drift apart.

---

## 5. Extraction

Classification picks the recipe, and the recipe picks the strategy per field.

| Strategy | Mechanism | Strengths | Weaknesses |
|---|---|---|---|
| Key-value | Match a form label (with aliases) and read its paired value from layout analysis (for example Textract `FORMS`) | Deterministic and cheap. Precise on fixed forms | Breaks when labels vary. Needs layout |
| Query | Ask a natural-language question of the page (for example Textract `QUERIES`) | Robust to label wording | Paid per page. One answer per question |
| Pattern | Regex or validator over text near an anchor | Free. Ideal for IDs, dates, amounts | Brittle without a good anchor |
| Model (LLM) | Prompt a language model with the page text plus field names, types and hints. Ask for structured output | Handles free-form and varied documents | Cost, latency. Can invent values |

**Modes**

- **rules:** key-value, query and pattern only.
- **hybrid:** rules first, then the model fills only fields left empty or below
  threshold.
- **model:** the model for every field. Text acquisition still runs, because
  grounding needs the text.

**Grounding is mandatory for model answers.** A model-extracted value is
accepted only if it, or a normalised form of it, appears in the page text it
claims to come from. The span is recorded as `evidence`. Ungrounded values are
returned with confidence 0 and flagged for review rather than dropped, so a
reviewer can see what the model proposed.

**A field with no strategy for the chosen mode is a configuration error raised
at load time**, not a silent empty value at run time. For example, in model
mode every field needs a hint or a description, because the model never sees
key-value aliases or queries.

---

## 6. Evaluation

**Dataset.** Hold out at least 20% of labelled documents per class, split by
**source document**, not by page, so pages from one document never land on
both sides of the split. Public blank forms are a useful smoke set, but they
are not representative. Filled, scanned and degraded copies are what
production sees, so augment with synthetic OCR noise (character substitution,
skew, low-DPI rasterisation) when real samples are scarce.

**Per-tier metrics**

- Precision and recall per class **at the operating threshold**, not overall
  accuracy.
- **Coverage:** the fraction of documents the tier accepts.
- **Selective accuracy:** accuracy on what it accepts. This number is what sets
  `τ`.
- A confusion matrix restricted to the top look-alike pairs.

**Cascade metrics**

- End-to-end precision on accepted documents. This is the main goal.
- The `UNKNOWN` rate, which is the human workload.
- Cost per 1,000 documents and p95 latency, both broken down by the tier that
  decided.

**Threshold selection.** For each tier, sweep `τ` on the validation set and
pick the lowest value that holds the target precision (for example ≥ 99.5% on
accepted documents). Re-run the sweep whenever the model or taxonomy version
changes.

---

## 7. Operations

- **Versioning.** `taxonomy_version`, `model_version` per tier, and `recipe.version`
  per class are all stamped on every decision. Use any of them as part of an
  idempotency key for paid downstream calls, so an unchanged document is never
  re-billed. Keep the types stable: the integer `2` and the string `"2"` hash
  differently.
- **Feedback loop.** Human labels on `UNKNOWN` and on overturned decisions go
  into a labelled set. Retrain §3.2 on a schedule, or when N new labels arrive.
  Add exemplars to §3.3 immediately. Retrain §3.4 only when the evaluation gap
  justifies it.
- **Drift monitoring.** Track per-tier coverage and the mean confidence of
  accepted decisions over time. A drop means the incoming documents have
  changed, for example a new form edition, before it shows up as errors.
- **Adding a class.** Add 1–10 exemplars to §3.3 and the class is live at once,
  at lower coverage. Retrain §3.2 once 10–50 examples exist. Bump
  `taxonomy_version`.
- **Removing or splitting a class.** Bump `taxonomy_version`, retrain, and
  re-evaluate.
- **Logging.** Log every attempt in the cascade, not only the winner. Without
  the abstentions it is impossible to tell whether a later tier is pulling its
  weight.

---

## 8. Recommended rollout

1. **Text acquisition + char n-gram TF-IDF/LR.** No
   per-document cost, and it is expected to cover the large majority of
   fixed-format forms.
2. **Add Titan nearest-class** as the next tier, for new classes with few
   examples and for documents the n-gram model abstains on.
3. **Measure before adding SageMaker Serverless.** Adopt it only for the
   specific look-alike pairs that §6 shows the first two learned tiers cannot
   separate at target precision.
4. **Extraction:** start each class in *rules* mode where layout is stable,
   move to *hybrid* for classes with variable labels, and reserve *model* mode
   for free-form documents. Grounding stays on in every mode.

---

## 9. Open questions

- **Page-level or document-level classification for bundles?** This decides
  whether a splitter is needed upstream (§1, out of scope).
- **Target precision on accepted documents**, and the acceptable `UNKNOWN`
  rate. Together they fix every threshold in §6.
- **Whether layout (bounding boxes) is available at classification time.** If
  it is, a layout-aware model becomes a candidate for §3.4.
- **Data residency and model-access constraints** for Bedrock and SageMaker in
  the target account and region.
