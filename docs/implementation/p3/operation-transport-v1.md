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

Parent OOS preflight now reconstructs the retained registration receipt and
complete eight-event family closure before writing the manifest reference or
preparing a child process. It binds the exact ordered candidate slot,
sequence 2, CANDIDATE state, version and frozen parameter digest. The existing
replica readback helper is shared with baseline and registration validation:
recomputation requires already retained bytes and cannot create missing proof.
Eight negative tests failed before correction; the adjacent registration,
baseline and replica set passed 64 tests in 90.18 seconds. Independent
gpt-5.6-sol/high review passed this bounded correction. Lint passed; the same
118 pre-existing type diagnostics remain. This establishes historical CAS
consistency only, not current SQL custody, worker fencing or host authority.

The operation CLI applies its existing retained authorization/review check to
BASELINES as well as REGISTER_FAMILY before executor construction. A missing
authorization regression previously ran all three synthetic replicas; it now
fails before any child. Baseline/registration tests passed 28 cases in 58.98s,
with 31 adjacent operation-input/authority/dispatch tests passing in 0.95s and
lint passing. Independent gpt-5.6-sol/high review passed this bounded change.
The CLI check does not establish accepted SQL authority or bind caller-supplied
job attribution to a committed database row; the official worker still owns
that remaining check.

PIT execution now has a typed supporting receipt behind `PITProof.no_future_suite_ref`:
`p3-pit-adversarial-suite-receipt-v1`. This extends the previously opaque
referenced evidence, without changing the accepted trading policy or C01–C16.
It binds source, tracked selector manifest, collection and execution report
refs, exact expanded node inventory, qualification metadata and false live
authority. The seven logical selectors currently expand to fourteen pytest
nodes; fields distinguish `logical_selector_count=7`,
`collected_node_count=14` and `passed_node_count=14`. A change to that source
inventory requires review and fresh qualification.

The Pre-P3 producer executes the exact selectors through the existing governance
plugin, with plugin autoload disabled and a minimal environment. It checks clean
source before, between and after collection/execution, rejects any non-pass or
inventory mismatch, retains both raw reports and records actual completion
time. Its private test root is cleaned. P2 source receipts bind the suite
receipt digest as DERIVED_RECEIPT; the suite never references that future P2
receipt. The host workflow uploads the precise PIT CAS directory as well as
top-level receipt JSON, so report references remain recoverable.

The consumer repeats all content, source and inventory checks. These bytes and
run IDs cannot authenticate execution by themselves: protected producer custody
and accepted independent review remain required. Actual-data revision/acquisition
closure is still a separate missing leg; this packet alone cannot set C01 PASS.
The current qualification adapter and official OOS dispatcher still require
that correction before any official results.

Source evidence: 22 focused tests passed in 6.28s, including three subprocess
collection/execution scenarios with all fourteen real portable PIT calls,
source-drift refusal, real private-root cleanup and retained P2 receipt linkage.
The broader Pre-P3/PIT and workflow/closure checks passed 77 and 179 tests
respectively before the final focused additions. These test fixtures use
synthetic authority metadata and issue no official protected qualification.

Reviewer reproduced a media-role and pre-read size defect. Sixteen negative
cases failed before correction. The common reference guard now requires JSON,
positive bounded sizes and digest-bound locators before any CAS read: 64 KiB
for the outer receipt, exact tracked size for the selector manifest and 128 KiB
for each observation report. Consumer tests also reject invalid nested refs
without reading their blobs. All 38 focused tests passed in 6.53s after the
correction. The two GitHub uploads are separate transport steps; retained
objects still need authenticated import and complete consumer readback.

## Explicit claim and recovery lanes (T-P3-030/031/040)

Forward migration 0022 requires the reviewed 0021 parent and exact function
bodies, ownership and grants. A shared SQL predicate enumerates operation/workflow
pairs. Claims require an explicit fixture selector and recheck accepted operation
authority after locking; PRE_SPAWN rechecks authority after both job and attempt
locks. Recovery takes an explicit lane and rechecks it after locking without
requiring an unexpired authorization, so expired custody can still be closed.
The old recovery signature is removed. Both lanes still use trading_job_worker;
this is routing isolation, not separate credentials or official runtime authority.

