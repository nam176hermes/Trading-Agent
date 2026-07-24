# NTA-1 implementation roadmap

1. Audit runtime and freeze live paths.
2. Recover one canonical paper dashboard and deny unknown assets.
3. Introduce strict contracts and a read-only Control API.
4. Migrate operational state to PostgreSQL with an idempotent legacy importer.
5. Add durable audited jobs and scheduler heartbeats.
6. Separate deterministic risk, signed plans, execution, and reconciliation.
7. Inventory models and introduce research agents in zero-weight shadow mode.
8. Gather G0-G4 paper evidence.
9. Consider a separately approved live-limited ADR only after evidence review.

Every stage uses a separate reviewed plan and branch, includes rollback notes,
and ends with fresh test and runtime evidence.
