# Hermes Goal - Upgrade Trading Agent Foundation

Use this as the master Goal command. Hermes must execute one package at a time and stop at every package gate.

## Goal

Upgrade the Trading Agent foundation from the verified 84/100 baseline to a runtime-proven, paper-only foundation above 90, without enabling live trading or changing strategy/model semantics.

## Inputs

Read:

```text
AGENTS.md
trading-agent-foundation-assessment-2026-07-22.md
00-README-execution-map.md
01-p0-host-release-proof.md
02-p0-postgres-runtime-parity.md
03-test-governance-and-critical-coverage.md
04-paper-live-boundary-hardening.md
05-maintainability-warning-and-fallback-cleanup.md
06-paper-runtime-foundation-validation.md
07-foundation-roadmap-and-scorecard.md
```

## Execution policy

1. Use the current clean repository root or an external worktree outside the repository root. Never create a linked worktree below `trading-agent/`.
2. Apply source preflight before source edits. Apply runtime preflight only before approved runtime actions. Never probe active runtime for source work.
3. Execute packages in dependency order.
4. Never combine Package 1 and Package 2 runtime operations in one approval.
5. Stop after each package and produce evidence.
6. Do not infer GO from documentation; require commands to exit 0.
7. Do not start Package 6 until Packages 1, 2 and 4 return GO, SEC-002 passes, and Package 6 receives a separate runtime Greenlight.
8. Do not enable a scheduler timer.
9. Do not perform public cutover.
10. Do not enable live trading.

## Package sequence

```text
RUN Package 1
STOP and report

If Package 1 GO:
    request exact disposable approval for Package 2
    if approved:
        RUN Package 2
        STOP and report
    if approval is pending or denied:
        mark Package 2 BLOCKED
        perform no runtime action

After Package 1 source stabilizes:
    RUN Package 3
    STOP and report
    RUN Package 4
    STOP and report
    RUN Package 5
    STOP and report

Package 2 approval waiting must not block source-only Packages 3, 4 and 5. Execute only one package at a time.

If Packages 1, 2 and 4 GO, including SEC-002:
    request a separate runtime Greenlight for Package 6
    do not start API/worker processes until approved
```

## Preflight routing

For source-only Packages 1, 3, 4 and 5, verify repository identity, baseline dirt, allowed paths, component rules and paper-forced test fixtures. Do not probe active runtime.

For runtime Packages 2 and 6, verify without printing secret values:

```text
exact approval and Greenlight
approved disposable/staging identity
requested/effective mode
all live gates
kill switch
forbidden runtime paths and ports rejected
tracked bounded process plan
```

If the applicable safety boundary is not verifiable:

```text
NO-GO - APPLICABLE SAFETY BASELINE NOT VERIFIED
```

## Repair authority

Hermes may automatically repair:

- source-only bugs;
- deterministic release tooling;
- validation defects;
- test isolation;
- warning/deprecation issues;
- skip governance;
- coverage configuration that does not alter protected dependency manifests;
- paper-release packaging exclusions after caller-impact analysis.

Dependency, lockfile, protected config, database, service startup and destructive changes always require their separate approval or Greenlight.

Hermes may not automatically:

- start/recover operator PostgreSQL;
- apply production migrations;
- start services;
- enable timers;
- enqueue real jobs;
- use provider/broker/exchange credentials;
- change risk thresholds;
- delete legacy data;
- deploy public dashboard;
- alter Cloudflare.

## Required package report

For each package return:

```text
Executive result
Files changed
Commits
Tests
Acceptance commands
Evidence paths
Safety invariants
Known limitations
Rollback
GO/NO-GO
```

## Final reassessment

After every planned package reaches a terminal state (`GO`, `NO-GO`, `BLOCKED`, or explicitly deferred by Nam), rerun the portable commands:

```bash
make ci
make audit-release
make test-runtime-release-host
```

Run PostgreSQL runtime commands only when the exact approval and command-specific Greenlight remain valid. A blocked or unexecuted runtime gate must remain visible in the score and final decision.

Create:

```text
docs/audits/trading-agent-foundation-reassessment-<date>.md
```

The reassessment must independently score the foundation. Do not merely add expected points.

## Final allowed decision

```text
GO - FOUNDATION ABOVE 90, PAPER RUNTIME PROVEN
```

or:

```text
GO - FOUNDATION IMPROVED, REMAINING GATES DOCUMENTED
```

or:

```text
NO-GO - FOUNDATION P0/P1 BLOCKERS REMAIN
```

Live trading remains NO-GO.
