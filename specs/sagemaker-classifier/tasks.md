# Tasks: SageMaker Serverless classifier

- **Status:** Approved (2026-10-02)
- **Design:** [design.md](design.md)
- **Blocked by:** classifier-contract tasks 1–7. **Do not start** until
  evaluation of tiers 1 and 2 identifies the class families tier 3 must cover
  (ADR-0005 decision 5).

- [x] **0. Prerequisite:** `CascadeContext` in classifier-contract (CORE-AC-22,
  task 8b). *(Prerequisite for SMKR-AC-01..03; the SMKR criteria are cited in task 4.)*
- [x] **1. `EndpointClient` protocol and `FakeEndpoint`.** *SMKR-AC-18*
- [x] **2. Request builder with payload truncation.** *SMKR-AC-04, 14*
- [x] **3. Response validator.** *SMKR-AC-05, 06, 07*
- [x] **4. Gating on earlier attempts and option validation.** *SMKR-AC-01, 02, 03, 23*
- [x] **5. Decision rule and `OTHER` handling.** *SMKR-AC-08, 09, 10, 22, 24*
- [x] **6. Error taxonomy, retry on throttle only.** *SMKR-AC-11, 12, 13*
- [x] **7. Overhead benchmark.** *SMKR-AC-20*
- [x] **8. Training, inference handler and held-out evaluation report.**
  *SMKR-AC-16*
- [x] **9. Promotion gate.** *SMKR-AC-15, 17*
- [x] **10. `serverless_config` helper** with explicit, validated memory and
  concurrency. *SMKR-AC-21*
- [x] **11. Gated real-endpoint contract test.** *SMKR-AC-19*
