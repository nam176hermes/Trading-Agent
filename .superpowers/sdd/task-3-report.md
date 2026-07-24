# Task 3 Report: Safety-state exporter and worker client

## Outcome

Implemented the Phase 4B safety evidence boundary without starting services,
changing canonical safety state, reading credentials, or touching linked
repositories. The exporter reads only the exact canonical `.mode` and
`.kill_switch` names plus filtered systemd gate inputs. The worker no longer
opens the legacy safety root and accepts only a strict, owner-controlled,
short-lived snapshot.

## Delivered boundary

- Added a safety exporter with a fixed production source and output path,
  explicit file-name allowlist, `O_NOFOLLOW`/descriptor-anchored reads, no
  directory enumeration, and no environment-file access.
- Filtered exporter composition so only `LIVE_EXECUTION_ENABLED` and
  `LIVE_TRADING_APPROVED` cross from its environment mapping; credential and
  database values are discarded.
- Preserved canonical sentinel semantics: absent is `INACTIVE`, a private
  timestamp/reason line is `ACTIVE`, and malformed/unsafe evidence is
  `UNKNOWN`.
- Added deterministic source identity over the two exact canonical paths and
  two exact gate names.
- Added atomic same-directory replacement through a retained directory fd,
  exact `0600` output, one clock sample, 2-second refresh interval, and exact
  6-second validity window.
- Added a strict snapshot client that rejects missing, symlinked, non-regular,
  wrong-owner, non-`0600`, oversized, duplicate-key, extra/missing-field,
  malformed, stale, future, wrong-window, wrong-commit, wrong-source, and
  non-paper/non-false/non-inactive evidence.
- Replaced worker construction's legacy-root provider with the snapshot client.
  Construction validates before lease recovery/claim, the process runner opens
  a new snapshot immediately before spawn, and each start/lease heartbeat opens
  another snapshot before recording progress.
- Safety drift before the first start heartbeat terminates the fixture and
  blocks the still-`CLAIMED` attempt; ACTIVE, stale, and invalid drift paths do
  not finalize success or leave the fixture child alive.

## TDD evidence

Initial RED:

- `uv run pytest -q tests/jobs/test_safety_state_exporter.py tests/jobs/test_safety_state.py tests/jobs/test_worker_lifecycle.py`
  - exit 2 during collection because `services.safety_state_exporter` and
    `services.job_worker.safety_state` did not exist.

Boundary-filter RED:

- `uv run pytest -q tests/jobs/test_safety_state_exporter.py -k composition_discards`
  - failed because the exporter retained credential/database values in its
    environment mapping.

Heartbeat-order RED:

- `uv run pytest -q tests/jobs/test_worker_lifecycle.py -k running_fixture`
  - 3 failed because `start_attempt` ran before the fresh safety read.

GREEN after the minimal fixes:

- `uv run pytest -q tests/jobs/test_safety_state_exporter.py tests/jobs/test_safety_state.py tests/jobs/test_worker_lifecycle.py`
  - 45 passed.
- `uv run pytest -q tests/jobs/test_worker_lifecycle.py -k running_fixture`
  - 3 passed, 17 deselected.

## Fresh verification

- `uv run pytest -q tests/jobs/test_safety_state_exporter.py tests/jobs/test_safety_state.py tests/jobs/test_worker_safety.py tests/jobs/test_worker_lifecycle.py tests/jobs/test_process_runner.py`
  - 111 passed.
- `uv run python -m compileall -q services/safety_state_exporter services/job_worker tests/jobs/test_safety_state_exporter.py tests/jobs/test_safety_state.py tests/jobs/test_worker_lifecycle.py`
  - exit 0.
- `git diff --check`
  - exit 0.

## Self-review and concerns

- Exporter filesystem opens are limited to the canonical root, `.mode`,
  `.kill_switch`, the exact runtime directory, and one exact temporary/output
  name. It does not call `listdir`, `scandir`, `walk`, or glob production paths.
- Worker snapshot reads use `O_NOFOLLOW` and validate the opened descriptor, so
  atomic replacement cannot redirect a read after validation.
- The source fingerprint identifies the configured source boundary, while file
  owner/mode and atomic publication provide snapshot authority; it is not a
  content MAC and is not represented as one.
