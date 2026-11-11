# 0003. Character n-gram TF-IDF + logistic regression as tier 1

- **Status:** Accepted (2026-10-02)
- **Date:** 2026-10-02
- **Spec:** [../specs/char-ngram-lr/](../specs/char-ngram-lr/)
- **Related:** 0001, 0004

## Context

Most documents in scope are **fixed-format forms**. Each one identifies itself
through boilerplate: a title, a form number, the issuer's name, and standard
legal text. The text often comes from OCR, which corrupts characters
(`Borrovver`, `1O40`, `rn` for `m`). A word-level model treats each corrupted
word as a new, unseen token and loses the signal.

What tier 1 needs to be:

- free per document, so it can run on every document;
- fast enough to run inside a function, with no network call;
- trainable in seconds from tens of examples per class;
- able to abstain on documents it does not recognise.

## Decision

1. **Features:** character n-grams within word boundaries (`char_wb`, n = 3–5),
   with sublinear TF-IDF weighting, fitted on normalised page-1 and page-2
   text.
2. **Model:** multinomial logistic regression with balanced class weights,
   with probabilities calibrated by sigmoid calibration when each class has
   enough data.
3. **Accept** only when all three hold: top probability ≥ `τ₁`, top-two margin
   ≥ `δ₁`, and cosine similarity to the predicted class's TF-IDF centroid ≥
   `ρ₂`. The centroid check is the out-of-distribution guard. Logistic
   regression always picks one of the classes it knows, so a document from an
   unseen class can still score high.
4. **Train with scikit-learn, offline. Serve from an exported artefact**
   (vocabulary, IDF, coefficients, intercepts, centroids, thresholds) scored
   with NumPy, so the runtime does not ship SciPy or scikit-learn.
5. **Exported scoring must match scikit-learn** within 1e-6 on a held-out set.
   A test enforces this at export time.

## Alternatives considered

| Alternative | Why it lost |
|---|---|
| Word TF-IDF + logistic regression | OCR errors and form numbers break the tokens. Measurably weaker on degraded scans |
| Keyword and regex rules per class | Brittle, needs hand-maintenance for every class and edition, and has no calibrated confidence |
| Naive Bayes | Faster to train, but its probabilities are poorly calibrated, which undermines thresholds (ADR-0001 §5) |
| Linear SVM | Similar accuracy, but no native probabilities. Calibration would need extra data |
| Gradient-boosted trees over n-grams | Larger artefact and slower to score, with no accuracy gain on boilerplate-heavy text |
| Shipping scikit-learn in the function | Close to the function package-size limit, with slower cold starts, for code that inference does not need |

## Consequences

- **Positive.** Zero cost per document and scoring in milliseconds. Expected to
  resolve most fixed-format forms (~95–98% on visually distinct forms; to be
  replaced by measured numbers). Retraining takes seconds.
- **Negative.** Needs ~10–50 labelled documents per class before a class can be
  enabled. Weak on look-alike editions whose boilerplate is almost identical,
  which are left to tiers 2 and 3. The exporter is extra code that must stay in
  step with the trainer.
- **Neutral.** Every retrain bumps `model_version` and needs a fresh threshold
  sweep.

## AWS integration

- **Runtime:** in-process, wherever the cascade runs. Only the NumPy scorer is
  needed at classification time.
- **Artefact:** a versioned `.npz` file, loaded once per process.
- **Training:** scikit-learn, locally or in a SageMaker Training Job using the
  scikit-learn framework container.

## Revisit when

- Measured selective accuracy at the target precision stays below the target
  for a class family: escalate those classes to tier 3 (ADR-0005).
- The class count grows so large (hundreds or more) that the artefact or the
  scoring time becomes a cold-start problem.
