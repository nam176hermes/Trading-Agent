# Phase 3 Final Runtime Evidence

Captured 2026-07-11T12:52:13-04:00.

| Invariant | Final value |
|---|---|
| Requested / effective mode | `paper / paper` |
| Live gates | `false / false` |
| Kill switch | `INACTIVE` |
| Orders / trades | `30 / 0` |
| Active agent | active, PID `4181928`, unchanged |
| Active legacy dashboard | active, PID `4183789`, unchanged |
| Active dashboard port | `0.0.0.0:3002`, unchanged |
| Cloudflared | PID `3283180`, unchanged |
| PostgreSQL | `127.0.0.1:55432`, revision `0002_quarantine_lineage` |
| PostgreSQL reader | default transaction read-only `on` |
| Local API / candidate test ports | stopped; no listeners on 18400 or 3302 |
| Real apply guard | relocked; no-approval apply rejected |

Final canonical counts remain 43,055 and quarantine remains 222. Both runs are
complete; 4,656 tracking chunks are committed. Combined legacy inventory is
unchanged. No active service, port 3002, Cloudflare configuration, scheduler,
live gate, kill switch, model, prompt, strategy, or legacy source was changed.
No order or exchange call was made.
