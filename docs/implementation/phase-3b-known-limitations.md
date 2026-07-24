# Phase 3B Known Limitations

- One of 16,517 canonical decisions has an explicitly empty legacy
  `report_snippet`. PostgreSQL stores `NULL/UNKNOWN`; the existing string
  contract renders it as empty. No text can be recovered without fabrication.
- The 136 `WATCH`/`WATCH FOR EXIT` observations remain quarantine-only. This is
  the accepted normalization boundary, not a Phase 3B contract defect.
- Asset lineage intentionally retains 41,039 exact source occurrences. Future
  archival/partition policy may be useful at larger scale, but compaction must
  not discard provenance.
- SPY and QQQ are registry-configured but absent from the 17 canonical assets;
  Phase 3B correctly creates neither assets nor lineage for them.
- The research scheduler remains unrestored and reports remain stale by prior
  design. Phase 4 may address durable queues/scheduling without enabling live
  trading.
- Contract generation and migration tests emit two upstream deprecation
  warnings. They do not change output or acceptance.
- Deep-offset decision pagination remains a previously documented scalability
  limitation; Phase 3B does not alter ordering or pagination.

There are zero unresolved price, cost-symbol, asset-lineage, migration,
ordering, or contract bugs. There are zero Phase 3B conflicts.
