# Local phase workflow implementation plan

> Execute inline; no automatic delegation. Preserve one owner per write surface.

**Goal:** local development and authenticated local qualification through P3,
with publication and external review batched at the end of each phase.

**Architecture:** retain the existing execution and proof owners; add explicit
local issuer provenance alongside the existing GitHub lane. No alternate worker,
database, sandbox or acceptance criteria.

**Stack:** existing Python 3.11, Pydantic, PostgreSQL/Alembic, protected files,
CAS, Make/uv and existing test harness. No new production dependencies.

**Approved design:** [local phase workflow](../specs/2026-09-18-local-phase-workflow.md).

## Baseline and authority — reconciled 2026-09-18

- Accepted source parent: `977ff03dc163e2ef471c095f7e69286ba0a2050e`.
- Parent tree: `cb4fa39da4ae1e60f8208876d3c1a92bdf5d3335`.
- Working checkout: `/home/thenam176/projects/trading-agent-m4-recheck-20260918`;
  branch `codex/m4-recheck-20260918`; clean before this documentation transfer.
  This existing standalone checkout has a real `.git` directory outside the
  canonical root. Its origin is GitHub; no remote operation is needed here.
- Document donor: `/home/thenam176/projects/trading-agent-local-phase-20260918`,
  HEAD `df5f4a3733ad0bd2589e5d5891e8c0305e8164b2`, tree
  `44187ae126eaa5dd41875f5b53fa8c6002326587`.
- Transferred byte-for-byte before this amendment: `AGENTS.md`,
  `docs/development-workflow.md`, this plan and its linked design. The donor's
  four dirty documents remain intact. Donor and target tracked AGENTS/workflow
  baselines were identical; no merge conflict or overwritten target edit.
- The only committed delta from donor parent to candidate is the reviewed
  protected-range fixture pin/comment and two generated status files (three
  paths, four insertions/four deletions). Retain those candidate changes.
- Canonical checkout was observed at `8ee1c18a5b99bca78738b6d5ca572bd0afb7c0b3`,
  with pre-existing `AGENTS.md`, `docs/development-workflow.md`,
  `/home/thenam176/projects/trading-agent/docs/audits/codex-workflow-efficiency-2026-09-08.md`
  and `report.md` changes.
  All remain untouched. The earlier plan's canonical `9b625aa` claim is stale.
- Transfer/preservation evidence is outside Git at
  `/home/thenam176/projects/trading-agent-optimization-evidence/local-phase-baseline-20260918/transfer-receipt.json`;
  original donor document bytes are retained beside it.
- This request authorizes baseline/document reconciliation, requirement mapping
  and plan/acceptance additions. No production-code implementation in this turn.
  PR #69 need not be merged to work from this candidate; this plan has no merge
  dependency and does not resume the previously stopped PR workflow.
- Later implementation scope remains the approved local lane; no commit, push,
  PR mutation, merge, service installation, protected configuration write,
  running database migration, official campaign, broker or live operation is
  authorized by this documentation change.

The parent SHA/tree identifies committed source, not the dirty documentation
result. Do not label tests on this dirty checkout a clean-candidate certificate.
Freeze and rebind future evidence after the approved source changes. Prior
receipts and original CAS remain immutable; no refresh is performed here.

The [requirement and acceptance matrix](2026-09-18-local-phase-acceptance.md)
is the row-level scope: eight P1-H/LTS DAG nodes, P2-00 through P2-20, every
Pre-P3 gate and all 34 P3 V2.1 task cards. It distinguishes implementation,
test, environment and certification gaps; mapped source is not a PASS claim.

## 1. Adopt the development/publication policy

Update `AGENTS.md` and `docs/development-workflow.md` to reference the approved
phase cycle, focused feedback and final publication boundary. State explicitly
that hosted checks are not required after each edit, but current formal gates
remain enforced until the new lane is implemented. Validate documentation
links, commands and the scoped diff. Do not modify CI triggers or branch rules.

## 2. Define and authenticate issuer identity

Owners:

- `packages/alpha_lifecycle/contracts/authority.py`: run and integration receipts.
- `packages/alpha_lifecycle/authority.py`: request validation and issuer context.
- `packages/runtime_release/config.py`: reuse protected canonical-file reads.
- `packages/alpha_lifecycle/pit_suite.py`: qualification metadata/readback.
- `tests/p3/test_qualification.py` and `tests/p3/test_authority.py`: contract regressions.
- `packages/p3_provenance.py`: versioned phase receipt/status identity, coordinated
  with step 4a; preserve legacy canonical bytes and explicit issuer discrimination.

