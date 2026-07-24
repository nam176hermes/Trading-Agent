# Phase 3B Source Analysis

Captured against combined source inventory
`dbc94142b6773bb5a79c7bc889e7323ca92c03e5375d0a596b679c3f01c7b4ce`.
The analysis opened legacy files and SQLite read-only and made zero PostgreSQL
or legacy writes. Focused extractor tests passed: `6 passed`.

## Decision price

| Classification | Count |
|---|---:|
| Total canonical decisions | 16,517 |
| `EXACT` | 16,517 |
| `DERIVED` | 0 |
| `LEGACY_ESTIMATED` | 0 |
| `UNKNOWN` | 0 |

Every canonical record has a finite numeric `price_at_decision` stored directly
at `memory/decisions.jsonl:<line>.price_at_decision`. The source file hash is
`0e97979237e4f0eaee8bc20235696a278c5b91e765acb5da591c24a358f981a3`.
Values range from 0.0 to 78,091.34; quality `EXACT` describes faithful source
reproduction, not market-validity judgment. No linked-report, nested signal,
SQLite, portfolio, nearest-neighbor, or estimation source is needed or
approved.

The 12,783 reported differences are explained by PostgreSQL deriving the
contract field from `decision_signal_snapshots.close` instead of storing the
direct decision value.

## Report snippet

| Classification | Count |
|---|---:|
| Total canonical decisions | 16,517 |
| `EXACT` | 16,516 |
| `DERIVED` | 0 |
| `LEGACY_ESTIMATED` | 0 |
| `UNKNOWN` | 1 |

The source is the direct `report_snippet` field in the same decisions JSONL.
Lengths are 0 through 500 characters, so no truncation is required. The single
unknown is source record index 19, decision
`decision_1db61ca8648fc1c609fe8685`, SOL, whose stored field is the empty
string. It will be `NULL/UNKNOWN` with `SNIPPET_SOURCE_MISSING`; no report,
rationale, typed decision, or LLM text will replace it.

`memory/typed_decisions.jsonl` contains 10,574 typed records and `decisions/`
contains decision documents, but neither is required because the direct source
has higher precedence and already resolves every non-empty canonical snippet.

## Cost-session symbols

| Check | Count |
|---|---:|
| Sessions in API/import scope | 20 |
| Sessions with top-level structured `symbols` | 20 |
| Sessions without evidence | 0 |
| Canonical session-asset links | 200 |
| Unknown assets | 0 |

Each newest scratchpad JSONL has a top-level `symbols` list at record index 1.
Every list contains the same ten registered crypto symbols. Values canonicalize
through the registry, deduplicate, and sort. Nested tool arguments and free
text were inspected only to confirm they are unnecessary; the approved parser
does not use them.

## Asset lineage

All 17 assets currently present in PostgreSQL have direct structured source
evidence. The append-only plan retains every canonical imported occurrence:

| Source type | Planned rows |
|---|---:|
| Asset registry | 17 |
| Canonical decision JSONL records | 16,517 |
| Valid market report asset records | 23,961 |
| SQLite signal rows | 344 |
| Structured cost-session symbols | 200 |
| **Total** | **41,039** |

There are 2,209 distinct source references: the asset registry, decisions
JSONL, SQLite signal logical source, 2,186 valid report files, and 20 scratchpad
files. SPY and QQQ exist in registry configuration but are not among the 17
canonical PostgreSQL assets, so Phase 3B does not create them or their lineage.

## Approved outcome

- Price uses only direct `EXACT` evidence.
- Snippet uses only direct `EXACT` evidence; one empty source stays
  `NULL/UNKNOWN`.
- Cost symbols use only top-level structured lists.
- Asset lineage is many-to-many and append-only.
- No estimation, dynamic report matching, free-text inference, or fabricated
  data is approved.

**SOURCE ANALYSIS GATE: PASS FOR ADR AND SCHEMA DESIGN**