The migration locks jobs while checking for active holdout custody and refuses
to strand an existing active holdout. Queued holdout remains excluded. The fixture
separately constructs a prior-source CLAIMED holdout with a matching attempt and
lease to exercise the retained start denial independently of claim routing.
Production and P1 database profiles remain unchanged; the P3 disposable profile
now requires 0022. Historical 0020 recovery vectors call their original SQL
signature before the forward upgrade; current repository recovery is exercised
after it. Event/outbox/head/job-result publication semantics are unchanged.

A real disposable PostgreSQL regression first claimed an unknown high-priority
workflow (71.45s failing run). A separate expiry regression observed PRE_SPAWN
CONTINUE after authorization expiry (74.31s failing run). The corrected source
at 1d59b764 passed the disposable SQL fixture in 74.00s, including accepted
positive vectors, exact pair combinations, wrong-lane refusal, a two-connection
post-lock payload-change race and real cluster cleanup. Final-source validation
is recorded separately; these source tests do not issue T-P3-060 or an InputSet.

Review also exercised inherited default ACLs, strictness and an alternate trusted
procedural language with the same function body. The migration now pins language,
null-input/leakproof behavior, full argument/output metadata and exact ACLs both
before and after replacement. The trusted-language regression failed in 30.64s;
the correction at 7e9aacc8 passed the real disposable fixture in 69.56s with
cleanup. Independent gpt-5.6-sol/high review passed this bounded packet. The
separate root static check retained 118 pre-existing errors; it was not PASS.

## Driver output custody (T-P3-030/031/040/043/052 correction)

The source now provides an explicit P3 spawn capability for BASELINES and
REGISTER_FAMILY. The parent verifies a complete reviewed source/runtime closure,
seals its executable bytes, mounts inputs read-only and gives each attempt a
private output directory. The existing ProcessRunner owns process creation,
lease checks, termination and reaping. This does not install an official host
attestor or enable the remaining operation executors.

The parent retains and reconstructs each successful attempt's result before
publication. Migration 0023 stores its canonical inventory reference and attempt
ID in the existing append-only publication commit row, in the same transaction
as canonical events, outbox, registry heads and job result. Idempotent retries
must match the request and both custody fields. Recovery rereads every retained
inventory artifact before issuing the derived receipt. Private outputs are
removed only after durable job finalization/publication and receipt retention;
failed finalization, lost output bytes or unproved cleanup preserve evidence.
Neither receipt timestamps nor future receipt/report hashes enter upstream
publication identity. Existing result, metrics and registry identities remain
unchanged.

The forward migration checks the reviewed function bodies/catalog, table/column
privileges, both directions of worker membership, direct/transitive P3 owner
membership, append-only trigger, exact constraints/indexes and final temporary
privilege revocation. An additional NOT VALID non-null constraint preserves
legacy rows while refusing every new custody-less INSERT, including an old
function body admitted around migration. Legacy rows are never backfilled into
current qualification evidence. The P3 disposable database profile requires
0023; production and P1 database profiles are unchanged.

Source checks: 591 P3/ProcessRunner/worker tests passed with two explicit host
checks skipped. The selected real PostgreSQL 16 fixture subsequently passed in
79.77s at 387ec1a5, exercising catalog drift, atomic rollback, two competing
connections, loss of the COMMIT response, exact custody readback and actual
cluster cleanup. The real local bubblewrap transport probe passed in 0.38s.
Contracts generation check and lint passed. Root static retains 117 existing
errors; it is not PASS. None of these checks is T-P3-060 or pinned native parity.

Official composition, actual-data revision/acquisition closure for C01, OOS,
primary selection, holdout, native parity and phase-exit execution remain HELD.
Any prior host/source receipts must be refreshed against the final merged
source before official InputSet issuance. LIVE_ELIGIBLE=false;
LIVE_ENABLED=false. No scheduler, retained research store, dataset, production
service or broker was activated by this source packet.

