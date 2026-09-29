# Local qualification and phase readback

This runbook supplements [the implementation plan](../superpowers/plans/2026-09-18-local-phase-workflow.md).
LOCAL evidence has its own issuer identity; GitHub receipts and protected promotion
remain on the existing hosted path. A proposal, source test, or successful export
is not a root approval, an independent review, or permission to open holdout.

## Candidate and evidence preparation

Use the clean, reviewed candidate descended from `977ff03`. Keep source, protected
configuration, the operator CAS, the custodian CAS and disposable PostgreSQL roots
separate. Use Python 3.11 and the frozen root lockfile. Both live flags stay false.

Retain an `EnvironmentIdentity` using the existing contract: current root lock
hash, pinned P1 native closure hash, P3 sandbox policy hash, Linux x86_64 and decimal
precision 50. Retain the exact qualification input inventory. Pass their canonical
`ArtifactRefV1` JSON files and the private CAS to the proposal command:

```bash
uv run python scripts/qualify_pre_p3.py local-plan \
  --issuer <approved-issuer-token> \
  --store-root /absolute/private/qualification-cas \
  --input-ref /absolute/private/input-ref.json \
  --environment-ref /absolute/private/environment-ref.json \
  --output /absolute/private/proposed-pre-p3-profile.json
```

This creates a UUID run on the observed machine and boot, attempt 1, current
source identity, unchanged policy digest and all ten gate bindings. It neither
installs a profile nor issues an approval. A changed candidate requires a new
plan and new evidence. Do not relabel an old execution with the new source.

## Execute and authenticate prerequisites

Use `local-propose` with the same proposed profile for each gate:

```bash
uv run python scripts/qualify_pre_p3.py local-propose \
  --profile /absolute/private/proposed-pre-p3-profile.json \
  --gate HWC_PORTABLE_QUALIFIED \
  --output /absolute/private/hwc-local-proposal.json
```

Run HWC, P1_H_COMPLETE, P1_LTS_READY, P2_SOURCE_COMPLETE, P2_RUNTIME_QUALIFIED,
P2_QUALIFIED, ALPHA_REGISTRY_FOUNDATION, P3_BASELINES_FROZEN,
P3_EVALUATION_PROTOCOL_FROZEN, then PRE_P3_READY. Gates retain observed pytest
collection and execution, with exact selectors and no skipped/absent tests.
HWC uses the actual headless/boundary/contract validators and dashboard build.
P2 source adds the executed PIT manifest and three deterministic certifications.
P2 runtime requires the existing separately approved disposable PostgreSQL 16
fixture; the command cannot point at an existing database.

For both P1 gates, additionally provide `--retained /absolute/private/p1-refs.json`.
The array order is: complete native LTS receipt, external foundation receipt,
external native receipt, external operator receipt, then every leaf referenced
by those external receipts. The existing P1 validator recomputes the complete
DAG. A local-only P1 test result cannot replace those independent receipts.

The protected issuer must review each actual result and pin its CAS reference in
`LocalQualificationApproval`. Aggregates require already authenticated children;
the launcher cannot approve its own child receipts. Approvals expire within the
receipt's interval (at most 24 hours), have a revocation bit and retain all four
safe-authority flags false.

## Bounded host change for operator review

The source change does not execute this installation. Review exact generated
bytes, source SHA/tree, issuer, host/boot, UIDs, credentials and intervals first.
Do not install placeholders or copy a user CAS wholesale.

