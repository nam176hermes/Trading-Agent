# Foundation Roadmap and Scorecard

## Baseline

| Area | Baseline |
|---|---:|
| Overall foundation | 84/100 |
| Source foundation | 87.8/100 |
| Runtime/release readiness | 5/10 |
| Production cutover | NO-GO |
| Live trading | NO-GO |

## Roadmap

### Milestone 1 - Releasable source

Packages:

```text
Package 1
Package 3
Package 5
```

Exit:

- host offline release proof pass;
- skip inventory managed;
- critical coverage measured;
- warnings/fallback behavior controlled.

### Milestone 2 - Database runtime authority

Package:

```text
Package 2
```

Exit:

- migration 0008 runtime proof;
- event-ledger runtime proof;
- restore semantic parity;
- dual-read parity;
- disposable cleanup.

### Milestone 3 - Reduced live authority

Package:

```text
Package 4
```

Exit:

- paper artifact cannot invoke live execution;
- no live credentials in child environment;
- future live artifact requires separate governance.

### Milestone 4 - Paper runtime foundation

Package:

```text
Package 6
requires Package 1 GO
requires Package 2 GO
requires Package 4 GO, including SEC-002
requires separate runtime Greenlight
```

Exit:

- one controlled snapshot;
- sealed artifact;
- valid event chain;
- queue idle;
- rollback proof.

## Estimated score impact

| Closure | Expected uplift |
|---|---:|
| Host release proof | +3 |
| PostgreSQL runtime parity | +4 |
| Skip governance | +1 |
| Critical coverage | +1 |
| Live-boundary hardening | +1 |
| Warning/fallback cleanup | +1 |
| Paper runtime evidence | qualitative runtime maturity increase |

A score above 90 is not automatic. Re-run the full assessment using fresh evidence.

## What not to do yet

- Add trading models.
- Add coins/brokers.
- Enable live trading.
- Tune LLM prompts for alpha.
- Redesign the dashboard broadly.
- Enable automatic scheduler production.
- Claim profitability.
- Claim production readiness from source tests alone.

## Next roadmap after foundation >90

Only after this package passes:

```text
Quant/model inventory
→ point-in-time/leakage audit
→ walk-forward/OOS
→ deterministic risk service
→ paper OMS/reconciliation
→ G0–G4 paper evidence
```

## Re-assessment command

Hermes should create a new assessment only after Packages 1 and 2 pass. The report must use fresh command output and must not simply add estimated points to 84.