First write failing tests for local identity with no GitHub variables, rejection
of unprotected self-issued JSON, host/boot/source/policy mismatch, expiry,
revocation, duplicate attempts and issuer collision. Preserve old canonical
receipt bytes/digests and the existing GitHub negative tests.

Implement a versioned discriminated identity, authenticated using protected
host custody, with explicit LOCAL versus GITHUB provenance. Reuse existing
serialization, hashing, validity and protected-file machinery. Thread identity
through all producers/consumers; do not extend old strict schemas by silently
adding default fields that change old digests.

## 3. Qualify HWC and Pre-P3 locally

Owners:

- `scripts/record_hwc_portable_receipt.py` and `packages/hwc_status.py`.
- `scripts/qualify_p1_engine_lts.py`, `packages/nautilus_upgrade_authority/lts.py`
  and `scripts/qualify_pre_p3.py::p1_external_v1/p1_bridge_v2`: retain the full
  eight-node DAG; local mode's `external_evidence=NOT_ASSESSED` is not P1-H/LTS
  completion. Authenticate native, foundation and operator evidence per lane.
- `scripts/qualify_pre_p3.py` and `packages/pre_p3_provenance.py`.
- `packages/project_status.py` and `scripts/derive_project_status.py`; step 4a
  also owns the nested P3 phase projection.
- `tests/hwc/test_headless_portable.py`.
- `tests/governance/test_pre_p3_qualifier.py` and `tests/governance/test_pre_p3_provenance.py`.

Reuse HWC headless/recovery/boundary/contract proofs and P1 LTS/native/operator
proofs. Reuse P2 source/PIT, deterministic replay and approved disposable
PostgreSQL runtime proofs. Keep scope-bound external approvals and time limits.

Before certification, close the concrete PIT manifest coverage gaps in the
matrix: the mapped symbol-change test checks lookup-key rejection, listing/
delisting checks model parsing, corporate-action checks projection rejection,
and provider-conflict checks rank ambiguity. These nodes do not by themselves
exercise the manifest's T1/T2/T3 cutoff rule. Reuse an existing semantically
complete test if found; otherwise add the missing temporal assertion to the
owning suite before updating the manifest. Do not lower the seven-case contract
or substitute collection success for executed semantic evidence. Verification:
`uv run python scripts/dev.py test tests/data_platform/test_adversarial_suite_manifest.py tests/data_platform/test_revision_chains.py tests/security_master/test_resolver.py tests/security_master/test_models.py tests/security_master/test_p1_projection.py tests/data_platform/test_provider_quality.py tests/security_master/test_parquet_snapshot.py`.
Done when every named case demonstrates its required cutoff behavior and retained
collection/report readback identifies the actual executed test.

First demonstrate that a fully validated local candidate can be locally ready
without a promotion receipt, while published readiness remains HELD. Reject
missing evidence, wrong source, mixed issuers, mutated tracked inputs and stale
receipts. Local receipt aggregation must reread retained proof bytes, not accept
a list of PASS booleans. Do not accept a local receipt in a hosted certificate.

Refactor only shared proof validation, separating it from issuer/transport
validation. Keep hosted promotion constructors and validation intact. Store
local qualification evidence outside source to avoid self-referential source
identity and accidental publication of host data. Expose separate local and
published verdicts; do not edit generated PASS states by hand.

## 4. Carry identity through the durable P3 session

Owners:

- `services/job_worker/p3_host_profile.py`, `p3_official.py`, `p3_session.py`.
- `services/job_worker/p3_integration.py`, `p3_qualification_readback.py`.
- `services/job_worker/p3_custodian_profile.py` and `p3_holdout_release.py`.
- `services/job_store/worker_repository.py`, `p3_catalog.py`, `p3_sql.py`.
- Session SQL owned by migrations `0029_p3_session_holdout_claim.py` and
  `0030_p3_session_terminal_fences.py`; create a forward migration if required.
- `scripts/p3_authority.py` and existing worker command entry points.