| Proposed destination | Content and custody |
| --- | --- |
| `/etc/trading-agent/local-qualification/pre-p3.json` | Root-owned canonical `LocalPreP3Profile`; select the approved identity and complete bindings. |
| `/etc/trading-agent/local-qualification/<issuer>/<gate>.json` | Root-owned current `LocalQualificationApproval` pinning one receipt; no self-issued signature or fake workflow identity. |
| `/var/lib/trading-agent-local/qualification/<run-id>/` | Shared evidence copies, each named by SHA-256. Copy only the manifest-bound dependency closure, verify every byte/ref, root-owned `0444`, trusted root-owned ancestors. Set the installed Pre-P3 profile's store to this root. Producer CAS stays private. |
| `/etc/trading-agent/local-qualification/<issuer>/operations/<nonce>.json` | Root-owned `p3-local-authority-request-v1`, exact source/input/operation/review, short validity; use the separate SQL authority credential. |
| `/run/trading-agent-p3/local/<issuer>/<run-id>-<attempt>/profile.json` | `LocalOfficialHostProfile`, exact job/payload/environment/runtime closure and LOCAL integration receipt plus terminal Job API readback. |
| Same run directory: `session.json`, `stage.json` | `LocalSessionProfile` and current `SessionStageProfile`; actual parent PID/start/boot and exact claim/lease-token hash. Issue stages only after their real claims. |
| Protected custodian profile at the separately approved absolute path | `LocalCustodianProfile`, same local authorization and derived holdout request, distinct research/custodian UIDs, actual socket peer identities, matching SQL catalog. |
| `/etc/trading-agent/local-qualification/phase-exit.json` | `LocalPhaseProfile`, exact final receipt and `P3_PHASE_EXIT` qualification binding; worker read credential directory only for committed publication readback. |

The local profiles and approval documents in this table use canonical JSON plus
one newline, root-owned `0444`. Existing independent review and operation-manifest
files consumed by `_read_authority_bytes` retain their own contract: exact canonical
artifact bytes without an added newline, root-owned `0400`/`0600`, or `0440`/`0640`
with the approved reader group. Protected CAS copies also retain exact artifact
bytes without an added newline. The shared
proof reader permits at most 128 KiB per prerequisite proof while authority JSON
retains the 64 KiB bound. Large research/native artifacts stay under their existing
bounded custody owners. Never weaken the private CAS permissions for cross-UID
access. Use distinct approved accounts; actual UIDs must be observed, not assumed.

Provision a new isolated rehearsal database through reviewed owner credentials;
apply the existing migration chain and new forward-only
`0031_p3_local_identity`, `0032_p3_enqueue_freshness`, then
`0033_p3_core_closure_repair` (projection-recovery repair and the P1 closure
rotation to `C_new`). Their parent and final catalogs are pinned in source.
Current LOCAL admission requires 0033; old 0031 and 0032 are held, while
historical hosted 0026/0030 code paths remain explicit. The API
reads only accepted authority through its scoped SQL reader, validates current
LOCAL request/prerequisites before enqueue and again before transaction commit.
A rejection rolls back jobs, events and authorization bindings. This is an API
admission checkpoint; filesystem revocation and SQL commit are not one atomic
operation. Worker/custodian fences remain required, and a SQL principal is not
granted independent local qualification authority.
Never run this against the installed historical database as part of preparation.
LOCAL and GITHUB use the same experiment/disclosure ledger. Do not create a fresh
database, family, epoch or lane to retry an already disclosed experiment.

Rollback admission by withdrawing/revoking the newly installed approvals and
run profiles, then stop only a separately approved task-owned launcher. Preserve
CAS evidence, SQL disclosure rows and terminal receipts. Migration 0031 has no
downgrade; repair durable state through a reviewed forward migration. Never
restore a pre-disclosure database snapshot to permit another opening. Service,
account or credential provisioning requires its own exact reviewed changes.

After installation, read back with the real owner and custodian UIDs:

```bash
uv run python scripts/qualify_pre_p3.py local-readback \
  --output /absolute/private/local-pre-p3-readback.json
```

This reports local source readiness; it does not enqueue research or grant
published completion. Run the LOCAL integration fixture with
`p3-local-integration-fixture-plan-v1` and protected local fixture authorization.
Its parent must execute three real pinned native runs and disposable PostgreSQL,
retain streams, prove cleanup, and validate the canonical terminal Job API result.
The LOCAL SQL proof includes native process fencing, session terminal expiry,
local issuer rejection, cross-lane single disclosure and phase-exit readback.


## Complete LOCAL runtime composition

The paper projection is not a full P3 runtime. Prepare a **standalone Git clone
at `stage/application`** with frozen root dependencies and the pinned CPython
archive. Seal the Git objects, tracked source and runtime together; no separate
mutable source import path is permitted. This preparation produces no approval,
activation, credential, service or job. Run under the proposed service UID only
after the host package is approved; the private root must be a direct0700 `/tmp`
child owned by that UID. Do not change permissions on the operator's home.

