# Phase 3 PostgreSQL Control API Smoke

The Control API ran only on `127.0.0.1:18400` with
`TRADING_STORE_BACKEND=postgres`. It was never exposed through Cloudflare.

`/health/live`, `/health/ready`, `/v1/meta`, `/v1/system/status`,
`/v1/market/latest`, `/v1/signals`, `/v1/decisions`, decision detail,
`/v1/capabilities`, and `/v1/costs` returned 200. The decision total was
16,517, latest report was `STALE` with 10 assets, and capability evidence was
9 total / 0 verified / `UNKNOWN`.

System status remained `PAPER / PAPER / NON_LIVE`, orders 30, trades 0. POST and
PUT to `/v1/decisions` returned 405. Migration run, audit, and decision counts
were identical before and after all GETs. No SQL exists in route handlers; SQL
is contained in repositories. A PostgreSQL-unavailable test returned readiness
503 and a read failure without legacy fallback.

No exchange module, mutation route, legacy writer, or public listener was
initialized by the API smoke.