Trace the SQL called by claim, heartbeat, cancellation, terminal publication
and expired-lease recovery before changing persisted identity. Do not modify
applied migration history. Regenerate catalog/contract artifacts using their
owners. Keep Job API as the only durable-job owner.

Add failing tests at request, host-profile, coordinator and SQL seams for
matching local identities, wrong issuer, source drift, revoked authority,
session restart and duplicate holdout disclosure across lanes. A second lane
must not create a second experiment or bypass the existing disclosure ledger.

Implement one authenticated local launcher adapter feeding the existing
coordinator; retain GitHub as another adapter. Keep exact-job binding, process
and boot identity, bounded deadlines, current leases, capability role checks,
protected review, real sandbox startup, six native processes, CAS readback and
terminal SQL result validation. A launcher cannot approve its own job/review.

Use a common lane-aware source readiness decision at enqueue, worker startup
and custodian admission. Every privileged consumer must reject a partial or
mixed-lane migration. Do not expose local execution until all seams agree.

Focused suites include the existing profile/session coordinator, session
holdout claim/driver, qualification readback and custodian-profile tests.
Real PostgreSQL tests require exact disposable-fixture authorization; mocked
SQL tests cannot qualify the durable migration.

## 4a. Complete phase-exit provenance and lane-aware status before any holdout

This step is required after steps 2–4 and before steps 5–6. It closes the omitted
T-P3-053/T-P3-073 seam; neither a launcher-only change nor local Pre-P3 readiness
can finish P3 while final status remains hosted-only.

Observed flow on the accepted parent:

1. `P3HoldoutSession.prepare_exit` delegates to
   `services/job_worker/p3_publication_producer.py::prepare_phase_exit`, which
   calls `packages/alpha_lifecycle/phase_exit.py::evaluate_phase_exit`.
2. The evaluator recomputes retained holdout/reference/parity evidence and 13
   checks. Publication produces QUALIFIED on PASS, REJECTED on economic FAIL;
   HELD cannot publish a registry decision. Reuse this owner unchanged.
3. `services/job_worker/command_registry.py` assigns PHASE_EXIT the validator
   `p3-publication-exit-v1`; `services/job_worker/results.py::validate_p3_result_bytes`
   returns a PublicationProposal for that route. A separately parsed
   `p3-phase-exit-v1` receipt is not the actual publication result.
4. `packages/p3_status.py::derive_p3_status` reads a tracked PhaseExitReceipt,
   requires GitHub attestation, then requires protected promotion for
   `phase_complete`. `packages/p3_provenance.py` has workflow-numeric identities
   and a PASS-only phase receipt. The workflow currently retains preflight and
   terminal JSON; it does not create/attest a phase-exit bundle. No production
   PhaseExitReceipt producer was found in scripts/packages/services/workflows.

Expected touch points and minimum behavior:

- **Receipt producer/readback:** extend the existing authority wait/readback
  seam (`scripts/p3_authority.py`, `services/job_worker/p3_qualification_readback.py`
  where reusable, actual publication result in `services/job_worker/worker.py`
  and `services/job_store/p3_publication_repository.py`). First prove the precise
  terminal-publication read API; do not reuse integration-only readback as if it
  already verifies phase exit. Build a source/policy/issuer/job/attempt-bound
  exit bundle only from committed SQL outcome, current registry head and verified
  retained CAS closure. Construct it after commit, never from a proposed event
  or child's PASS. Authenticate through protected authority; researcher JSON
  cannot issue authority. Interrupted export can resume exact-byte readback
  without another holdout or a duplicate registry transition.
- **Receipt contracts:** version `PhaseExitReceipt`/status in
  `packages/p3_provenance.py`; LOCAL and GITHUB identities cannot collide. Preserve
  v1 parsing/canonical digests and the hosted PromotionReceipt contract. Retain
  FAIL/NONE_QUALIFIED/HELD outcomes as non-success evidence; none may be coerced
  into the PASS-only certificate.
- **Status consumer:** update `packages/p3_status.py::derive_p3_status`,
  `packages/project_status.py::derive_project_status`, and their generator CLIs
  together. Derive local source qualification, pipeline completion, economic
  objective and published completion independently. Valid authenticated local
  success can satisfy local completion with published promotion HELD; it must
  not satisfy legacy published `phase_complete` or `protected_promotion`.
  For hosted status retain `_attested`, exact workflow/ref/source validation,
  nearest first-parent promotion and invalid-nearest-no-fallback behavior.
  Missing, malformed, unauthenticated, expired/revoked, stale source and mixed
  issuer evidence must have distinguishable blockers and fail closed.
