# Phase 3 Explicit Legacy Rollback

After PostgreSQL smoke, only the local Control API test process was restarted
with `TRADING_STORE_BACKEND=legacy` on `127.0.0.1:18400`. This was an explicit
configuration rollback, not automatic fallback.

All Phase 2 read endpoints returned 200, latest report remained `STALE`, legacy
decision total was 16,653, capability was 0 verified, and system mode remained
`PAPER / PAPER`. The candidate on local port 3302 returned 200 for `/`,
`/signals`, `/risk`, `/history`, and `/plan`; browser console had zero errors.

Active agent/dashboard services, port 3002, and Cloudflare were unchanged. The
local rollback API and candidate were stopped after proof.