- `TRADING_SAFETY_EXPORTER_COMMIT` must be supplied to both exporter and worker
  by the protected unit configuration added in a later task. Missing or invalid
  authority fails closed.
- The fixed runtime parent must already exist, be owned by the service identity,
  and have no group/world permissions. The exporter intentionally does not
  create or relax it.
- No service/unit/release tooling, linked backend, database, real safety file,
  credential, process, port, or live runtime was accessed or changed.

Rollback is code-only: revert this task commit to restore the prior direct
provider. Runtime rollback remains outside this task and must stop the worker
before the exporter; no runtime deployment was performed here.

## Review follow-up: typed safety drift through validation

The review found two related lifecycle gaps: result-validation heartbeats did
not reopen safety evidence, and process-time safety drift collapsed distinct
reason codes into generic `SAFETY_DRIFT`.

The follow-up adds a typed heartbeat instruction and a bounded sanitized
`SAFETY_*` reason field on process outcomes. The process runner retains the
exact reason through anchored child cleanup, and worker finalization uses that
reason for the BLOCKED transition/event. Invalid injected reason shapes are
reduced to `SAFETY_STATE_INVALID`; valid exporter/client codes remain exact.

Result validation now reopens and validates the snapshot before its first
lease/progress heartbeat, at every validator progress callback, and once after
validation immediately before any `SUCCEEDED` transition. ACTIVE, stale,
invalid, and missing evidence finalize the RUNNING attempt as BLOCKED with the
distinct reason. A stale-success or retry path is not available.

Follow-up RED evidence:

- `uv run pytest -q tests/jobs/test_worker_lifecycle.py tests/jobs/test_process_runner.py`
  - exit 2 during collection because the required typed
    `HeartbeatInstruction` transport did not exist.
- `uv run pytest -q tests/jobs/test_worker_lifecycle.py -k after_validation_before_success`
  - failed because the completed validation still finalized `SUCCEEDED` when
    safety became missing immediately before finalization.

Follow-up GREEN evidence:

- `uv run pytest -q tests/jobs/test_worker_lifecycle.py tests/jobs/test_process_runner.py`
  - 69 passed.
- `uv run pytest -q tests/jobs/test_worker_lifecycle.py -k after_validation_before_success`
  - 1 passed, 26 deselected.

The added lifecycle cases cover drift while the fixture child is running,
after child exit before validation, and during validation progress. Assertions
require the exact `SAFETY_KILL_SWITCH_ACTIVE`, `SAFETY_STATE_STALE`,
`SAFETY_STATE_INVALID`, or `SAFETY_STATE_MISSING` final reason, BLOCKED state,
no retry/SUCCEEDED finalization, and no surviving fixture child.

Fresh follow-up verification:

- `uv run pytest -q tests/jobs/test_worker_lifecycle.py tests/jobs/test_process_runner.py`
  - 70 passed.
- `uv run pytest -q tests/jobs/test_safety_state_exporter.py tests/jobs/test_safety_state.py tests/jobs/test_worker_safety.py tests/jobs/test_worker_lifecycle.py tests/jobs/test_process_runner.py`
  - 119 passed.
- `uv run python -m compileall -q services/job_worker tests/jobs/test_worker_lifecycle.py tests/jobs/test_process_runner.py`
  - exit 0.
- Scoped `git diff --check`
  - exit 0.

## Review follow-up: freeze the first process safety trigger

The cleanup loop previously replaced its saved safety reason on every later
heartbeat even after `SAFETY_DRIFT` had already established the termination
cause. A child first stopped by an ACTIVE kill switch could therefore be
recorded as stale if the short-lived snapshot expired during bounded cleanup.

The process runner now captures a typed safety reason only in the same branch
that first establishes a non-CONTINUE termination. Later cleanup heartbeats
still run and can assist cleanup, but cannot replace that first attributable
cause. Worker finalization consequently receives the original reason for the
job, attempt error code, and transition event.

RED evidence:

- `uv run pytest tests/jobs/test_process_runner.py -k freezes_first_safety_reason -vv`
  - failed after observing `SAFETY_KILL_SWITCH_ACTIVE` then
    `SAFETY_STATE_STALE`; the outcome incorrectly contained the later stale
    reason.

GREEN evidence:

- `uv run pytest -q tests/jobs/test_process_runner.py -k freezes_first_safety_reason`
  - 1 passed.