Foundation run 34581120051 at 5bbe0000 passed 9,895 root tests but rejected one
unclassified skip: the real P3 Bubblewrap transport probe had incorrectly been
left in the portable remainder. The correction adds that exact node to the
existing NATIVE-BWRAP-OS-SANDBOX lane and removes its environment opt-in skip.
The existing native preflight now determines execution versus unavailable
capability; portable PASS cannot stand in for this native proof. All 317 prior
inventory rows and the 49 portable-closure rows are preserved. The current
inventory has 318 active nodes, 19 in the Bubblewrap lane, and 367 governed nodes;
both exact inventory and governed-set hashes are updated.

The missing-routing regression failed before correction. Afterwards 34 focused
tests passed, including the real local transport probe and inventory/closure
checks. The wider topology and failure-diagnostic suite passed 290 tests and
exposed one stale count assertion; its corrected exact 19+8 count test passed
separately. Static remains at 117 pre-existing diagnostics. No skip allowance,
runtime receipt, host capability or economic policy was fabricated by this fix.

Foundation run 34584920196 at a99807de then found stale count consumers in the
P0 closure checker and artifact-firewall fixture: 82 failures, 9,814 passes.
The source checker now requires the accepted 318 active/59 native/259 external
counts, and the firewall fixture binds 367 governed nodes. P0's historical table
is retained with the current delta stated separately. Two focused failures were
reproduced locally before correction; both then passed, and all three affected
suites passed 265 tests in 79.83 seconds. Independent review passed this count
repair without changing any gate, classification, closure row or status rule.

Foundation run 34588600068 at 1aa08866 completed 9,896 root tests and 10,653
aggregate passing observations, then failed the separate critical-coverage rerun:
the latter attempted the governed native bwrap transport test on a host without
/usr/bin/bwrap. Coverage now derives exact native deselections from the existing
hash-locked capability inventory and rejects overlap with any sealed required
critical case. Native qualification, inventory/classifications, coverage sources,
paths, floors and required cases remain unchanged. The genuine routing test
failed before correction; 85 focused governance/native tests then passed in
37.35 seconds, including the real local bwrap probe. Independent review passed.

The first local full coverage attempt exposed missing dashboard generator tools
while their frozen install was still completing; it was not a source failure.
After npm ci completed, the full gate was rerun successfully. Transition
repositories measured 96.9136% line and 92.3611% branch coverage. No threshold
was lowered and no native capability receipt was inferred from portable coverage.
The bounded primary-selection repair rejects duplicate report/trial references
before storage and requires the ordered frozen A0–A3/version1.0.0 family before
publication. Ranking and aggregate excess subtraction use Decimal50/HALF_EVEN
without changing the caller context. Focused tests reproduced duplicate-family
acceptance and precision-dependent wrong selection before correction. Nine tests
then passed in 27.38 seconds, including explicitly stubbed SELECTED/NONE_QUALIFIED,
permutation/version failures, and an actual synthetic A0 qualification rejection
when four distinct reports represent that same candidate. Independent review
passed this bounded source scope; static retains 117 existing diagnostics.
Complete qualification/closure/head validation, trial disclosure, current review,
SQL admission and select-primary dispatch remain HELD.

The downstream candidate closure consumer now reconstructs the frozen OOS
qualification, retained prepublication evidence, expected predecessors, registry
transitions, semantic request, idempotency key and ordered deterministic ledger
UUIDs. It reads both actual registry blobs before returning a terminal head.
The producer reuses the unchanged OOS evidence and UUID formulas. Recalculation
uses readback-only storage under a cumulative 1 GiB read budget, with direct JSON
reference bounds. Publication and evaluation reuse the common canonical JSON
reader, which rejects non-JSON media before I/O throughout typed reads.

