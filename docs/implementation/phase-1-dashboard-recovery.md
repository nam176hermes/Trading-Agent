# Phase 1 Candidate Dashboard Recovery

The candidate dashboard remains undeployed.

- Only `report_*.json` is catalogued.
- Every candidate file is parsed and runtime-validated; mixed validation files and invalid JSON are skipped with structured warnings.
- Latest selection uses semantic `as_of`/`timestamp`, never filename or mtime.
- Responses use schema version `1.0` and typed `VALID`, `STALE`, `NO_DATA`, or `INVALID_SOURCE` states with generated time, age, and source basename.
- Market and signals return 200 for expected data states.
- Decision totals use the complete JSONL line count, independent of page limit.
- Confidence stays in `[0,1]` and is formatted through one shared percent formatter.
- Legacy `STRONG SELL` normalizes to `STRONG_SELL`; the domain also supports the ten baseline cryptos.
- Capability evidence defaults to `UNKNOWN` with evidence fields; module presence does not imply PASS.
- Deployment identity reports the candidate repo/build and explicitly says live execution is disabled.

At runtime, research reports remain stale since 2026-06-25 while `live_prices.json` continues to update. These are presented as separate health facts.
