# Phase 3 Fixture Import Evidence

The transactional writer was exercised only against isolated database
`trading_agent_test` and synthetic source identity `/synthetic/fixture`.

Fixture content included two reports with report assets, 503 valid decisions,
one `STRONG SELL` alias, `WATCH`, `WATCH FOR EXIT`, invalid confidence evidence,
more than 500 source positions, and two deterministic decision chunks.

## Results

```text
explicit apply=False: rejected
first apply valid decisions inserted/skipped/updated: 503 / 0 / 0
first apply reports/report assets: 2 / 2
second apply valid decisions inserted/skipped/updated: 0 / 503 / 0
canonical decision duplicates: 0
STRONG SELL normalization audit: persisted
quarantine rows after two runs: 6
full sensitive fixture payload retained: no
```

Report writes use one transaction per report source. Decision streams use
deterministic 500-record chunks. Domain rows, quarantine rows, audit events,
run counters, and committed checkpoints share the chunk transaction.

The production staging database `trading_agent` remained empty.