Genuine regressions covered a completely rehashed false PASS, bypassed closure
validation, arbitrary event IDs, missing event blobs, and direct/nested JSON
media. The initial 28-test suite passed, independent review found three further
holes, and the corrected 42-test suite passed in 140.90 seconds. A later
shared-reader regression reproduced two alternate reader bypasses (2 FAIL,
1 PASS); after removing duplicate readers, all 20 JSON/publication/registration
checks passed in 9.48 seconds. Synthetic committed identities in these tests do
not authenticate SQL custody. Current SQL heads, complete trial history,
protected independent review and select-primary dispatch still remain HELD.

The final adjacent closure/baseline/replica suite passed 81 tests in 221.24
seconds. Independent review passed the bounded correction after the shared
reader fix; static remains at 117 pre-existing diagnostics and canonical
contracts checked successfully. No SQL schema or transactional write path was
changed by this packet.

The unprivileged operation CLI now handles fixed candidate OOS intents. It
checks complete retained registration and the exact allowed candidate before
constructing its executor, runs all three replicas, and retains the existing
OOS publication proposal in private CAS. No SQL transaction or publication
identity changed. The missing-branch regression failed with HELD E_OPERATION
before implementation. The real portable three-child test then passed in
86.06 seconds, preserved shared input CAS, and retained ALPHA_FAIL. Six adjacent
baseline/environment tests passed in 68.40 seconds. Independent review passed
this bounded source path; static retains 117 existing diagnostics. Worker
provider dispatch and parent OOS output validation remain HELD until their
source and admission checks are implemented and qualified.

Parent output validation now admits candidate OOS proposals through the current
attempt's retained output whitelist, reconstructs qualification and publication,
and binds all three physical replica results, manifests and artifact inventories.
The existing baseline replica check is reused. SQL publication remains the same
single transaction. The initial CLI-to-custody regression reached the missing
operation branch and failed; its test cleanup was corrected to use abandon().
The real three-child case then passed in 101.90 seconds, and the final OOS/output
adjacent suite passed 14 tests in 252.49 seconds, including altered physical
result, manifest, inventory and extra-file rejection. Independent review passed;
static retains 117 existing diagnostics. Provider admission, protected profile,
source qualification and official execution remain HELD.

Primary selection now verifies the complete frozen 28-trial disclosure and
retained historical receipt/results, then reuses the existing deterministic
ranking. Family approval binds the disclosure projection without a hash cycle;
staging additionally requires its actual validity interval to cover the issued
operation authorization. The unprivileged CLI and parent validator both use
this selection owner. Parent validation admits no replica paths and recomputes
through readback-only retained custody. The bounded closure walker verifies
artifact bytes before parsing and derives the six existing embedded scenario
references from typed EvaluationResult, preserving every published identity.

Genuine red evidence includes missing disclosure (1 FAIL), expired inner review
(1 FAIL), six historical receipt gaps (6 FAIL), missing CLI operation (1 FAIL),
missing isolated driver projection (1 FAIL), missing parent branch (1 FAIL), and
missing derived scenario closure (1 FAIL). The reviewed disclosure/CLI/authority
suite passed 79 tests in 448.82 seconds. Final real portable CLI-to-custody,
forged-selection rejection and derived-closure checks passed 3 tests in 317.52
seconds; adjacent OOS/baseline/PIT checks passed 15 tests in 243.45 seconds.
Canonical contracts passed; static retains 117 pre-existing diagnostics.
Independent source review passed this bounded packet. Driver inventory adds
only the five existing selection/qualification/custody/PIT owners. SQL atomic
publication and fixed policy values are unchanged. Synthetic SQL identities
and review fixtures are not protected authority. Current canonical attempt
completeness, SQL heads, protected reviewer admission, worker profile, host
qualification and all official research execution remain HELD.

The broad P3 suite exposed one stale negative-test date after the synthetic
research fixture expanded to the frozen 2018 start. Its hardcoded 2019 date
formed an inverted training range and failed at schema parsing instead of the
intended dataset-boundary guard. The test now derives one day before the actual
dataset start. The original case reproduced 1 FAIL in 12.94 seconds; both
training-boundary cases passed in 26.23 seconds after the test-only correction.
The guard, threshold arithmetic and production source are unchanged.
