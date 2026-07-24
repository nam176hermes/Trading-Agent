# Phase 2 Runtime Evidence

Captured 2026-07-11 10:00-10:10 America/Toronto.

| Item | Evidence |
|---|---|
| Active agent | `trading-agent.service`, PID 4181928, active, legacy backend cwd |
| Active legacy dashboard | `trading-dashboard.service`, PID 4183789, active, port 3002 |
| Verification API | ephemeral PID 114857, bound only `127.0.0.1:18400`, stopped after smoke |
| Requested/effective mode | PAPER / PAPER |
| Execution capability | NON_LIVE |
| Hard gates | false / false in verification process; active service configuration unchanged |
| Kill switch | INACTIVE |
| Orders/trades before and after | 30 / 0 and 30 / 0 |
| Latest market report | `report_20260625_045437.json`, `2026-06-25T04:54:37.766581Z`, 10 assets, STALE |
| Decision total | 16,653 |
| Capability evidence | 9 total, 0 verified, all UNKNOWN |

All ten required GET endpoints returned 200 with trace IDs. Known decision detail returned 200. POST market and PUT status returned 405. Report-directory metadata hash remained `2c92e2a7e7b2a4b322cc8c0f4d4bc1ecac6b8bd4f35b74bad27b5b2c8e27e299` before and after. Decision/report sizes and mtimes remained unchanged.

The active database heartbeat continues to be written by the pre-existing agent, so Phase 2 proof uses stable order/trade counts plus fixture-level full file metadata snapshots. No exchange module or credential loader is imported by the Control API, and no exchange call was made.
