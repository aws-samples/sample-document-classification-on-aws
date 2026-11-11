# Specs

Each component directory holds three files:

| File | Answers | Changes when |
|---|---|---|
| `requirements.md` | **What** the component must do, as testable acceptance criteria | Behaviour changes |
| `design.md` | **How** it does it: interfaces, data shapes, algorithms, error handling | The implementation approach changes |
| `tasks.md` | **In what order** it gets built, with each task traced to criteria | Work is planned or completed |

The *why* lives in the component's ADR under `../adr/`, not here.

## Requirement format

Requirements are user stories. Each one carries acceptance criteria written in
EARS form (Easy Approach to Requirements Syntax), so every criterion is a
single testable sentence:

| Pattern | Template |
|---|---|
| Ubiquitous | THE `<component>` SHALL `<response>` |
| Event-driven | WHEN `<trigger>` THE `<component>` SHALL `<response>` |
| State-driven | WHILE `<state>` THE `<component>` SHALL `<response>` |
| Unwanted behaviour | IF `<condition>` THEN THE `<component>` SHALL `<response>` |
| Optional feature | WHERE `<feature is enabled>` THE `<component>` SHALL `<response>` |

**IDs.** `<PREFIX>-AC-<NN>`, numbered within a component and never reused. A
dropped criterion is marked `~~struck~~ (withdrawn: reason)` rather than
deleted, so the IDs in old tasks and commits still resolve.

| Component | Prefix |
|---|---|
| classifier-contract | `CORE` |
| char-ngram-lr | `NGRAM` |
| titan-nearest-class | `TITAN` |
| sagemaker-classifier | `SMKR` |

## Spec status

| Status | Meaning |
|---|---|
| Draft | Under discussion. Do not implement |
| Approved | Reviewed. Implementation may start, beginning with `tasks.md` task 1 |
| Implemented | Every criterion is implemented |
| Superseded | Replaced; the header links its successor |
