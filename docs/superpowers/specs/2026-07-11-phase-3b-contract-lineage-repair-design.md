# Phase 3B PostgreSQL Contract and Lineage Repair Design

**Status:** Approved in conversation on 2026-07-11

**Goal:** Repair the four Phase 3 contract and lineage blockers without
fabricating legacy data, changing canonical actions/counts, or affecting the
active paper-trading runtime.

## Scope and safety boundary

Phase 3B covers decision `price_at_decision`, decision `report_snippet`, cost
session symbol attribution, and many-to-many asset source lineage. It may add a
new Alembic revision, deterministic read-only source extractors, guarded
backfill code, internal PostgreSQL lineage tables, tests, and acceptance
evidence.

The work must not change live gates, the kill switch, scheduler state, models,
strategies, prompts, port 3002, Cloudflare, active processes, orders, trades,
legacy sources, Phase 3 migration runs, or canonical decision action and
confidence. It must not initialize an exchange or broker client. `WATCH` and
`WATCH FOR EXIT` remain quarantined observations and are never mapped to a
canonical action.

## Gate sequence

Work proceeds in this order:

1. Capture Git, PostgreSQL, Alembic, inventory, canonical export, and runtime
   safety evidence.
2. Create a permission-`0600` custom-format PostgreSQL dump, restore it into a
   temporary database, and verify 15 tables, Alembic `0002_quarantine_lineage`,
   43,055 canonical rows, and 222 quarantine rows. Stop if any check fails.
3. Analyze immutable legacy sources and publish exact source counts and four
   ADRs. No schema change occurs before this gate is complete.
4. Add the Phase 3B schema and backfill behavior using test-first development.
5. Run a zero-write dry-run against the approved source inventory. Stop on
   unapproved estimation, generated snippets, free-text symbol inference,
   unreconciled assets, conflicts requiring policy changes, or canonical count
   drift.
6. Apply only with Phase 3B-specific approval variables, rerun to prove zero
   changes, then unset the variables and prove the guard is relocked.
7. Rerun dual-read, contract drift checks, isolated backend tests, candidate
   checks, safety tests, PostgreSQL smoke, explicit rollback to legacy mode,
   and final runtime evidence.

## Architecture

The approved approach is a hybrid of canonical nullable values and append-only
lineage tables. Values needed by the existing query contract remain directly
queryable, while source candidates, conflicts, and multi-source attribution
remain lossless and auditable.

### Decision values and evidence

`decisions` receives nullable `price_at_decision` and `report_snippet` values
plus the minimum provenance state required to interpret a null or populated
value. A `decision_field_lineage` table stores one immutable evidence record
per decision field, source hash, source record index, and normalization
version. It records source type/path/field, provenance quality, normalized
value fingerprint, migration run, and conflict or rejection reason.

The canonical value is updated only when the incoming evidence has higher
quality than the stored evidence. Quality precedence is:

`EXACT > DERIVED > LEGACY_ESTIMATED > UNKNOWN`

Equal-quality differing values are not overwritten. Both evidence records are
retained and an `EQUAL_QUALITY_CONFLICT` is recorded. Existing non-null values
are never blindly replaced.

### Cost-session attribution

`cost_session_assets` links a cost session to canonical assets using structured
legacy evidence only. Each link carries source lineage and a stable identity.
The parent cost session stores symbol provenance state so an evidenced empty
set can be distinguished from unknown attribution. Query output is a stable,
sorted, unique canonical symbol list assembled through the asset registry.

Filename-only guesses and unrestricted prose parsing are forbidden. Unknown
symbols produce evidence with `UNKNOWN_ASSET`; they do not create assets.

### Asset source lineage

`asset_source_lineage` is an append-only many-to-many table. A canonical asset
may have multiple source records and files. Its identity includes asset,
source type, source path, source hash, optional record index, normalization
version, and canonical fingerprint. Reprocessing identical evidence is a skip;
a changed source hash creates a new lineage row. Config-seeded assets point to
the asset-registry source hash/version.

### Backfill tracking and conflicts

Phase 3B uses new backfill tracking rather than rewriting existing Phase 3
migration runs. Each run records domain, status, seen/updated/unchanged/unknown/
conflicted counts, inventory hash, code commit, normalization version, start
and finish timestamps. Evidence records use the required reason codes:

