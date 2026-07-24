# Task 7 Report: Fail-Closed Command and Safety Attestation

## Outcome

Task 7 defines and validates authority; it does not spawn a process. No
deployment directory, manifest, runtime service, child process, database,
exchange, broker, order, or trade was created or changed. Production remains
blocked until operations provisions a separately reviewed release and matching
code-owned manifest digest.

## Canonical safety evidence

- The worker and read-only Control API use the same resolver for the exact
  canonical `.kill_switch`.
- Absent means `INACTIVE`; one valid timestamp/reason sentinel means `ACTIVE`;
  malformed, unreadable, symlinked, wrong-owner, or unsafe-mode present evidence
  means `UNKNOWN`. There is no `INACTIVE` sentinel content format.
- Missing/invalid `.mode` and missing/invalid live gates remain unknown and
  block worker execution. Worker path overrides remain forbidden.

## Immutable release authority

- The root-owned deployment is fixed at
  `/opt/trading-agent-research/releases/5f444971f0778806f1d9caf64931abf5856f5da5`.
- The external `trading-agent-release-manifest/v1` path and SHA-256 are fixed in
  code. Its exact-set contract covers every directory and file below the root,
  including the whole venv, native extensions, `.pth`, data/config, dot, and
  ignored files. Every entry has path/type/mode/size/SHA-256.
- Attestation uses no Git command, subprocess, injected runner, working-tree
  state, or tracked-file subset. It rejects every extra/missing/changed/symlink
  entry and unsafe file type.
- Deployment and manifest ancestors must be root-owned, not group/world
  writable, and free of special mode bits. The manifest, root, directories,
  interpreter, and all artifacts are root-owned, read-only, free of
  setuid/setgid/sticky bits, and have no extended attributes (including POSIX
  ACL and `security.capability`). The fixed interpreter is manifest-covered,
  executable, and invoked with `-I -B`.
- Capabilities are opaque, weak-set-issued, short-lived by monotonic time, and
  single-use. Build consumes before revalidating the full manifest and the
  root/interpreter/manifest device+inode identities. Expiry is checked both
  before and after the costly full re-attestation.
- `prepare_immediate_spawn(job)` returns an opaque short-deadline token, not a
  reusable `BuiltCommand`. Task 9 must call
  `consume_prepared_spawn(prepared)` exactly once at its direct `Popen`
  boundary; delayed, forged, and second use block.

## Child environment

- `ResearchEnvironmentSettings` has no public constructor fields and is accepted
  only if issued into a weak set.
- Exact fixed data/output/scratch roots and dedicated credential key names are
  revalidated on every build. Output/scratch roots are worker-owned mode `0700`,
  exact, and non-symlink.
- The child starts from an empty environment, receives only dedicated
  `TRADING_RESEARCH_*` credentials, uses fixed `PATH` and isolated `HOME`, and
  forces paper mode plus all live gates false.

## TDD and verification

RED was observed as collection failure for the new release-manifest API before
implementation. Fresh final evidence:

```text
uv run pytest -q tests/jobs/test_command_registry.py \
  tests/jobs/test_worker_safety.py tests/jobs/test_child_environment.py \
  tests/control_api/test_status_repositories.py
80 passed

uv run pytest -q tests/jobs
304 passed, 1 pre-existing Starlette deprecation warning

uv run pytest -q tests/control_api/test_status_repositories.py
6 passed

uv run python -m compileall -q packages/safety_evidence.py \
  services/job_worker apps/control_api/control_api/repositories/status.py
exit 0

git diff --check
exit 0
```

## Remaining deployment gate

Operations must produce and separately review the complete immutable release
and external manifest, then update the code-owned manifest SHA-256 through a
reviewed code change. Until both exact fixed paths exist with matching content
and root-owned permissions, command attestation blocks. Task 7 intentionally
did not provision them.