- `uv run pytest -q tests/jobs/test_process_runner.py tests/jobs/test_worker_lifecycle.py`
  - 71 passed.

Fresh broader verification:

- `uv run pytest -q tests/jobs/test_safety_state_exporter.py tests/jobs/test_safety_state.py tests/jobs/test_worker_safety.py tests/jobs/test_worker_lifecycle.py tests/jobs/test_process_runner.py tests/jobs/test_worker_claims.py tests/jobs/test_worker_leases.py`
  - 133 passed, including disposable-database job/attempt/event finalization
    coverage.

## Deployment audit follow-up: canonical user runtime path

The protected worker authority and exporter previously disagreed: the worker
was bound to `/run/user/<uid>/trading-agent/safety-state.json`, while the
exporter published under `~/.local/run`. The exporter now derives its one fixed
path as `/run/user/{os.geteuid()}/trading-agent/safety-state.json`; it does not
consult `XDG_RUNTIME_DIR` and cannot be redirected by an attacker-controlled
XDG value.

Exporter composition explicitly rejects `TRADING_SAFETY_STATE_PATH` rather
than accepting a path override. Atomic publication remains relative to the
already-opened `trading-agent` parent dirfd and still requires that directory
to be owned by the effective service uid with no group/world permissions. The
exporter does not create the parent; the later unit owns that
`RuntimeDirectory=trading-agent` responsibility, and the worker consumes the
same protected-authority path read-only.

RED evidence:

- `uv run pytest -q tests/jobs/test_safety_state_exporter.py`
  - 2 failed, 11 passed: the default was still `~/.local/run/...`, and the
    explicit path override was not rejected.

GREEN evidence:

- `uv run pytest -q tests/jobs/test_safety_state_exporter.py`
  - 13 passed, including wrong-runtime-owner rejection and descriptor-anchored
    atomic replacement.
- `uv run pytest -q tests/jobs/test_safety_state_exporter.py tests/jobs/test_safety_state.py`
  - 29 passed.
- `uv run pytest -q tests/jobs/test_safety_state_exporter.py tests/jobs/test_safety_state.py tests/jobs/test_worker_safety.py tests/jobs/test_worker_lifecycle.py tests/jobs/test_process_runner.py`
  - 126 passed.

No runtime directory, service, unit, safety file, root-owned path, or systemd
state was created or modified by this follow-up.

## Deployment audit follow-up: private mounted safety sources

The exporter no longer opens the active legacy root. Its source contract is
split into two fixed roots:

- `CANONICAL_SOURCE_ROOT` remains
  `/home/thenam176/.hermes/crypto-research` and is used only to compute the
  canonical `.mode`/`.kill_switch` source fingerprint paths.
- `MOUNTED_SOURCE_ROOT` is
  `/run/user/<euid>/trading-agent/safety-sources` and is the only directory the
  exporter opens for source bytes.

The mounted root must be a non-symlink directory owned by the effective uid
with exact mode `0700`. Reads remain dirfd-relative and allowlisted to `.mode`
and `.kill_switch`; no directory enumeration occurs. An extra `.env` or other
credential-like file in either the canonical or mounted test fixture is never
opened. Missing `.kill_switch` remains `INACTIVE`, while missing `.mode` is
`UNKNOWN`.

The output remains the distinct sibling
`/run/user/<euid>/trading-agent/safety-state.json`, atomically replaced through
its separately opened parent dirfd. Production composition rejects canonical,
mounted, generic-source, and output path override keys. It does not consult
the active legacy root or an environment-selected mount.

RED evidence:

- `uv run pytest -q tests/jobs/test_safety_state_exporter.py`
  - collection failed because the required `CANONICAL_SOURCE_ROOT` and
    mounted-source interface did not exist.

GREEN evidence:

- `uv run pytest -q tests/jobs/test_safety_state_exporter.py`
  - 22 passed.
- `uv run pytest -q tests/jobs/test_safety_state_exporter.py tests/jobs/test_safety_state.py`
  - 38 passed.
- `uv run pytest -q tests/jobs/test_safety_state_exporter.py tests/jobs/test_safety_state.py tests/jobs/test_worker_safety.py tests/jobs/test_worker_lifecycle.py tests/jobs/test_process_runner.py`
  - 135 passed.

This follow-up did not create or inspect runtime mounts, start services, edit
units, access root, or read the canonical legacy safety/credential files.
