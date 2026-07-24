# Phase 4 Worker Attribution Synchronization

Date: 2026-07-12

## Scope

The migration worker now matches reviewed crypto-research commit
`0de20a05c6c1d44a91227b4d032403e99afc099e`. This was a code, test, ADR, and
evidence update only. It did not deploy a release, provision a manifest, start
or restart a process, enable a service/timer, or touch trading execution.

## Contract synchronized

- `/opt/trading-agent-research/releases/<full revision>` and
  `/etc/trading-agent/releases/<full revision>.json` are versioned by the exact
  reviewed commit.
- The manifest SHA-256 remains intentionally unprovisioned and fail closed until
  Task 12 provisions and reviews the immutable release.
- Child attribution uses exact `job_<32 lowercase hex>` and
  `attempt_<32 lowercase hex>` identifiers and `TRADING_JOB_ATTEMPT_ID`.
- Backend revision flows through attestation, built command, process outcome,
  and result validation. The process runner does not import approval state.
- The scratchpad input is fixed to
  `/home/thenam176/.hermes/crypto-research/.dexter/scratchpad`.
- Worker-owned lineage cannot be supplied through settings/source input; the
  legacy attempt variable is rejected and never sent to the child.
- Reports require exact job, attempt, backend revision, and research-only
  lineage. Replay sidecars require the reviewed exact six-key schema and only
  bounded sanitized event metadata.

## Backend fixture seam

Cross-boundary tests copy the reviewed backend's canonical environment values,
report lineage, and exact replay sidecar shape. The replay sidecar has no
`research_only` key by backend contract: its writer is reachable only through
the attributed research-only replay path. Adding that key in the migration
validator would reject every valid artifact from the approved revision.

## Rollback

Revert this synchronization commit. Do not point the worker at a different
release, populate a digest, or start services until Task 12 has separately
reviewed and provisioned the immutable artifacts.

## Review report

Self-review found no remaining high- or medium-severity issue in the scoped
diff. The main compatibility question was the missing `research_only` field on
replay sidecars. The approved backend's exact fixture proves that omission is
intentional, while reports do emit the field. The validator therefore enforces
the exact sidecar shape and documents the research-only reachability invariant
instead of inventing a seventh key the approved backend cannot produce.

Residual operational work is intentionally deferred: Task 12 must build the
immutable release, generate and independently review its exact-set manifest,
set the digest, provision root-owned paths, and perform the gated no-op runtime
smoke before any service activation.

This checkpoint is historical. Immutable-runtime and semantic-input hardening
subsequently advanced the reviewed backend pin to
`51de1cf06b3d595a336e19390230d0c09b608585`; the current command registry and
allowlist ADR carry that final identity.

## Verification

- Focused red/green contract coverage passed for revision pinning, source
  override rejection, strict IDs, last-moment child environment, result
  lineage, and exact replay schema.
- `tests/jobs`: 442 tests collected; the fresh full run completed without a
  failure.
- `tests/control_api`: 101 tests collected; 100 completed successfully and
  `test_generated_contracts_are_present_and_current` failed in an unrelated
  subprocess because `uv run python scripts/generate_contracts.py --check`
  cannot import the existing `packages.safety_evidence` module from its script
  path. The direct command reproduces the same baseline import-path failure.
- `python -m compileall -q services tests/jobs` and `git diff --check` passed.