- **Closure and publication:** `packages/pre_p3_provenance.py::_excluded_output`
  and `tests/governance/test_p3_source_outputs.py` must retain pure structural
  checks for exact versioned output paths. Keep local authority/evidence external;
  do not exclude a whole receipt directory or introduce recursive closure calls.
  Keep `scripts/record_p3_promotion.py` hosted-only. Review any necessary hosted
  producer/attestation YAML change as an explicit later CI change; no CI edit
  or weakening is authorized by this document transfer.
- **Consumers:** include `services/job_worker/results.py`, source readiness at
  enqueue/host-profile/custodian admission, serializers and generated project
  status. Trace all imports before editing; regenerate through owners only.

Proof first: add one focused end-to-end seam test using retained synthetic
publication evidence: committed exit → authenticated receipt → local status;
no GitHub variables and published gates stay HELD. Its negative cases must reject
an uncommitted proposal, forged receipt, wrong source/policy/host/issuer/attempt,
missing CAS dependency, changed registry head and unauthenticated mixed-lane
proof. Test restart after SQL commit but before receipt export, preserved FAIL,
NONE_QUALIFIED and HELD, and no second holdout across lanes. Extend existing
`test_phase_exit.py`, `test_publication_recovery.py`, `test_result_roundtrip.py`,
`test_p3_status.py`, `test_p3_source_outputs.py`, `test_p3_promotion_writer.py`
and `test_p3_workflow.py` at their existing seams.

Done only when the full producer-to-status round trip works before official
research, all legacy rejection cases still pass, and the real host/SQL proof
is separately qualified. No post-holdout source patch to manufacture completion.

## 5. Verify source and prepare host activation

Run affected tests via `uv run python scripts/dev.py test <paths>`, then static
and generated contract checks. Review security/correctness and separately
review unnecessary complexity. Record unavailable independent review as
NOT REVIEWED, never PASS.

At a frozen candidate, prepare the dashboard build, run `make test-all`, and
complete native, persistence, concurrency, recovery and authority checks.
Retain exact command/exit/source/input/environment evidence. Reuse earlier
checks only with an explicit unchanged-dependency argument.

Prepare a bounded host provisioning/activation change with rollback and exact
paths only after the source is reviewable. Existing approval does not authorize
writing root-owned authority, service configuration or a running database.
Do not bypass missing host authority. No qualification result may be claimed
before a real run in the approved environment.

## 6. Run local qualification and finish P3

On the approved host, execute local HWC, P1-H/LTS, P2 and Pre-P3 qualification
for the exact frozen source. Revalidate existing dataset provenance against
that source; do not download or rebuild unchanged data without cause.

Run baselines, family registration and disclosed OOS evaluations through the
existing Job API/worker. Select one primary according to the unchanged policy.
If selection succeeds, perform independently approved HOLDOUT -> PARITY ->
PHASE_EXIT in the bounded protected session. No automatic retry, replacement
primary or policy tuning from holdout results. Preserve a valid no-alpha result.

Retain local technical, qualification and economic verdicts separately. Prepare
a compact phase review package containing scope-to-evidence mapping, source
SHA/tree, commands/results, limitations, unresolved findings and P4 dependencies.
Exclude secrets, runtime state and large data from the published package.

## 7. Publish at the phase boundary

Only after separate push authorization, publish the reviewed batch and run the
existing hosted verification with real GitHub identities. Obtain external
review and resolve material findings. A necessary corrective publication is
allowed. Plan the next phase against the final reviewed baseline and actual
P3 economic outcome.

## Validation and executable acceptance contract

Commands below are for later implementation unless marked executed in the
matrix. `uv run python scripts/dev.py test` owns private temporary storage;
add `--native-custody` only for suites that require that capability. A test path
is an existing target to extend, not a claim the new tests already exist.

