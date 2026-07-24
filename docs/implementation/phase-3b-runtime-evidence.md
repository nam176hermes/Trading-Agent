# Phase 3B Runtime Evidence

Captured after schema, backfill, dual-read, contract, candidate, backend, and
API smoke verification.

| Invariant | Final value |
|---|---|
| Requested / effective mode | `paper / paper` |
| Live gates | `false / false` |
| Kill switch | `INACTIVE` |
| Orders / trades | `30 / 0` |
| Active agent | active, PID `4181928`, unchanged |
| Active legacy dashboard | active, PID `4183789`, unchanged |
| Dashboard listener | only `0.0.0.0:3002`, unchanged |
| Cloudflared | PID `3283180`, unchanged |
| Trading research timers / cron | `0 / 0`, scheduler remains unrestored |
| Local smoke ports 18400 / 3302 | stopped; no listener |
| PostgreSQL | `127.0.0.1:55432`, `0003_contract_lineage_repair` |
| Reader default transaction | read-only `on` |
| Canonical / quarantine | `43,055 / 222` |
| Phase 3B lineage | 33,034 decision / 200 cost links / 41,039 asset |
| Backfill runs / conflicts | 4 completed / 0 events |
| Source inventory | `dbc94142b6773bb5a79c7bc889e7323ca92c03e5375d0a596b679c3f01c7b4ce` |
| Phase 3B guard | relocked; unapproved apply rejected |
| Original Phase 3 guard | relocked; unapproved apply rejected |

PostgreSQL API GET smoke left migration runs, Phase 3B runs, audit, quarantine,
decision, and all lineage counts identical. The API has no mutation route and
did not import or initialize exchange code. Explicit legacy-mode smoke passed
and the local process was stopped.

No active service, port 3002, Cloudflare configuration, scheduler, live gate,
kill switch, model, strategy, prompt, source file, order, or trade was changed.
