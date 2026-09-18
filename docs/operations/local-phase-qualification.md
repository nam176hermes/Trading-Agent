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
`0031_p3_local_identity`. Its parent and final catalogs are pinned in source.
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