| Step | Proof-first / focused command | Done when / matrix rows |
| --- | --- | --- |
| 1 | `git diff --check`; resolve all document links; compare transfer hashes | Exactly four donor files transferred, original dirty bytes preserved, baseline matches; A01 |
| 2 | `uv run python scripts/dev.py test tests/p3/test_contracts.py tests/p3/test_authority.py tests/p3/test_qualification.py` | Forgery/collision/expiry/revocation and old-byte tests pass; A02 |
| 3 | `uv run python scripts/dev.py test tests/nautilus_upgrade/test_qualify_p1_engine_lts.py tests/governance/test_pre_p3_qualifier.py tests/governance/test_pre_p3_provenance.py tests/hwc/test_headless_portable.py` | Full local prerequisite DAG readback authenticates; no local receipt satisfies hosted promotion; A03–A04 |
| 4 | `uv run python scripts/dev.py test tests/p3/test_host_profile.py tests/p3/test_session_coordinator.py tests/p3/test_session_holdout_claim.py tests/p3/test_qualification_readback.py tests/p3/test_custodian_profile.py` | Admission, lease/recovery, terminal and custodian share issuer identity; real disposable SQL migration proof additionally required; A05–A06 |
| 4a | `uv run python scripts/dev.py test tests/p3/test_phase_exit.py tests/p3/test_publication_recovery.py tests/p3/test_result_roundtrip.py tests/governance/test_p3_status.py tests/governance/test_p3_source_outputs.py tests/governance/test_p3_promotion_writer.py tests/governance/test_p3_workflow.py` | Receipt producer/status round trip passes; failed alpha retained; hosted behavior unchanged; A07–A09 |
| 5 | `uv run python scripts/dev.py static`; `make check-contracts`; then `make build-dashboard` and `make test-all` | Frozen-source required gates, security/concurrency/recovery reviews complete; no hidden skip; A10 |
| 6 | Approved local adapters and exact disposable/native/host runbook after implementation; their CLI does not yet exist | Actual authenticated local HWC/P1-H/P2/Pre-P3 and P3 path evidenced; actual economic outcome retained; A11 |
| 7 | Genuine hosted `make ci-portable NONINTERACTIVE=1` after publication authorization | Exact published candidate verified, external findings resolved; A12 |

Do not invent a runnable local qualifier command until its source exists. Use
`make test-p2-runtime-postgres` only with current authorized disposable fixture
records; source mocks cannot substitute for SQL/native proofs. `make test-all`
includes audit and contracts; do not repeat leaves on unchanged inputs. Formal
qualification requires clean, frozen inputs and review; this turn's focused
checks are reconnaissance of existing code, not local-lane acceptance.

## Acceptance and stop conditions

Completion requires real local qualification without GitHub variables, old
hosted behavior preserved, cross-lane replay rejected, single holdout semantics
preserved, and no live authority. Tests with injected authority prove only the
tested rejection/acceptance logic, not installed host authority.

Stop dependent execution for missing independent review, missing protected host
authority, failed durable migration tests or source/evidence drift. Continue
independent source work where possible. Report the exact missing prerequisite;
do not replace qualification with documentation or fabricate a receipt.

## Current implementation state

- Step 1: documentation/process policy implemented in this change.
- Steps 2–4a: mapped to source owners and acceptance rows; not implemented by this change.
- Steps 5–7: not performed for the new local authority lane.
- Current source/qualification/runtime gates have not been altered or promoted.

## Handoff

Use this candidate checkout and the linked matrix for the next source task.
Verify HEAD/tree/dirty paths against the transfer receipt before implementation;
only these documentation changes are task-owned. Report every changed file,
exact command/exit, skipped prerequisite and final source identity. Retain
separate source, test, environment, certification, economic, remote and runtime
verdicts. Neither the old stopped handoff nor this amendment restarts PR #69.

READY FOR APPROVAL — NO IMPLEMENTATION PERFORMED

This refers to the new local-lane source implementation; the requested document
transfer and planning amendment are completed, not pending permission.


## Execution addendum — 2026-09-18: local qualification authentication

The operator explicitly authorized the identity/evidence authentication slice.
This addendum supersedes the earlier no-implementation status only for that
slice of step 2. The candidate remains `977ff03dc163e2ef471c095f7e69286ba0a2050e`
(tree `cb4fa39da4ae1e60f8208876d3c1a92bdf5d3335`) plus the uncommitted changes.
PR #69 was not merged. Existing documentation was preserved; this section is
appended to the prior plan without rewriting its baseline record.

Implemented source and executable acceptance:

