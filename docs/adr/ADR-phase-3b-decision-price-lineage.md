# ADR: Phase 3B Decision Price Lineage

## Decision

Use `memory/decisions.jsonl.price_at_decision` from the same canonical decision
record as the only Phase 3B price source. All 16,517 values are `EXACT`.

## Precedence and quality

1. Same-record numeric `price_at_decision`: `EXACT`.
2. Explicitly linked report with an approved link/timestamp policy: `DERIVED`.
3. Approved approximate policy: `LEGACY_ESTIMATED`.
4. Otherwise: `NULL/UNKNOWN`.

Only item 1 is enabled. Report matching and estimation remain disabled because
they are unnecessary.

## Forbidden inference

Do not use signal close, nearest report/current price, filename time, portfolio
state, or exchange/broker data as a substitute. A stored numeric zero remains
an exact reproduction rather than being silently replaced.

## Identity and collision

Identity is domain + decision ID + source hash + one-based record index +
`phase3b-v1`. Lower-quality evidence is ignored. Equal-quality different
values record `EQUAL_QUALITY_CONFLICT` and do not overwrite.

## Contract and rollback

The existing public numeric field shape is preserved. PostgreSQL reads the
stored value rather than signal close. Internal lineage is not public.
Rollback is legacy read mode plus restore of the verified pre-change custom
dump; existing Phase 3 migration runs are never rewritten.
