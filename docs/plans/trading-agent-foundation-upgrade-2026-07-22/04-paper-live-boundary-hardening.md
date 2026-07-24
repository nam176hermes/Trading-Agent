# Package 4 - Canonical Paper/Live Boundary Hardening

## Goal

Ensure the canonical paper-only release cannot package, expose or invoke live-capable legacy execution entrypoints even if configuration is changed incorrectly.

The legacy backend currently retains a valid live policy path. Existing gates are strong, but the canonical paper release should have a smaller authority surface.

## Design principle

```text
Paper release:
research + paper execution only
no live command catalog
no live adapter initialization path
no live credential loading

Future live-capable release:
separate artifact
separate manifest
separate approval
separate account/subaccount
separate promotion gate
```

## In scope

- Inventory live-capable legacy entrypoints.
- Run CodeGraph or deterministic import/caller analysis before changing packaging.
- Trace imports and command catalog reachability across the 14 currently identified legacy consumers.
- Prove canonical release excludes those entrypoints.
- Add negative executable tests.
- Prevent live credentials from entering paper child environments.
- Add packaging allowlist/denylist at release construction time.
- Document future separation without enabling it.

## Required inventory

Include:

- `legacy/research-backend/live_execution_policy.py`;
- CCXT direct execution;
- CCXT bridge;
- Alpaca/broker paths;
- exchange adapters;
- order executor;
- credential loaders;
- mode mutation paths;
- live-specific CLI commands;
- kill-switch and risk preflight dependencies.

For each component classify:

```text
PACKAGED_AND_REACHABLE
PACKAGED_BUT_UNREACHABLE
EXCLUDED_FROM_PAPER_RELEASE
TEST_ONLY
ARCHIVE_ONLY
```

## Change-risk gate

This is a high-risk release and live-authority change. Before the first edit:

- identify every caller/importer of candidate excluded modules;
- distinguish command reachability from harmless import-time type/shared-code dependency;
- prohibit filename-only exclusion as proof;
- keep research-only imports runnable;
- require a second read-only review of the exact candidate diff;
- request approval before any dependency or protected configuration change.

## Release proof

Create an executable test that inspects the built paper release and fails if it contains or exposes:

```text
live execution CLI
real order submission command
live adapter registry entry
credential loader reachable by job command
unapproved broker module
live mode transition command
```

The test must use the built artifact, not only source grep.

## Child environment proof

Assert paper worker children do not receive:

```text
exchange credentials
broker credentials
withdrawal credentials
dashboard service token
database owner credentials
live approval values
```

Assert forced values:

```text
TRADING_MODE=paper
LIVE_EXECUTION_ENABLED=false
LIVE_TRADING_APPROVED=false
LIVE_TRADING_ENABLED=false
```

## Governance proof

Add an ADR stating that adding a live-capable entrypoint to the canonical release requires:

- new artifact class;
- manifest change;
- security review;
- promotion approval;
- account isolation;
- signed risk and execution gates;
- separate acceptance suite.

## Acceptance

- Canonical paper artifact excludes live-capable command entrypoints without breaking approved research imports.
- No paper job type can resolve to a live module.
- No child environment includes trading credentials.
- Live gates remain enforced at multiple layers.
- Existing paper and safety tests pass.
- No live code is deleted if required as legacy archive; it is blocked from active runtime.

## Stop conditions

Stop if the only way to pass is to weaken gates, rename live code without removing reachability, or delete evidence needed for rollback/audit.

## Deliverables

```text
docs/adr/ADR-canonical-paper-release-boundary.md
docs/implementation/foundation-live-path-inventory.md
docs/implementation/foundation-paper-release-exclusion.md
docs/implementation/foundation-paper-child-environment.md
docs/implementation/foundation-live-boundary-evidence.md
```

## Final decision

```text
GO - CANONICAL PAPER RELEASE HAS NO LIVE AUTHORITY
```

or:

```text
NO-GO - LIVE-CAPABLE PATH REMAINS REACHABLE
```