| Requirement | Source / test evidence | Current verdict |
| --- | --- | --- |
| Explicit LOCAL/GITHUB identity; unchanged legacy bytes | `packages/alpha_lifecycle/contracts/authority.py`: new strict identities, binding, versioned receipt and approval; `tests/p3/test_local_qualification.py`: discrimination and golden legacy authorization digest | Implementation and focused tests PASS; new GITHUB model is structural, not hosted authentication |
| Bind source, policy, input, environment, issuer, run/attempt and host/boot | `packages/alpha_lifecycle/local_qualification.py`: `validate_local_qualification`; wrong-binding and actual observed-host rejection tests | Authentication primitive implemented; caller must establish expected context independently |
| Reject forgery, stale/expired/revoked authority and altered retained bytes | Same validator/test: real protected-reader rejection of self-issued files, strict digest/canonical validation, validity checks, CAS readback, second approval/host/time observation | Focused source tests PASS; positive protected custody is injected in tests, not installed authority |
| Reject replacement between integrity verification and parsing | Receipt is parsed from the exact verified byte buffer; red/green swap regression retained | PASS |

The validator reuses the existing protected-file reader, canonical serializer,
digest/source/reference validators and artifact store. The fixed approval path
is `/etc/trading-agent/local-qualification/<issuer>/<gate>.json`, using existing
root-owned file/ancestor checks. That current approval pins an immutable retained
receipt. Host identity comes from OS machine/boot files, never GitHub or host
environment variables. Receipt validity is bounded to 24 hours, receipt size to
65,536 bytes, and retained proofs to 1–64 unique references of at most 131,072
bytes each; input/environment references have the same per-reference bound.
Larger suites bind compact proof manifests. No production dependency was added.

A successful call authenticates these bindings and retained bytes; each gate
owner must still evaluate its existing semantic proof validators. Repeated
validation is read-only and is not a durable single-use lease. The primitive
must be called fresh at admission; its returned model is not runtime authority.
Source/policy/input/environment expectations must not be copied from the receipt
being authenticated.

Validation executed for this slice:

| Check | Result / evidence |
| --- | --- |
| Proof-first missing API, receipt swap and nil-run regressions | Observed failures followed by passing implementation; `red-assertion.log`, `receipt-read-race-red.log`, `nil-run-red.log` |
| `uv run python scripts/dev.py test tests/p3/test_local_qualification.py` | **53 passed**, exit 0; `local-final.log` |
| Focused existing contract/authority/qualification/protected-reader regressions with the new tests | **133 passed**, exit 0 before the final UUID4 refinement; `focused.log`; final refinement covered by local and affected regressions below |
| `uv run python scripts/dev.py test --native-custody tests/p3 tests/alpha_lifecycle tests/governance/test_pre_p3_provenance.py tests/governance/test_pre_p3_qualifier.py tests/governance/test_p3_status.py tests/governance/test_p3_source_outputs.py tests/governance/test_p3_workflow.py tests/runtime_release/test_config.py` | **1564 passed, 13 skipped**, exit 0, 1652.92 seconds; `affected-regression.log` |
| `uv run python scripts/dev.py static` | PASS, exit 0; `static-final.log` |
| `uv run python scripts/generate_contracts.py --check` | PASS, exit 0; `contracts.log`; this leaf is not the complete `make check-contracts` target |
| `make build-dashboard` | PASS, exit 0; `build-dashboard.log`; later UUID-only source change does not affect dashboard inputs |
| `make test-all` | **FAIL**, exit 2: audit's P1 lineage check requires a clean source checkout; stopped before the root suite. Canonical audit, 11 D0 tests and boundary check passed before this stop; `test-all.log` |
| Independent security review / Codex Security scan | **NOT REVIEWED**; no scan capability was available; author review is retained separately |

All named logs, source hashes, red/green evidence and preservation checks are
retained outside Git at
`/home/thenam176/projects/trading-agent-optimization-evidence/local-qualification-identity-20260918/`.
These are development-test records, not authenticated qualification receipts.
The 13 skips retain existing opt-in disposable PostgreSQL, pinned native Python
and transient sealed-view environment/selection requirements. Native custody
support in the wrapper does not satisfy all those other prerequisites. No SQL
schema changed, and no PostgreSQL fixture was enabled by this slice.

