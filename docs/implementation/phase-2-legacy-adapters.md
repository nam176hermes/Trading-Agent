# Phase 2 Legacy Adapters

- Market repository scans only `report_*.json`, validates all assets, sorts by semantic `as_of`/`timestamp`, and returns FRESH/STALE/NO_DATA/UNKNOWN. Invalid sources are counted and logged without raw payloads.
- Decision repository streams JSONL, skips invalid lines, normalizes fields, derives stable SHA-256-based IDs from line position/content, supports filters/total/pagination, and does not rewrite source files.
- Operational status opens SQLite using `mode=ro`, reads mode/kill-switch/live-price state, reverse-chronological pagination, detail lookup, and asset/action/date filters.
- Status repository opens SQLite in `mode=ro`, separates API readiness, backend liveness, research freshness, live-price freshness, mode, execution capability, kill-switch state, and order/trade counts.
- Capability repository returns nine `UNKNOWN` records until current benchmark evidence exists.
- Cost repository labels evidence `UNKNOWN` without observed LLM accounting and `ESTIMATED` when only legacy call events are available.

All adapters receive a configured root. None imports execution, broker, exchange, scheduler, or credential code. Fixture side-effect tests compare every source file's size and nanosecond mtime before/after all GET endpoints.