- `SOURCE_FIELD_MISSING`
- `SOURCE_LINK_NOT_FOUND`
- `AMBIGUOUS_SOURCE_MATCH`
- `PRICE_TIMESTAMP_MISMATCH`
- `SNIPPET_SOURCE_MISSING`
- `SYMBOL_EVIDENCE_MISSING`
- `UNKNOWN_ASSET`
- `LOWER_QUALITY_SOURCE_IGNORED`
- `EQUAL_QUALITY_CONFLICT`

Domain writes and their lineage records are atomic per transaction chunk.
Resume continues from committed stable identities and never duplicates
evidence.

## Source policies

### Decision price

Precedence is a price explicitly stored in the same decision record, followed
by a report or payload explicitly linked by the decision and permitted by the
price ADR. A linked report price is `DERIVED` only when the ADR's deterministic
timestamp/link policy passes. `LEGACY_ESTIMATED` remains disabled unless the
source analysis produces a separately approved deterministic policy. No
nearest-neighbor lookup is allowed. Insufficient evidence produces
`NULL/UNKNOWN`.

### Report snippet

Precedence is an explicit legacy snippet field, then deterministic extraction
from an explicitly linked stored report/summary/rationale/document field as
approved by the snippet ADR. Text must already exist in a legacy source.
Truncation, if required, uses one fixed length and normalization version and is
audited. No LLM-generated or reconstructed prose is allowed.

### Cost symbols

Only structured session metadata, tool invocation arguments, structured report
references, asset fields, or deterministically linked decision/report IDs may
attribute symbols. Symbols are resolved through the existing asset registry,
deduplicated, and stably sorted. Missing evidence remains explicitly unknown;
an evidenced empty set remains distinct.

### Asset lineage

Asset evidence comes from exact structured occurrences in imported decisions,
reports, signals, cost sessions, and the asset registry. A source may attribute
many assets and an asset may have many sources. No single source is selected as
the canonical source of a multi-source asset.

## CLI and apply approval

The backfill CLI is dry-run by default and supports explicit `--apply`, one or
more of `--domain decision-price`, `--domain decision-snippet`, `--domain
cost-symbols`, and `--domain asset-lineage`, plus `--resume RUN_ID`.

Real apply requires all four scoped variables:

- `TRADING_PHASE3B_APPLY_APPROVED=true`
- `TRADING_PHASE3B_SOURCE_INVENTORY_HASH=<approved hash>`
- `TRADING_PHASE3B_ALEMBIC_REVISION=0003_contract_lineage_repair`
- `TRADING_PHASE3B_NORMALIZATION_VERSION=<approved version>`

These variables do not authorize the Phase 3 importer or trading execution.
They are unset after apply, and a rejected unapproved apply proves relocking.

## API contract

Existing public field shapes are preserved when possible. Internal lineage is
not exposed publicly. Nullable or unknown semantics are centralized between
legacy and PostgreSQL adapters so PostgreSQL is never forced to copy an unsafe
legacy derivation for byte equality. Any breaking public change requires a
separate versioned v2 ADR and is outside Phase 3B.

## Testing and acceptance

Tests cover source precedence, timestamp mismatch, ambiguity, deterministic
snippet extraction/truncation, structured symbol mapping, unknown assets,
many-to-many lineage, idempotency, lower/equal-quality conflicts, transaction
rollback, resume, atomic value/evidence writes, and run status accuracy.

Dry-run must report exact domain counts while writing nothing. Apply must leave
all pre-existing canonical table counts and canonical actions unchanged except
for approved nullable field updates and new lineage/backfill rows. A second
apply must produce zero changes. Dual-read must contain no `MIGRATION_BUG`,
`QUERY_ORDERING_BUG`, or `CONTRACT_BUG`; unverifiable values must be explicitly
`UNKNOWN` rather than fabricated.

The final decision is `GO FOR PHASE 4 — DURABLE JOB QUEUE AND RESEARCH
SCHEDULER` only when every stated Phase 3B exit criterion and runtime safety
check has fresh evidence. Otherwise it is `NO-GO — PHASE 3B CONTRACT OR
LINEAGE BLOCKERS REMAIN`. Phase 3B never proposes or enables live trading.

## Rollback

Schema rollback is restore-based. Before changes, the verified custom-format
dump is retained outside Git with permission `0600`. Application rollback uses
the documented legacy read mode. A failed or partially completed backfill is
rolled back transactionally or resumed by stable identity; existing Phase 3
runs are never deleted or rewritten.
