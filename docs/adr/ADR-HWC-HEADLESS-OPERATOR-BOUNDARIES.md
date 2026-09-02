# ADR: Headless core and operator boundaries

**Status:** Accepted source architecture; runtime deployment held  
**Date:** 2026-09-02

## Decision

Trading-Agent uses a headless core with three narrow HTTP authorities. The
read-only Control API owns canonical queries, the Job API owns durable research
and backtest jobs, and a separate Operator API owns only requested-mode PAPER,
kill-switch activation, and safety-checked kill-switch clear. The dashboard and
CLI are untrusted clients of those APIs.

The dashboard may authenticate users and record authorization audit events. It
does not own trading state, jobs, risk, execution, ledger, accounting, data,
alpha truth, runtime lifecycle, or live eligibility. WEB credentials may only
activate the kill switch. CLI credentials may read raw operator state, request
PAPER, activate the kill switch, and clear it when canonical safety evidence is
fresh and fail-closed.

## Source stages

HWC-A freezes and mechanically enforces the architecture, contracts, protected
state journal, command service, credentials, API, and `ARCH_CONTRACT_READY`
gate. HWC-B removes dashboard compatibility authority, adds the independent
CLI, proves dashboard-independent operation and command recovery, and derives
source readiness. Exact-byte grandfathering is permitted only until its named
HWC-B migration task removes the current writer.

## Invariants

- Control API remains read-only and Job API remains the durable job authority.
- Operator commands are authenticated, policy checked, journaled as durable
  intent/applied/receipt records, and idempotent.
- Ambiguous mutation recovery is unavailable, never ordinary no-change.
- The production safety root remains
  `/home/thenam176/.hermes/crypto-research` and is not generalized for tests.
- Dashboard or CLI loss cannot change or stop canonical core state.
- Broker, network-trading, production, and live authority remain false.

## Migration and rollback

Current writer bytes are listed in the HWC boundary policy. Any byte drift or
new writer fails the boundary gate. A migrated writer is removed from the
policy immediately; final source qualification permits no trading-state debt.
Before deployment, rollback is source-only. After journal data exists, rollback
must preserve it and fail closed on unknown records. Rollback never clears a
kill switch.

## Authority limit

This ADR authorizes no deployment, service change, database mutation, broker or
exchange access, Release Authority v2 activation, live eligibility, or live
execution. Source qualification is not runtime authority.
