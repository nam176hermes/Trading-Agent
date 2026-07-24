# Phase 1 Known Limitations

- Dashboard mutations are intentionally locked until an operator configures a protected password.
- Credentials previously stored in broadly readable overrides require external rotation (`ROTATION_REQUIRED`); no credential was rotated or exposed in this phase.
- PostgreSQL, Control API, durable job queue, and canonical operational audit database are not implemented.
- The research pipeline scheduler is not restored; research data remains stale since 2026-06-25.
- Historical models remain `LEGACY_UNVERIFIED`; no OOS or leakage validation was performed.
- Ten crypto execution routes remain disabled until venue symbols, market IDs, precision, and minimums are verified in a separate non-order task.
- Active legacy and backend worktrees contain extensive pre-existing/runtime changes, so reproducibility still depends on a controlled consolidation.
- The active dashboard build reports a non-fatal Turbopack NFT tracing warning caused by runtime filesystem resolution.
- `tests/test_integration.py` is a standalone script that calls `sys.exit`; invoking it through pytest yields a harness internal error after its 43 checks pass. It is run directly.
- Live trading remains NO-GO.