```bash
uv run python scripts/prepare_p3_local_runtime.py \
  --source /absolute/clean/candidate \
  --stage /tmp/approved-private-slot/stage \
  --python-runtime /absolute/pinned-cpython-archive.tar.gz \
  --uv /absolute/pinned/uv \
  --dashboard-build /tmp/approved-private-slot/dashboard-build
```

`--dashboard-build` is the exact `apps/dashboard/.next` that the
`HWC_PORTABLE_QUALIFIED` producer built. The LOCAL HWC proof binds its tree hash and
every consumer re-validates it against the stage it reads, but a Git clone never
contains that ignored directory, so a stage without it cannot pass
`read_local_pre_p3`. Copy it from the producer checkout into the private slot as the
service UID can read it (the builder runs as that UID and must not be given the
operator's home), then pass that copy. The builder copies it verbatim before sealing,
refuses links through the seal, and reports `dashboard_build_sha256`; confirm it equals
`evidence.dashboard_build_sha256` in the HWC receipt before building authority bytes.

`--python-runtime` is the archive pinned by `PAPER_PYTHON_RUNTIME_PROVENANCE`,
not an arbitrary interpreter having the same version. The builder verifies it,
uses pinned uv and the frozen root lock, includes development dependencies needed
by the integration fixture, and requires an already populated offline uv cache.
It copies dependency files instead of linking them. Only installer entrypoints
and greenlet's development-only C header are omitted to preserve the existing
Python core contract. Missing cache material is a preparation failure, never a
network download. Outputs from failure remain task-owned diagnostic material.

Use existing `build_staging_release_authority_v2` and
`build_staging_activation_v2` to prepare and review the exact authority bytes
for this stage, with actual prior-release/Package6 approval digests, runtime
paths and validity. The operator must review the stage inventory; synthetic
staging documents from tests are not installable authority. Scope stays
`PACKAGE6_STAGING_ONLY`. The30-minute authority window and6-second safety
snapshot are unchanged. A bounded, approved owner must refresh actual safety
observations and activation during a longer run; do not reuse the P1-only
refresh capability or extend TTL. The session deadline must fit the approved
runtime interval, even though its general upper bound is37minutes.

For a host composition that owns the approved safety producer, construct
`LocalRuntimeSafetyPreflight(authority, identity, source, deadline,
refresh_dynamic_evidence=producer)` and pass it as `p3_safety_preflight` to
`build_worker` or `build_p3_fixture_worker`. Use the admitted operation deadline,
or the admitted session deadline for session work. Do not also pass a separate
`p3_runtime_guard`: the preflight pins and rechecks that approval itself. The
producer must export actual observations and the matching activation; it grants
no authority. The shared worker accepts this composition only for LOCAL P3 and
the exact initial runtime authority.

This reuses `refresh_staging_worker_runtime_authority`: full immutable stage
attestation completes before the producer creates the six-second snapshot.
The existing `AuthorityBoundSafetyPreflight` then checks both protected dynamic
reads, followed by current LOCAL approval and final snapshot freshness checks.
Expired, replaced or revoked authority still blocks. Repeated full-stage hashing
after snapshot creation can consume its validity window on a large runtime.
P3 staging approval stays in the attester's environment and is not forwarded as
half of a legacy research-fixture configuration.

HWC also validates the exact dashboard build tree. Copy its already-qualified
artifact into the runtime, check its tree hash, seal it with the rest of the
stage, and derive a new static authority for that inventory. A tracked-source
clone alone does not reproduce HWC's build evidence.

In addition to current request and Pre-P3 approvals, install one independent
root-owned0444 canonical `LocalRuntimeProfile` (JSON plus newline) at
`/etc/trading-agent/local-qualification/<issuer>/runtime.json`. Its schema is
`p3-local-runtime-profile-v1`, with exact `identity`, `source`,
`application_root`, `application_python`, `uid`, `static_authority_sha256`,
`package6_approval_sha256`, `issued_at`, `expires_at` and `revoked=false`.
These are observed values from the actual stage and approved user; do not install
placeholders. Self-written staging JSON alone cannot satisfy this root custody.

Launch with the actual sealed interpreter, for example
`<stage>/application/.venv/bin/python3.11 -I -B -m services.job_worker.main`,
using the four existing `TRADING_PACKAGE6_*` staging bindings and the exact LOCAL
fixture/official environment from the reviewed host packet. Python's installed
source path is inside the sealed application. Both fixture and official startup
now enforce source/root/interpreter/UID/host/boot/static authority/approval/expiry
before reading worker credentials. Session entry also checks its full deadline.
The unchanged attester verifies sealed bytes and dynamic evidence. Switching
issuer lane does not change experiment identity or the disclosure ledger.

The executable source-composition test is
`uv run python scripts/dev.py test tests/p3/test_local_runtime.py`. It runs the
actual staged interpreter, loader and attester without GitHub variables, with
explicit synthetic staging material and no installed approval or job. Set
`P3_TEST_PYTHON_ARCHIVE` when the pinned archive is outside the documented local
cache. No archive means the heavy test is skipped and composition is unverified
on that host. Cross-UID protected installation and live qualification remain
separate approval/evidence steps.

## Official research and terminal readback

The existing bounded launcher accepts `P3_ISSUER_KIND=LOCAL`. Its routing value
is not authority: protected requests/profiles, source prerequisites, review,
sandbox, credentials and SQL catalog still have to validate. Run baselines,
registration and four disclosed OOS jobs, then select the primary. Only a selected
primary plus independent approvals may start HOLDOUT → PARITY → PHASE_EXIT in
one process-owned session. Never rerun holdout to obtain a PASS.

Export an existing committed exit after the worker's atomic publication:

```bash
uv run python scripts/export_p3_phase_exit.py \
  --job-id <committed-phase-exit-job> \
  --store-root /absolute/private/research-cas \
  --credentials-directory /absolute/worker-read-credentials \
  --output /absolute/private/phase-export.json
```

The reader checks committed SQL custody, the exact attempt/output inventory,
current registry head, retained exit result and historical independent review.
It rereads SQL before returning. Restarting the exporter reproduces the same CAS
receipt and cannot calculate, claim, publish or disclose again. A changed head,
missing dependency or uncommitted proposal rejects. Economic FAIL remains FAIL.
For a blocked/failed/cancelled job or a completed NONE_QUALIFIED selection, use
`--terminal-detail /absolute/private/job-result.json` instead of `--job-id` and
credentials. This creates non-publication evidence; it cannot invent a registry
transition. The protected issuer must authenticate the observation before status
uses it. Existing `p3_authority.py wait` retains that terminal Job API document.

Prepare `LocalPhaseProfile` externally from the exported receipt and exact
source/policy/input/environment, then verify its readback and propose the wrapper:

```bash
uv run python scripts/qualify_pre_p3.py local-propose-phase \
  --profile /absolute/private/proposed-phase-profile.json \
  --output /absolute/private/phase-qualification-proposal.json
```

The wrapper's PASS means the retained readback passed integrity checks; the
referenced economic outcome remains PASS, FAIL, NONE_QUALIFIED or HELD. The
protected issuer must approve this wrapper at `P3_PHASE_EXIT.json` and install
the exact phase profile. After that, derive external local status:

```bash
uv run python scripts/derive_p3_status.py --local
uv run python scripts/derive_project_status.py --local \
  --write /absolute/private/local-project-status.json
```

Local source qualification, pipeline completion, economic outcome and local
completion are separate. Protected promotion and published completion remain
HELD. Default tracked v1/v2 status and GitHub promotion retain their original
attestation requirements; no CI change or protected-main publication is implied.

The LOCAL runtime approval digest is pinned at startup and reread before/after
each shared worker safety preflight, including recovery, claim, pre-spawn, active
execution and session waits/fences. Revocation, replacement, missing custody or
expiry follows the existing `SAFETY_AUTHORITY_CHANGED` blocked-worker path. A
holdout session retains that original digest through all stages and binds runtime
validity to its `workflow_deadline`; current stage approvals retain their own
independent expiry checks. A new still-valid runtime profile cannot silently
rebind a running session.
