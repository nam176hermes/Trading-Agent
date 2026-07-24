# Acceptance and promotion gates

## Phase 0 gate

- Active process, cwd, repo/commit, port, command, and data root identified.
- Scheduler map covers cron, systemd, supervisor, PM2, tmux, and screen.
- All execution paths and flags inventoried without order calls.
- Live execution proven disabled.
- Canonical repo/dashboard/URL/data root proposed with rollback.

## Phase 1 gate

- Market and signal APIs return typed `VALID`, `STALE`, or `NO_DATA`.
- Malformed or unrelated reports cannot cause HTTP 500.
- Decision total and pagination are correct.
- Confidence `0.5` renders as `50%` everywhere.
- All current assets and signals pass runtime validation.
- Capability without current evidence is `UNKNOWN` or `STALE`.
- Ten known crypto assets route to crypto execution; unknown assets reject.
- Live mode remains disabled and no order was created.
- Build, typecheck, lint, targeted frontend tests, backend smoke tests, broker
  routing tests, and paper-trader tests pass.

## G0 - code and contracts

- Tests pass, dependencies are pinned, schemas are compatible, and no secrets
  are committed.

## G1 - point-in-time data

- No future-known data, snapshots are immutable, gaps are within policy, and
  every decision has lineage.

## G2 - research utility

- Positive predefined out-of-sample utility versus baseline; results are not
  dependent on one asset or one window; claims have evidence.

## G3 - robustness

- No lookahead; cost and slippage stress pass; partial fills, missed trades, and
  delayed execution are represented.

## G4 - paper operation

- Zero policy breaches, unresolved reconciliation mismatches, or orphan orders.
- Every order has idempotency and a RiskDecision.
- Paper P&L is replayable and a kill-switch drill passes.

Passing G4 is not authorization for live trading.
