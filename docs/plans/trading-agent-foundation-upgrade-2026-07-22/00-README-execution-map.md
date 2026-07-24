# Trading Agent Foundation Upgrade Plan

**Baseline report:** `trading-agent-foundation-assessment-2026-07-22.md`  
**Baseline commit:** `e8166622a181307c5aa5869f5900d9845f294e83`  
**Baseline branch:** `codex/canonical-monorepo`  
**Foundation score:** `84/100`  
**Current decision:** GO for continued paper-only development; NO-GO for production cutover or live trading.

## Purpose

This package converts the latest foundation assessment into executable Hermes Agent work packages. Each package is intentionally narrow, evidence-driven, and fail-closed.

The package does not authorize:

- live trading;
- runtime cutover;
- production PostgreSQL mutation;
- scheduler enablement;
- provider, broker, or exchange connectivity;
- deployment to the public dashboard;
- deletion of legacy data;
- modification of active runtime services without a separate approval record.

## Required execution order

```text
Package 1 - Host release proof
    |
    +--> Package 3 - Skip governance and critical coverage
    |
    +--> Package 4 - Canonical paper-only/live-boundary hardening
    |
    +--> Package 5 - Warning, exception and maintainability cleanup

Package 2 - Disposable PostgreSQL runtime parity

Package 6 - Paper runtime foundation validation
    requires Package 1 = GO
    requires Package 2 = GO
    requires Package 4 = GO, including SEC-002
    requires a separate runtime Greenlight

Foundation re-assessment
    requires all eligible package gates and fresh controller verification
```

Packages 3 and 5 may be prepared in parallel only after Package 1 source changes stabilize. Package 4 depends on Package 1 because its proof inspects the built paper artifact. Package 6 must not begin until Packages 1, 2 and 4 have returned GO.

## Package map

| File | Goal | Priority |
|---|---|---|
| `01-p0-host-release-proof.md` | Prove an offline, symlink-free, runnable release build on the actual host | P0 |
| `02-p0-postgres-runtime-parity.md` | Run approved disposable PostgreSQL runtime parity for migration 0008 and event-ledger behavior | P0 |
| `03-test-governance-and-critical-coverage.md` | Turn skipped tests into a managed inventory and enforce branch coverage for critical paths | P1 |
| `04-paper-live-boundary-hardening.md` | Prove canonical paper releases cannot expose live-capable legacy entrypoints | P1 |
| `05-maintainability-warning-and-fallback-cleanup.md` | Remove warnings and replace broad silent fallback behavior with explicit typed failure | P2 |
| `06-paper-runtime-foundation-validation.md` | Validate one complete paper-only runtime chain after P0 closure | P1 |
| `07-foundation-roadmap-and-scorecard.md` | Consolidated roadmap, gates, score targets and dependency map | Reference |
| `08-hermes-master-goal.md` | Master Hermes Goal command for orchestrating the packages safely | Operator entrypoint |
| `BACKLOG.csv` | Machine-readable implementation backlog | Planning |

## Preflight classes

Source-only packages and runtime packages use different preflights. Source work must not probe active runtime merely to edit or test source.

### Source preflight: Packages 1, 3, 4 and 5

Verify:

```text
repository root and AGENTS.md
baseline commit and branch
tracked and untracked status
allowed-path manifest
portable test environment forces paper mode and all live gates false
no active runtime, broker, exchange or operator database probe
```

### Disposable/staging runtime preflight: Packages 2 and 6

Verify without printing secret values:

```text
exact approval record and expiration
approved disposable/staging identity
requested_mode = paper
effective_mode = paper
LIVE_EXECUTION_ENABLED = false
LIVE_TRADING_APPROVED = false
LIVE_TRADING_ENABLED = false where applicable
kill_switch = INACTIVE
forbidden runtime paths, ports and identities are rejected
```

If the applicable preflight cannot be verified safely, stop with:

```text
NO-GO - APPLICABLE SAFETY BASELINE NOT VERIFIED
```

## Universal evidence hierarchy

Use evidence in this order:

1. Fresh command output.
2. Runtime/service identity.
3. Database/catalog query.
4. Tests.
5. Source code.
6. Documentation.

Documentation alone never proves runtime readiness.

## Universal Hermes restrictions

Hermes may:

- inspect source and documentation;
- use the current clean root or an external worktree outside the repository root;
- edit source-only files;
- run isolated tests;
- build offline/staging artifacts;
- create disposable PostgreSQL fixtures after an exact approval record;
- generate reports and backlog evidence.

Hermes may not:

- start or recover operator-managed PostgreSQL;
- use sudo;
- restart active trading services;
- enable scheduler timers;
- enqueue real research jobs;
- run SNAPSHOT against the real data root;
- call exchange, broker, provider, or credential endpoints;
- modify Cloudflare or port 3002;
- create a linked worktree below the repository root;
- add or change dependencies without component-specific review and explicit approval;
- delete PGDATA, runtime evidence or legacy data without a command-specific Greenlight;
- weaken fail-closed gates to make tests pass.

## Completion target

After Packages 1 and 2 pass, perform an interim P0 closure re-assessment.

Run the final foundation re-assessment after every planned package has reached a terminal state: `GO`, `NO-GO`, `BLOCKED`, or explicitly deferred by Nam. A final score above 90 is justified only when every required package gate exits 0 and the closure matrix is updated from fresh evidence.

An interim P0 score does not authorize production cutover or live trading.
