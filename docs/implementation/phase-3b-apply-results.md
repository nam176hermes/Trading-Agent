# Phase 3B Apply Results

Applied against inventory
`dbc94142b6773bb5a79c7bc889e7323ca92c03e5375d0a596b679c3f01c7b4ce`,
Alembic `0003_contract_lineage_repair`, normalization `phase3b-v1`.

## First apply

Duration: 48 seconds.

| Domain | Run ID | Seen | Updated | Unknown | Conflicts | Lineage inserted |
|---|---|---:|---:|---:|---:|---:|
| decision-price | `e2552118-0aaf-55bb-b60a-c0b6f5409236` | 16,517 | 16,517 | 0 | 0 | 16,517 |
| decision-snippet | `cbc33ec8-c4e4-538f-a35d-296899b3abad` | 16,517 | 16,516 | 1 | 0 | 16,517 |
| cost-symbols | `846e2a56-b336-572f-8d96-6f24aed55d89` | 20 | 20 | 0 | 0 | 200 |
| asset-lineage | `45b768f6-62b1-5fd5-9b24-ca4c6d868eca` | 41,039 | 0 | 0 | 0 | 41,039 |

The unknown snippet stored `NULL`, provenance `UNKNOWN`, and exact source
lineage with `SNIPPET_SOURCE_MISSING`. No canonical action/confidence changed.

## Second apply

Duration: 24 seconds. The same deterministic run IDs were reused. Every domain
reported zero updates, zero conflicts, and zero lineage inserts. Unchanged
counts were 16,517 prices, 16,517 snippets, 20 cost sessions, and 41,039 asset
lineages.

Before/after snapshots were identical:

```text
phase3b_backfill_runs=4
decision_field_lineage=33,034
cost_session_assets=200
asset_source_lineage=41,039
phase3b_backfill_events=0
non-null decision prices=16,517
non-null decision snippets=16,516
action/confidence hash=9e04120548b86f46de8188719d8da8cf
```

Canonical rows remain 43,055 and quarantine remains 222. The stable canonical
export excluding the new repair fields remains
`b458c23c4ae861408070356e9e387a9fad89e1ae302e862e3544c685c0be3e7c`.

## Guard relock

After unsetting the four scoped variables:

```text
Phase 3B apply approval is not enabled
explicit real apply approval is not enabled
```

Both attempts were rejected before source/database writes. Runtime service PIDs
were unchanged during apply.
