# Phase 3 Candidate Dashboard PostgreSQL Smoke

The candidate ran locally on `127.0.0.1:3302` with
`CONTROL_API_ENABLED=true`, connected to the PostgreSQL-backed Control API on
`127.0.0.1:18400`. Active port 3002 was untouched.

Browser automation rendered `/`, `/signals`, `/risk`, `/history`, and `/plan`.
All routes returned 200 and the final browser pass had zero console errors or
warnings. The command center displayed:

```text
PAPER · NON_LIVE · STALE
Canonical Decisions: 16517
```

Market confidence and risk content came from the PostgreSQL-backed contract;
capability remained unknown. In Control API mode, signal cards no longer call
the legacy `/api/memory` filesystem route. There was no fake data fallback,
mutation request, order call, or exchange initialization.

The local candidate and API test processes were stopped after smoke and
rollback proof.
