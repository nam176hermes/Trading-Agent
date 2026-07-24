# Phase 3B Real-data Dry-run

Run against source inventory
`dbc94142b6773bb5a79c7bc889e7323ca92c03e5375d0a596b679c3f01c7b4ce`
and PostgreSQL revision `0003_contract_lineage_repair` using the read-only
role. Normalization version was `phase3b-v1`.

## Results

### Decision price

```text
total decisions:       16,517
already exact:              0
backfillable exact:    16,517
backfillable derived:       0
unknown:                    0
conflicts:                  0
```

### Decision snippet

```text
total decisions:       16,517
already populated:          0
backfillable:          16,516
unknown:                    1
conflicts:                  0
```

The unknown is the explicitly empty direct field at decision source record 19.
It remains `NULL/UNKNOWN`; no text is generated or extracted from a weaker
source.

### Cost symbols

```text
sessions:                            20
sessions with evidenced symbols:    20
sessions with no evidence:            0
unknown assets:                       0
conflicts:                            0
planned normalized links:           200
```

### Asset lineage

```text
canonical assets:                   17
source lineage rows planned:    41,039
distinct source references:      2,209
conflicts:                           0
```

## Zero-write proof

Before and after values were identical:

```text
asset_source_lineage=0
cost_session_assets=0
decision_field_lineage=0
decisions_price=0
decisions_snippet=0
phase3b_backfill_events=0
phase3b_backfill_runs=0
```

Canonical rows remained 43,055 and quarantine remained 222. No estimated
price, generated snippet, heuristic symbol parser, unknown asset, asset
reconciliation failure, conflict, or canonical count change was observed.

**PHASE 3B DRY-RUN GATE: PASS FOR SCOPED APPLY IN THIS SESSION**
