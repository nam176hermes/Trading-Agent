# Phase 3 First Apply Results

The approved source root was applied only after the final safety, source hash,
empty-target, backup, and restore gates passed.

| Field | Result |
|---|---|
| Run ID | `ee409404-d587-4e7c-add8-d5b580ff4a48` |
| Start / finish | `2026-07-11T16:31:44.148619Z` / `2026-07-11T16:32:22.830854Z` |
| Duration | 38.682261 seconds |
| Source inventory | `dbc94142b6773bb5a79c7bc889e7323ca92c03e5375d0a596b679c3f01c7b4ce` |
| Planner manifest | `06964c9ce162bf0fefa637c0a04d86eaea9b21deae0060ddec1555ba63f20892` |
| Schema / normalization | `0002_quarantine_lineage` / `phase3-v1` |
| Records seen | 19,298 source-level records |
| Canonical inserted / skipped / updated | 43,055 / 0 / 0 |
| Invalid / quarantine | 222 / 222 |
| Normalization audit rows | 2,349 |
| Source files / chunks | 2,295 / 2,328 |

Canonical inserts were assets 17, reports 2,186, report assets 23,961,
decisions 16,517, signals 344, capability evidence 9, cost summary 1, and
cost sessions 20. Decision reconciliation was 16,517 canonical plus 136
quarantined observations equals 16,653 source observations.

All chunks committed. Constraints were validated; orphan, duplicate source
identity, duplicate decision fingerprint, duplicate report-asset, and unsafe
quarantine payload checks returned zero. The latest report was
`2026-06-25T04:54:37.766581Z`, 10 assets, `STALE`.