Acceptance-matrix delta: A02's identity/authentication primitive has source and
focused test evidence. Full producer/consumer propagation and installed protected
issuer readback remain incomplete, so A02 is not promoted to overall PASS. The
linked matrix remains the parent-baseline inventory; this addendum records only
this slice's changes. Missing implementation/tests for HWC/Pre-P3, request,
worker/profile, session, claim/lease/recovery, custodian, result readback,
`p3_provenance.py`, `p3_status.py` and phase-exit remain steps 3–4a. Cross-lane
single-holdout proof and any required new migration with real PostgreSQL remain
mandatory there. Missing installed protected host authority is an environment
gap; missing clean-source qualification and independent review are certification
gaps. None can be replaced by the passing source tests above.

Status: **identity/authentication slice IMPLEMENTED AND SOURCE-TESTED**;
full local qualification, integrated runtime and certification remain **HELD**.
No root/protected-host file was installed; no database, service, workflow, legacy
serialized contract, remote branch or live authority was changed. No commit,
push, PR merge, official campaign or holdout was performed. Existing dirty work
is preserved. Resume only the separately scoped next implementation step.


## Execution continuation: local adapters and system integration

The operator subsequently authorized execution of all planned source work and
repair of implementation/test blockers. Work remains in the isolated candidate
checkout, descended from `977ff03`; canonical/donor checkouts are preserved.
The earlier identity-only checkpoint above is historical, not the current scope.

Implemented owners now include `packages/local_pre_p3.py` and the `local-plan`,
`local-propose`, `local-readback` commands; explicit local operation/fixture
contracts and protected request admission; worker/profile/session/custodian and
result readback; new migration 0031 with shared single-disclosure state; and
`p3_phase_readback.py`, versioned phase receipts, local status and restartable
export. Existing HWC/P1 validators are reused. The PIT manifest is unchanged;
its mapped tests now exercise temporal symbol/listing/action/provider corrections.

The [local qualification runbook](../../operations/local-phase-qualification.md)
owns exact proposal/installation/readback order and rollback boundaries. Root
custody, distinct accounts, isolated host/database installation, independent
security review and actual scientific outcomes are separate from source tests.
No official holdout, protected approval, running database change, service
installation, hosted workflow mutation or promotion was performed by this source
implementation. Final verification and unresolved gates are recorded in the
acceptance matrix and external execution manifest.

## Host execution findings and completion criteria

The operator delegated technical review and the isolated host changes for all
seven completion steps. This removes a human-review pause; it does not create
human-review evidence, hosted identity, official economic results or live authority.
Keep every prior receipt bound to its original candidate and execution.

| Finding | Gap classification | Repair and required acceptance |
| --- | --- | --- |
| Historical Git revisions absent from a standalone candidate caused four aggregate failures | Environment | Import only verified historical objects; preserve HEAD/source. Recheck the four exact tests and run the final candidate aggregate. |
| HWC passed in the producer checkout but the sealed runtime lacked its dashboard build | Environment | Include the exact qualified build artifact, seal it, rederive runtime authority and read all ten prerequisites under service UIDs. |
| Complete stage checks after snapshot creation could exhaust the six-second window | Implementation and runtime qualification | Reuse the existing static-before-dynamic refresh primitive through the shared LOCAL P3 worker composition. Keep static/source/interpreter/UID/approval checks and final snapshot freshness. Prove a real worker run and active revocation. |
| Slow outer progress callbacks expired the synthetic publication claim before its stale-head check | Test-fixture lifecycle | Renew only its still-active fenced claim around ordinary checkpoints, with the existing30-second lease. Two16-second callbacks must pass; a callback exceeding30seconds must fail. Keep cancellation, deliberate expiry, locks and terminal replay unchanged. |
| Passing native replicas or a terminal FAILED job is not a successful integration receipt | Certification | Require all original SQL vectors, cleanup, a SUCCEEDED durable Job API result, and recomputation by `validate_integration_job_result`. |

The actual reproduction and historical execution records remain outside Git in
`trading-agent-optimization-evidence/local-host-qualification-e01c3f9-20260919`.
The source repair continues in an isolated descendant candidate. Its fresh
qualification must cover the final source identity; old host results are
diagnostic evidence rather than replacement certificates. The final seven-step
matrix must separately report source checks, protected prerequisite approval,
runtime/session/custodian behavior, current readback and official phase status.
