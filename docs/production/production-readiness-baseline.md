# Production Readiness Baseline

## Decision

The initial promotion decision is **`NO_GO`**. The decision vocabulary is
closed to `NO_GO`, `GO_PAPER_PRODUCTION`, and `GO_LIVE_LIMITED`; baseline
capture has no decision override and always emits `NO_GO`. Paper production is
the only contemplated next promotion target. It is not authorized by this
baseline. Live-limited operation is not authorized, and passing a source, test,
or evidence gate does not enable live trading.

The machine-readable decision is recorded in
[`promotion-status.json`](promotion-status.json) as schema v2 with
requested/effective mode `paper/paper`, `live_execution_enabled=false`, and
`live_trading_approved=false`. Its source binding is explicitly historical.
Its deployment-evidence state is `UNAVAILABLE`, with both evidence path and
SHA-256 reference set to `null`; the record therefore makes no claim about the
currently deployed release, unit files, or processes.

## Bound source and checks

- Canonical source root: `/home/thenam176/projects/trading-agent`
- Historical baseline source commit: `e304d83da260d11120ac648d67882359645c68a5`
- Historical baseline source tree: `bf4d1fb20944670df8110fc7eee3dbe3bc390b55`
- Canonical branch: `codex/canonical-monorepo`
- `make audit`: PASS for the core, research-backend, and dashboard components
- `make check-contracts`: PASS with no generated-contract drift
- Focused baseline schema test: PASS after the required RED failure

The baseline was captured from source and local validation only. The active
runtime was not probed. No provider, broker, exchange, account, balance,
position, order, withdrawal, service, scheduler, production database, or
credential endpoint or state was read, contacted, started, stopped, or
mutated.

## Current-source note (2026-08-03)

This baseline is historical and remains immutable. Later paper-only source
work, including P10 canonical-market-data and critical-coverage ratcheting,
does not update this record, change `promotion-status.json`, or establish
deployment evidence. The current source candidate remains unsealed and cannot
be read as production readiness, release installation, or trading approval.

## Observational deployment evidence

The checked
[`source-release-unit-pid.schema.json`](../../ops/evidence/source-release-unit-pid.schema.json)
contract describes a read-only observation chain from source commit/tree to an
immutable release manifest, effective unit identity, and reuse-safe process
identity. Every source-to-release, release-to-unit, and unit-to-process link is
exactly `VERIFIED`, `DRIFTED`, or `UNAVAILABLE`. Process identity requires the
PID, Linux process start ticks, and command fingerprint; a bare PID is never
sufficient.

The JSON Schema is a closed structural contract; schema-only validation is not
authoritative. `packages.deployment_evidence.DeploymentEvidence` is the
normative semantic validator for normalized absolute paths, exact RFC 3339 UTC
timestamps, unique service IDs, link/identity consistency, and secret-like key
rejection. Consumers must apply that validator after structural validation;
the schema's `x-semantic-validation` marker records this requirement for
machines.

Observational evidence does not authorize promotion. Even a fully `VERIFIED`
chain remains `NO_GO` in this module because promotion authority is absent.
The contract adds no collector, publisher, systemd or procfs reader, cutover
behavior, runtime authorization, or Release Authority v2. Missing, malformed,
drifted, or unavailable evidence also remains `NO_GO`.

## Initial promotion blockers

The decision remains `NO_GO` until the production-readiness plan closes and
records evidence for all applicable blockers, including:

- provider-secret containment, external rotation evidence, and a publish-safe
  source snapshot;
- complete fail-closed authorization at every order, cancel, and close sink;
- bounded dashboard login input and explicit trusted-proxy handling;
- safe model-artifact handling with legacy and LLM output kept shadow-only;
- hermetic mandatory PostgreSQL promotion validation; the disclosed core
  PostgreSQL authority failure is assigned to Task 4 and is not waived here;
- reproducible CI plus dependency and security gates for all three preserved
  dependency graphs and lockfiles;
- deterministic intent, risk, signed plan, paper execution, reconciliation,
  model governance, telemetry, and readiness controls;
- current G0-G4 evidence, including the full unchanged paper observation
  window and required failure drills; and
- immutable release authority, backup/restore proof, paper cutover, rollback
  proof, and a final centralized promotion assessment.

Live-limited operation has additional blockers: a completed paper-production
observation window, a separately approved exact ADR, two explicit approvals,
fake-server validation, and a bounded canary. Those conditions are outside
Task 0 and do not alter this paper-only baseline.

## Evidence preservation

Later tasks may update the current promotion status and add references to new
evidence. They must not rewrite historical evidence records. Missing, stale,
mismatched, or unverifiable evidence keeps the decision at `NO_GO`.
