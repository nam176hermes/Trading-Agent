# P3 official operation transport correction

Status: source contract only; official execution and qualification remain HELD.
This corrects the T-P3-001/T-P3-043/T-P3-052 transport seam in V2.1. It does
not change the accepted policy set, accounting conventions, candidate family,
perturbations, thresholds, or legacy metric and registry identities.

The original transport copied `RunAuthorization.input_set_ref` into the worker
manifest field. An InputSet is not a BaselineManifest or EvaluationManifest,
and the broad OOS authorization did not distinguish A0–A3 from primary
selection. The private `P3OperationInput` now binds one exact workflow operation,
one broad operation, one InputSet, a sorted unique frozen alpha scope, and a
closed body. The definitions live in `packages/alpha_lifecycle/operation_input.py`.

| Workflow operation | Closed body | Allowed alphas |
| --- | --- | --- |
| baselines | BaselinesInput | none |
| register-family | RegisterFamilyInput | all four |
| oos-a0 through oos-a3 | CandidateOOSInput | exact corresponding alpha |
| select-primary | SelectPrimaryInput | all four |
| holdout-primary | HoldoutInput | one primary, verified by downstream selection binding |
| native-parity | NativeParityInput | the same one primary |
| phase-exit | PhaseExitInput | the same one primary |

An intent contains no authorization, review, job, attempt, lease, nonce, or
issuance-time backreference. ReviewApproval covers the intent's **semantic
digest**, calculated without its `digest` member. The worker manifest ArtifactRef
uses the **content SHA256** of the full canonical intent, including that member.
The authorization ArtifactRef likewise uses full canonical authorization bytes.
Those two digest meanings must remain distinct in persistence and inventories.

The private request file carries both the authorization and the intent.
Authorization InputSet, operation and alpha scope must equal the intent.
Staging requires a canonical current APPROVED review bound to the source,
distinct operator/reviewer identities, its evidence artifact, and a validity
window containing the entire authorization window. Dispatch additionally reads
the exact root-owned protected review. Environment checks bind the declared
GitHub repository, protected main ref, source SHA, workflow-dispatch trigger,
workflow path, run ID and attempt. Environment variables alone are not proof of
host, reviewer, or database authority. Shape preflight is explicitly
`STRUCTURE_VALIDATED` with `execution_authorized=false`.

The integration fixture remains on `_FixtureAuthorization` and cannot carry an
official operation intent. It does not require an official InputSet.

## Required downstream work before enabling official execution

Migration 0021 must preserve merged migration 0020 and accepted history. It must
persist both intent digest meanings, exact workflow operation and alpha scope,
and expose acceptance only to a separately provisioned protected authority
principal. Job API and worker credentials must not grant themselves acceptance.
Enqueue/start/commit must bind this intent and current authorization; publication
must retain the existing single event/outbox/head/job-result transaction.

Holdout consumption must be durably fenced before any plaintext access, unique
by holdout commitment, and reject another request/job after consumption. The
meaning of `holdout_input_set_ref`, the protected plaintext delivery protocol,
and native request/runtime closure still need executable contracts and tests.
`native_request_ref` is a required root, not a claim that its producer exists.
Postcommit receipt/registration/closure recovery is also required. No official
worker profile is enabled by this correction.

A failed primary retains its result and keeps phase exit HELD; there is no
fallback. LIVE_ELIGIBLE=false and LIVE_ENABLED=false.
