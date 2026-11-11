# Tasks: Classifier contract and cascade

- **Status:** Approved (2026-10-02)
- **Design:** [design.md](design.md)

Each task begins with failing tests for the criteria it cites.

- [x] **1. Project skeleton and traceability marker.** Package layout, pytest
  config, and an `ac` marker registered with a check that fails on unknown
  IDs. *(Enables all criteria.)*
- [x] **2. Core types.** `PageText`, `DocumentText`, `ClassDecision` with
  validation, and `DecisionFactory`. *CORE-AC-02, 03, 04, 05*
- [x] **3. Classifier protocol and `FakeTier`.** *CORE-AC-01*
- [x] **4. Configuration loader.** Parse, validate and register tiers.
  *CORE-AC-11, 12, 13*
- [x] **5. Sequential cascade.** Ordering, early return, and the `UNKNOWN`
  fallback. *CORE-AC-06, 07, 08*
- [x] **6. Error and timeout isolation.** *CORE-AC-09, 10*
- [x] **7. Audit trail.** Attempts, hashes and versions on `CascadeResult`.
  *CORE-AC-14, 15, 16*
- [x] **8. Agreement mode.** *CORE-AC-17, 18*
- [x] **8b. Optional `CascadeContext`.** *CORE-AC-22*
- [x] **9. Overhead benchmark and dependency check.** A timing test with
  four no-op tiers, plus an AST import check (no import-linter dependency). *CORE-AC-19, 20*
- [x] **10. Traceability gate.** `tools/check_traceability.py`, run by
  `tests/test_traceability.py`. Fails if any criterion of an Approved or
  Implemented spec lacks a test, or a marker names an undefined criterion.
