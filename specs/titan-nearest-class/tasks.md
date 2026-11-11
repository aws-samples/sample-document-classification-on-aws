# Tasks: Titan embeddings, nearest class

- **Status:** Implemented (2026-10-02); task 10 open
- **Design:** [design.md](design.md)
- **Blocked by:** classifier-contract tasks 1–4; char-ngram-lr task 1 (shared
  `prepare()`)

- [x] **1. `Embedder` protocol and `FakeEmbedder`.** *TITAN-AC-17*
- [x] **2. Index builder: vectors, centroids, skipped classes, content-addressed
  version.** *TITAN-AC-01, 02, 03, 04*
- [x] **3. NumPy isotonic calibration and threshold sweep.** *TITAN-AC-05, 06,
  07, 20, 24, 25, 26*
- [x] **4. Index loader with model, dimension and taxonomy guards.**
  *TITAN-AC-12, 13*
- [x] **5. k-NN scoring and decision rule.** *TITAN-AC-08, 09, 10, 11, 16, 21, 22*
- [x] **6. Embedding cache.** *TITAN-AC-14, 23*
- [x] **7. `BedrockTitanEmbedder` with timeouts and retries, plus
  `FailingEmbedder` tests.** *TITAN-AC-15*
- [x] **8. Scoring benchmark.** *TITAN-AC-18*
- [x] **9. Gated real-Bedrock integration test.** *TITAN-AC-19* (written; skipped
  unless `BEDROCK_IT=1`, not yet run against Bedrock)
- [ ] **10. First real index** from exemplars, with its calibration report.
  Compare its coverage against char-ngram-lr on the same validation set.
  Open: needs real labelled exemplars and Bedrock access.
