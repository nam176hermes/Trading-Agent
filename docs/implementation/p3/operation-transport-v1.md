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

Migration 0021 preserves migration 0020 and accepted history. Its separately
provisioned `trading_p3_authority` login accepts canonical reviewed intents;
Job API and worker roles cannot accept authority or write the acceptance tables.
Both digest meanings and exact workflow/alpha scope are retained in canonical
texts. One nonce binds one job. Enqueue rechecks current authority after unique
key and job binding waits; start/finalize/publication recheck after locks.

Publication retains the existing event/outbox/head/job-result transaction and
adds complete request custody to the same append-only commit row. Idempotent
readback compares the entire request, including predecessor heads. The worker
reader requires one commit bound to the succeeded job and its stored result.
Receipt recovery validates canonical request/result bytes and the database UTC
timestamp before CAS writes. Legacy rows without full request custody remain
HELD. The timestamp is the database-recorded publication time inside the
transaction, not a claim about the later physical COMMIT instant.

The worker recovers its receipt immediately after publication. Retention failure
propagates without a second finalization or research rerun. Recovery after a
process crash still needs the official reconciliation dispatcher. The fixture/database profiles now require 0021. The worker-owned fixture first
exercises the preserved 0020 vectors, then upgrades and exercises 0021. Its
parent requires the actual final SQL revision, exact source, both required
check sets and verified server/root cleanup before publishing IntegrationReceipt.
The synthetic vectors live in `services/job_worker/p3_operation_fixture.py` and
do not import the test tree. This source change does not provision or activate a
host profile.

Source verification: focused worker/publication tests: 61 passed; adjacent P3
and alpha tests: 258 passed, 1 explicit host test skipped. The selected real
PostgreSQL 16 test (`P3_OPERATION_SQL_SOURCE_TEST=1 uv run --frozen python
scripts/dev.py test tests/p3/test_operation_sql.py`) passed, including two
connections, expiry after lock waits, atomic rollback, exact idempotency,
receipt recovery after authorization expiry, holdout start denial, and real
cleanup. These synthetic disposable-cluster identities are not official review
or runtime qualification. Independent gpt-5.6-sol/high review passed the bounded
custody and immediate-worker packet. Static retains 130 existing diagnostics
versus 134 before this packet; there are no new diagnostics.

Holdout consumption must be durably fenced before any plaintext access, unique
by holdout commitment, and reject another request/job after consumption. The
meaning of `holdout_input_set_ref`, the protected plaintext delivery protocol,
and native request/runtime closure still need executable contracts and tests.
`native_request_ref` is a required root, not a claim that its producer exists.
Postcommit registration/closure and startup receipt recovery are also required. No official
worker profile is enabled by this correction.

A failed primary retains its result and keeps phase exit HELD; there is no
fallback. LIVE_ELIGIBLE=false and LIVE_ENABLED=false.

The fixture upgrade packet passed 25 focused revision/receipt tests and 263
adjacent P3/alpha tests (one explicitly selected host test skipped). The routed
PostgreSQL test passed with the 0021 profile, both real publication connections,
and receipt recovery after actual authorization expiry. Its synthetic 30-second
authorization window keeps a positive concurrent-write test separate from the
expiry assertions; heartbeat ticks continue while that window expires. No
production policy value changed. Static now retains 124 existing diagnostics,
with none in the new operation fixture module. Protected-main Foundation and
T-P3-060 must be rerun for the merged source; these checks are not that proof.
