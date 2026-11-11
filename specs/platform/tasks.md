# Tasks: Serverless service

- **Status:** Approved (2026-10-02)
- **Design:** [design.md](design.md)
- **Blocked by:** classifier-contract (all tasks);
  char-ngram-lr tasks 1–8 (tier 1 is enabled per namespace once trained)

Tasks 1–6 give a working tier 2 service. Tier 1 joins when its spec is
done, and tier 3 last.

- [x] **1. Module skeleton:** KMS, buckets, tables, bus, minimal inputs.
  *PLAT-AC-29, 32*
- [x] **2. Consumers:** Cognito pool, scopes, `consumers` table, admin route,
  usage plans, API-key binding, AppConfig onboarding. *PLAT-AC-13, 16, 18, 38, 41*
- [x] **3. Data-access role with session tags, plus the cross-namespace denial
  test.** *PLAT-AC-15*
- [x] **4. Classify Lambda:** AppConfig load, artefact cache, cascade, EMF,
  log hygiene. *PLAT-AC-19, 25, 26*
- [x] **5. Sync API.** *PLAT-AC-06, 07, 08*
- [x] **6. Async path:** jobs API, presigned upload, starter with
  name-idempotency, document state machine (text-layer only), events, polling.
  *PLAT-AC-01, 02, 03, 04, 05, 09, 11, 12, 14, 40*
- [x] **7. Textract path:** task token, SNS callback, OCR merge. *PLAT-AC-10*
- [x] **8. Namespace concurrency semaphore** with guaranteed release, and the
  reaper. *PLAT-AC-17, 39*
- [x] **9. AppConfig validator and rollback alarm.** *PLAT-AC-20, 23*
- [x] **10. Labels route and training workflow** (tier 1 job, tier 2 index
  build), with the registry gate and proposed config. *PLAT-AC-21, 22, 24*
- [x] **11. Alarms, dashboards and X-Ray.** *PLAT-AC-27, 28*
- [x] **12. IAM least privilege and policy tests.** *PLAT-AC-30*
- [x] **13. VPC mode** with interface endpoints. *PLAT-AC-31*
- [x] **14. Tier 3 resources behind `enable_tier3`.** *PLAT-AC-35* (needs the
  sagemaker-classifier spec)
- [x] **15. Release packaging:** versioned Lambda zips, `release_version`
  input, examples planned in CI. *PLAT-AC-33, 34*
- [x] **16. Latency benchmarks on the deployed example.** *PLAT-AC-36, 37*
- [x] **17. Rollout-safe configuration reads:** `NamespaceNotConfigured` from the
  provider, retried by the dedup and classify states. *PLAT-AC-42*
