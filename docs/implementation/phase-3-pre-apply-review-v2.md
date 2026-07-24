# Phase 3 Pre-Apply Review V2

## Resolved blockers

- Fixture-only explicit transactional apply passes.
- Second-run canonical inserts are zero.
- Resume skips committed chunks and retries failed chunks.
- Mid-chunk failures roll back all same-transaction rows.
- Canonical-content collisions do not overwrite.
- Quarantine rows retain bounded lineage and no full payload.
- Changed source metadata rejects old-run resume.
- Legacy integration output is isolated and 43/43 passes without live writes.
- Real-root CLI apply remains unconditionally hard-blocked.

## Approved decision policy

`WATCH` and `WATCH FOR EXIT` remain archived/quarantined observation states.
They are not executable `DecisionAction` values and are not mapped to another
action. Canonical count 16,517 plus quarantine count 136 reconciles to all
16,653 source observations.

## Storage and safety

Alembic is at `0002_quarantine_lineage`. The staging target still contains zero
rows in all fifteen operational/migration tables. PostgreSQL listens only on
`127.0.0.1:55432`. Both active legacy services retain their original PIDs,
paper/paper and false/false remain effective, the kill switch is inactive, and
orders/trades remain 30/0. Port 3002 and the existing cloudflared process were
unchanged.

No real-data apply was run. The next step requires explicit user review and a
separate session to remove or replace the pre-apply guard.

REAL-DATA APPLY STATUS: BLOCKED PENDING USER REVIEW
