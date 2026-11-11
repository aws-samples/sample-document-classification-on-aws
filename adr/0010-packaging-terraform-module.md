# 0010. Packaging: one Terraform module for pooled and silo deployments

- **Status:** Accepted (2026-10-02)
- **Date:** 2026-10-02
- **Spec:** [../specs/platform/](../specs/platform/)
- **Related:** 0006, 0007

## Context

"Many people can use it" means two things: many consumers calling one
deployment (ADR-0007), and many teams able to stand up their own deployment.
The second needs packaging that is versioned, parameterised and easy to
upgrade.

## Decision

1. **Ship one Terraform module**, `modules/doc-classification`, compatible with
   both OpenTofu and Terraform (≥ 1.10). Its inputs are `name`, `region`,
   `vpc_mode` (`none` | `byo`), `subnet_ids?`, `enable_tier3`, `kms_key_arn?`
   and `alarm_sns_topic_arn?`. Its outputs are the API URL, the event bus ARN
   and the Cognito issuer.
2. **Lambda code ships as versioned zip artefacts** in a release bucket. The
   module takes `release_version` as input, so an upgrade is a one-line change
   plus a plan.
3. **State per deployment** in an S3 backend with native locking.
4. **A deployment never hard-codes another deployment's resources.** Shared
   inputs, such as a VPC or KMS key, come in through variables.
5. **Every release ships an `examples/pooled` and an `examples/silo`
   configuration**, each of which is planned in CI.

## Alternatives considered

| Alternative | Why it lost |
|---|---|
| AWS CDK construct library | A strong option, but it ties consumers to a CDK language toolchain, and teams already standardised on Terraform would have to adopt CDK. Rejected 2026-10-02: the project owner chose Terraform |
| AWS SAM / CloudFormation templates | Weaker composition for the many optional pieces (VPC mode, tier 3, alarms) |
| AWS Service Catalog product | Useful as a *distribution* layer on top of the module, later. Not a replacement for it |

## Consequences

- **Positive.** One artefact serves both models of use. Upgrades are explicit
  and plannable.
- **Negative.** Teams on CDK must wrap the module or use a separate
  distribution.

## Revisit when

- Most adopting teams turn out to be on CDK.
