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

Publication now binds each event's evidence to the full canonical artifact hash
and each ordered registry reference to its entry's hash, UTF-8 byte size and JSON
media type. The worker transport rejects mismatches early; SQL independently
rejects direct malformed calls before the same atomic transaction can publish.
Five focused negative tests first failed, and a direct SQL permutation was
accepted before correction. Afterwards 59 focused and 285 adjacent tests passed
(one host test skipped); the selected PostgreSQL 16 test passed in 68.66 seconds,
including unchanged event/outbox/head/job state after rejection and real cleanup.

Official workflow dispatch now accepts the exact staged authorization, intent
and review through the protected `trading_p3_authority` SQL role before enqueue.
Its credential directory is withheld from fixture invocations and remains
outside Job API/worker role settings. Both enqueue and polling responses must
match the authorized payload, fingerprint, type, priority and operator; polling
also binds the assigned job ID. Genuine missing-acceptance and wrong-response
tests failed before correction; 31 focused tests and independent review passed.
Credential provisioning and real protected-host acceptance have not been run.

Baseline execution now returns the replay-proven BaselineSelection. Parent,
calculation child and replica readback share canonical research-input closure
checks, including dataset/fold/policy/PIT references and the training interval.
The publication producer retains deterministic proposals without a worker
fence. SQL independently requires the complete stage event inventory and binds
lifecycle status to qualification: registration remains unevaluated, OOS_PASS
and QUALIFIED require PASS metric/robustness identities, and REJECTED requires
FAIL. Existing registry identities and C01-C16 are unchanged.

Genuine negative tests demonstrated missing input closure, an incomplete
RESEARCHED-only batch and contradictory OOS_PASS decisions before correction.
The disposable PostgreSQL 16 regression passed in 70.99 seconds, with both
complete PASS/FAIL batches, rejection without partial publication, and actual
cleanup. This is synthetic source evidence, not T-P3-060 or official research.
Focused/adjacent source checks passed 319 tests with the explicitly selected SQL
host test skipped in that portable run; the SQL result above is its separate
execution. Canonical contract generation passed. A broader unrelated governance
run was interrupted for the review correction after 375 passes and one skip;
it is not a completed gate.
The baseline/registration executor packet repairs source seams in T-P3-021,
T-P3-031 and T-P3-042–044. The campaign entrypoint reads the exact private
P3OperationInput. Baseline dispatch binds InputSet/source/environment before
constructing the replica executor. Registration dispatch reuses current reviewed
operation staging, validates the retained baseline replay and all four frozen
candidate records/specifications, then emits exactly eight IDEA/CANDIDATE events
as an unprivileged PublicationProposal. It uses the retained authorization's
informational interval, not a later receipt or completion timestamp. SQL alone
can publish that proposal through the existing atomic capability.

The postcommit RegistrationProof producer reconstructs the exact eight-event
receipt/commit closure and historical candidate heads. It rejects incomplete,
reordered, unretained or mismatched source/dataset/cost/baseline/epoch records.
Retained baseline validation checks R1–R3, source/environment/policy, full result
references, manifest, output inventory and the unchanged maximum-return/tie
selection. These producers do not prove that arbitrary supplied CAS bytes were
issued by SQL; official callers still require the SQL custody reader.

The shared Bubblewrap executor rejects invalid source/environment bindings
before argv construction, keeps nested replicas in the driver's existing
session, discards unused stdout/stderr and reads result bytes through a
no-follow descriptor. File type/link count/size are checked before a bounded
read, with size/mtime/ctime stability checked afterwards. The P3 result parser
accepts canonical JSON with at most the producer's single LF terminator.

Fresh source verification for this packet: 83 focused tests passed before the
I/O changes; four custody/I/O tests then failed before correction and the full
replica/sandbox set passed 29 tests. Three noncanonical-framing tests failed
before correction; 42 worker/result/publication tests passed afterwards. The
adjacent P3/alpha/governance set passed 361 tests with one explicitly skipped
SQL host test after the CLI registration change. Registration CLI tests failed before its implementation and the
baseline/registration set then passed 27 tests. Canonical contract generation
and check passed. Static lint passed; type checking retains 118 pre-existing
diagnostics versus 124 before this packet, with no new owned diagnostics at
that check. These checks are source evidence, not official OOS or T-P3-060.

A local synthetic Bubblewrap namespace check observed four real host PIDs in
the same driver session. After terminating only that task-owned session, all
four procfs entries were absent. This is narrower than the required protected
host ProcessRunner cancellation/timeout/cleanup qualification.

Still required before official activation: the opaque official spawn provider,
separate claim/recovery scope, full OOS/primary validators, authoritative PIT
inventory and executed-suite evidence, durable one-time holdout consumption,
native runtime/request closure, and postcommit/startup report discovery.
Missing evidence remains HELD; current source tests do not qualify these seams.

Independent gpt-5.6-sol/high review passed this bounded source packet. It does
not approve the missing official execution, PIT/OOS/selection, or host gates.

Foundation run 34482129004 rejected the newly introduced SQL test because its
portable collection exclusion lacked a managed record. The test is now marked
`runtime_postgres` and has an exact security-critical managed deselection,
owned by job-plane and reviewed through 2026-10-31. A subprocess regression
observes real collection, verifies no SQL test executes in the portable lane,
and checks the resulting record against canonical test governance. Its prior
host marker and missing managed record failed this test before correction.

This changes no historical T-G03 inventory or receipt and does not grant its
older PostgreSQL GREEN authority to P3. Explicit P3 source-test opt-in remains
required. Protected T-P3-060 still requires its separate reviewed fixture,
workflow/Job API/worker path, real SQL/native runs and cleanup evidence.
