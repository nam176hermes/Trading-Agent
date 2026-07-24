# Phase 3 Quarantine Policy

WATCH and WATCH FOR EXIT are observation states.
They are not mapped to an executable DecisionAction in Phase 3.
They remain preserved in the source archive and migration quarantine lineage.

Approved reconciliation:

```text
source decision observations: 16,653
canonical executable decisions: 16,517
quarantined observations: 136
WATCH: 122
WATCH FOR EXIT: 14
16,517 + 136 = 16,653
```

Each persisted error contains run ID, relative source path, source content hash,
one-based record index, payload hash, approved sanitized legacy value, stable
error code, bounded sanitized message, creation time, and normalization version
`phase3-v1`. Full JSONL payloads, prompts, credentials, API bodies, environment
values, and unbounded exceptions are never stored.

The observations are not lost: their source data remains untouched, their
content identity remains attributable, and their exclusion from the canonical
decision set is represented by `INVALID_ENUM` quarantine evidence.
