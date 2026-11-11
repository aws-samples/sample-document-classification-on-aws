# Architecture Decision Records

One decision per file, numbered in order, never renumbered.

| ADR | Title | Status |
|---|---|---|
| [0001](0001-classifier-cascade-and-contract.md) | Classifier cascade and shared contract | Accepted |
| [0003](0003-char-ngram-tfidf-logistic-regression.md) | Character n-gram TF-IDF + logistic regression as tier 1 | Accepted |
| [0004](0004-titan-embedding-nearest-class.md) | Titan embeddings, nearest class, as tier 2 | Accepted |
| [0005](0005-sagemaker-serverless-classifier.md) | Trained classifier on SageMaker Serverless as tier 3 | Accepted |
| [0006](0006-serverless-service-shape.md) | Serverless service shape: API, orchestration and results | Accepted |
| [0007](0007-consumers-namespaces-and-isolation.md) | Consumers, namespaces and isolation | Accepted |
| [0008](0008-text-acquisition.md) | Text acquisition: text layer first, Textract for scans | Accepted; PDF library superseded by [0011](0011-permissive-pdf-library.md) |
| [0009](0009-model-lifecycle.md) | Model lifecycle: train, register, promote by reviewed config | Accepted |
| [0010](0010-packaging-terraform-module.md) | Packaging: one Terraform module for pooled and silo deployments | Accepted |
| [0011](0011-permissive-pdf-library.md) | PDF handling with pypdf, a permissively licensed library | Accepted |

0002 (an exact-duplicate cache tier) was removed with the tier, so its number
is a gap.

**Lifecycle:** `Proposed` → `Accepted` → (`Deprecated` | `Superseded by NNNN`).
Once an ADR is Accepted, only its status line changes. A change of direction
gets a new ADR that links back to the one it replaces.

## Template

```markdown
# NNNN. Title

- **Status:** Proposed
- **Date:** YYYY-MM-DD
- **Spec:** ../specs/<component>/
- **Related:** NNNN

## Context
The forces at play: the problem, the constraints, what is known and unknown.

## Decision
What we will do, stated in full sentences.

## Alternatives considered
Each option, with the reason it lost.

## Consequences
What becomes easier, what becomes harder, what we must now do.
Positive, negative and neutral, all stated.

## Revisit when
Concrete, observable triggers for reopening this decision.
```
