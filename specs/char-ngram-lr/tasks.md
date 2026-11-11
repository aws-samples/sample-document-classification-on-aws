# Tasks: Char n-gram TF-IDF + logistic regression

- **Status:** Approved (2026-10-02)
- **Design:** [design.md](design.md)
- **Blocked by:** classifier-contract tasks 1–4

- [x] **1. Shared `prepare()`.** *NGRAM-AC-02, 18*
- [x] **2. Dataset loader and grouped split.** *NGRAM-AC-05*
- [x] **3. Trainer: vectoriser, logistic regression, class exclusion,
  conditional calibration, centroids.** *NGRAM-AC-01, 03, 04, 06, 24*
- [x] **4. Threshold sweep and training report.** *NGRAM-AC-07, 08, 09*
- [x] **5. `char_wb_ngrams` re-implementation, with a reference-string test.**
  *(Prerequisite for 7.)*
- [x] **6. Exporter to `.npz`.** *NGRAM-AC-10*
- [x] **7. NumPy scorer and parity tests.** *NGRAM-AC-11, 12*
- [x] **8. `CharNgramTier`: load guard and decision rule.** *NGRAM-AC-13, 14,
  15, 16, 17, 19, 23, 25, 26*
- [x] **9. OCR-noise generator and robustness test.** *NGRAM-AC-20*
- [x] **10. Scoring and load benchmarks, plus the AST import test.**
  *NGRAM-AC-11, 21, 22*
- [ ] **11. First real training run** on the labelled set. Put the measured accuracy
  in the training report, and in ADR-0003 in place of the indicative figure
  while that ADR is still Proposed. *Open: needs a real labelled set, which does
  not exist yet; the synthetic and public-form runs in the tests are not a substitute.*
