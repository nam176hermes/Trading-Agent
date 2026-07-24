# Phase 3B Contract and Lineage Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repair decision price/snippet parity, cost-session symbol attribution,
and many-to-many asset source lineage with deterministic, idempotent,
provenance-preserving PostgreSQL backfills.

**Architecture:** Store contract-facing nullable values on canonical rows and
store immutable source evidence in normalized lineage tables. Analyze sources
before schema changes, default the CLI to dry-run, reject weaker/equal-conflict
overwrites, and require Phase 3B-specific approval variables for apply.

**Tech Stack:** Python 3.11, PostgreSQL 16, psycopg 3, SQLAlchemy/Alembic,
Pydantic 2, pytest, uv.

## Global Constraints

- Keep requested/effective mode `paper / paper`.
- Keep `LIVE_EXECUTION_ENABLED=false` and `LIVE_TRADING_APPROVED=false`.
- Keep kill switch `INACTIVE`, orders/trades `30 / 0`, port 3002, active PIDs,
  and Cloudflare unchanged.
- Do not initialize or probe exchanges/brokers, restore schedulers, or change
  models, strategies, prompts, command APIs, Redis, or Celery.
- Do not modify legacy sources or existing Phase 3 migration runs.
- Do not map `WATCH` or `WATCH FOR EXIT` to canonical actions.
- Missing evidence is `NULL/UNKNOWN`; no nearest-price, LLM snippet, filename
  symbol guess, unrestricted prose parser, or fabricated lineage is permitted.
- Preserve the untracked `docs/audits/` directory.

---

### Task 1: Baseline and Pre-change Checkpoint

**Files:**
- Create: `docs/implementation/phase-3b-prechange-checkpoint.md`
- Create outside Git: permission-`0600` PostgreSQL custom-format dump

**Interfaces:**
- Consumes: owner/migrator/reader settings under
  `~/.config/trading-agent/`, Phase 3 inventory helpers, runtime read-only
  status surfaces.
- Produces: verified dump path/hash and a stop/go checkpoint for Task 2.

- [ ] **Step 1: Verify baseline repository tests**

Run: `uv run pytest -q`

Expected: exit 0. If it fails, stop and report the exact pre-existing failure.

- [ ] **Step 2: Capture immutable pre-change facts**

Run read-only commands for `git status`, `git rev-parse HEAD`, Alembic current,
PostgreSQL table/count queries, source inventory hash, stable canonical export
hash, safety gates, modes, kill switch, orders/trades, service PIDs, listeners,
and Cloudflare PID. Redact credentials.

Expected canonical counts: 17 assets, 2,186 reports, 23,961 snapshots, 16,517
decisions, 344 signals, 9 capabilities, 1 cost summary, 20 cost sessions;
43,055 total and 222 quarantine rows.

- [ ] **Step 3: Create and secure backup**

Use the owner connection settings without printing the password:

```bash
umask 077
pg_dump --format=custom --file "$BACKUP_PATH" "$OWNER_DSN"
chmod 0600 "$BACKUP_PATH"
pg_restore --list "$BACKUP_PATH" >/dev/null
sha256sum "$BACKUP_PATH"
```

Expected: dump exists, mode `600`, archive list succeeds, and hash is recorded.
If the estimated dump exceeds 1 GB, stop and request approval before creation.

- [ ] **Step 4: Restore into a temporary database and verify**

Create a uniquely named temporary database, restore the dump, verify 15 public
tables, Alembic `0002_quarantine_lineage`, 43,055 canonical rows, and 222
quarantine rows, then drop only the temporary database.

Expected: every assertion passes. On any failure, keep the original DB
untouched, record the failure, and stop Phase 3B.

- [ ] **Step 5: Write checkpoint evidence**

Record timestamp, repo/commit/status, DB identity without password, counts,
hashes, safety invariants, backup path/hash/mode, restore verification, and
explicit GO/STOP decision in the checkpoint document.

### Task 2: Deterministic Source Analysis and ADRs

**Files:**
- Create: `tests/control_api/test_phase3b_source_analysis.py`
- Create: `apps/control_api/trading_control/phase3b_sources.py`
- Create: `scripts/analyze_phase3b_sources.py`
- Create: `docs/implementation/phase-3b-source-analysis.md`
- Create: `docs/adr/ADR-phase-3b-decision-price-lineage.md`
- Create: `docs/adr/ADR-phase-3b-report-snippet-source.md`
- Create: `docs/adr/ADR-phase-3b-cost-symbol-attribution.md`
- Create: `docs/adr/ADR-phase-3b-asset-source-lineage.md`

**Interfaces:**
- Produces: `analyze_sources(root: Path) -> Phase3BSourceAnalysis` and immutable
  `FieldEvidence` values containing entity ID, field/domain, value, quality,
  source type/path/hash/record index/field, normalization version, fingerprint,
  and optional reason code.

- [ ] **Step 1: Write failing source-extraction tests**

Cover direct decision price as `EXACT`, explicitly linked permitted report
price as `DERIVED`, mismatch/ambiguity as unknown, explicit/extracted snippet,
fixed deterministic truncation, missing snippet, structured cost symbols,
stable unique sorting, unknown assets, multiple source lineage rows, and no
asset creation.

- [ ] **Step 2: Run tests and verify RED**

Run: `uv run pytest -q tests/control_api/test_phase3b_source_analysis.py`

Expected: collection/import failure because `phase3b_sources` does not exist.

- [ ] **Step 3: Implement read-only evidence extraction**

Implement frozen dataclasses/enums and pure extraction functions. Use exact
structured JSON paths and explicit IDs only. Hash source bytes and normalized
values with existing `sha256_file`, `sha256_bytes`, and `record_key` helpers.
Do not write to PostgreSQL or legacy files.

- [ ] **Step 4: Verify GREEN and run real analysis**

Run the focused test, then:

```bash
uv run python scripts/analyze_phase3b_sources.py \
  --source-root /home/thenam176/.hermes/crypto-research \
  --format json
```

Expected: exit 0, exact totals for all four domains, no writes, and inventory
hash matching the checkpoint.

- [ ] **Step 5: Publish source analysis and four ADRs**

Each ADR states source precedence, exact/derived/unknown rules, forbidden
inference, stable identity, collision policy, contract impact, and restore-
based rollback. Record real source counts before creating revision `0003`.

- [ ] **Step 6: Commit the analysis gate**

Commit message: `docs: define phase 3b source evidence policies`

### Task 3: Alembic Contract-Lineage Schema

**Files:**
- Modify: `tests/control_api/test_alembic_schema.py`
- Create: `alembic/versions/0003_contract_lineage_repair.py`
- Create: `docs/implementation/phase-3b-schema.md`

**Interfaces:**
- Produces: nullable decision contract columns;
  `decision_field_lineage`, `cost_session_assets`, `asset_source_lineage`, and
  `phase3b_backfill_runs` tables with foreign keys/checks/unique identities.

- [ ] **Step 1: Write failing schema assertions**

Assert revision ID/down-revision, nullable decision fields, provenance checks,
foreign keys, append-only identity constraints, cost unknown-vs-empty state,
run counters/status, and downgrade raising restore-based rollback.

- [ ] **Step 2: Verify RED**

Run: `uv run pytest -q tests/control_api/test_alembic_schema.py`

Expected: failure because revision `0003_contract_lineage_repair` and its
columns/tables are absent.

- [ ] **Step 3: Implement revision `0003_contract_lineage_repair`**

Add nullable `price_at_decision`/`report_snippet` and minimal canonical
provenance columns to `decisions`; add `symbols_provenance_quality` and
`symbols_evidence_state` to `cost_sessions`; add the four normalized tables.
Use `NUMERIC(30,12)` for prices, SHA-256 strings of length 64, provenance check
constraints, and stable unique constraints. Do not modify revision 0001/0002.

- [ ] **Step 4: Verify GREEN in isolated test database**

Run schema tests and `uv run alembic upgrade head` against the test database,
then inspect `alembic current` and table metadata.

- [ ] **Step 5: Document and commit schema**

Commit message: `db: add contract lineage repair schema`

### Task 4: Backfill Planner and Conflict Semantics

**Files:**
- Create: `tests/control_api/test_phase3b_backfill.py`
- Create: `apps/control_api/trading_control/phase3b_backfill.py`

**Interfaces:**
- Produces: `build_backfill_plan(root, domains) -> BackfillPlan`, quality
  comparison, stable identities, reason-coded outcomes, and dry-run counts.

- [ ] **Step 1: Write failing planner tests**

Cover every E1-E4 case from the approved requirements, including direct price,
linked report rules, mismatch, ambiguity, explicit/preserved snippet,
deterministic extraction/truncation, structured cost symbols, duplicate sort,
unknown assets, multi-file lineage, rerun skip, changed hash, and no asset
creation.

- [ ] **Step 2: Verify RED**

Run: `uv run pytest -q tests/control_api/test_phase3b_backfill.py`

Expected: failure because backfill planner APIs are missing.

- [ ] **Step 3: Implement minimal pure planner**

Implement the four domains and precedence
`EXACT > DERIVED > LEGACY_ESTIMATED > UNKNOWN`. Lower quality emits
`LOWER_QUALITY_SOURCE_IGNORED`; equal-quality differing values emit
`EQUAL_QUALITY_CONFLICT`; neither overwrites. Identities include domain,
canonical ID, source hash/index, and normalization version.

- [ ] **Step 4: Verify GREEN**

Run focused source and backfill tests. Expected: all pass with no DB writes.

- [ ] **Step 5: Commit decision and snippet planning**

Commit message: `backfill: add decision price and snippet lineage`

- [ ] **Step 6: Commit cost and asset planning**

Commit messages:

```text
backfill: add cost symbol attribution
backfill: add canonical asset source lineage
```

### Task 5: Transactional Writer, Resume, and Apply Guard

**Files:**
- Create: `tests/control_api/test_phase3b_transactions.py`
- Create: `tests/control_api/test_phase3b_approval.py`
- Create: `apps/control_api/trading_control/phase3b_approval.py`
- Create: `apps/control_api/trading_control/phase3b_writer.py`
- Create: `apps/control_api/trading_control/phase3b_migrate.py`
- Modify: `pyproject.toml` only if an existing script registration convention
  requires it; do not add dependencies.

**Interfaces:**
- CLI: dry-run default, `--apply`, repeatable `--domain`, `--resume RUN_ID`.
- Apply requires the four exact Phase 3B variables and validates DB identity,
  revision, inventory, normalization version, safety gates, paper mode, kill
  switch, and absence of production credentials.

- [ ] **Step 1: Write failing approval and transaction tests**

Cover missing/mismatched approval, dry-run zero writes, apply success, same
apply zero changes, chunk rollback, resume, conflict, atomic value/lineage,
accurate completed/failed status, and isolation from Phase 3 approval.

- [ ] **Step 2: Verify RED**

Run both focused files. Expected: missing module/API failures.

- [ ] **Step 3: Implement approval validation and transactional writer**

Use psycopg transactions and parameterized SQL. Insert immutable evidence with
`ON CONFLICT DO NOTHING`; update canonical values only after quality/conflict
checks. Commit domain chunks atomically and persist run counters/status.

- [ ] **Step 4: Implement CLI**

Parse only approved domains, reject `--apply` plus invalid flags, produce stable
JSON counts, and keep dry-run read-only. `--resume` accepts only a matching
incomplete Phase 3B run and never resumes completed Phase 3 runs.

- [ ] **Step 5: Verify GREEN and broader migration tests**

Run focused tests plus all existing migration/side-effect tests. Expected: all
pass; no exchange import or GET write.

- [ ] **Step 6: Commit writer coverage**

Commit message: `test: add phase 3b idempotency and conflict coverage`

### Task 6: Repository Contract Repair

**Files:**
- Modify: `tests/control_api/test_decision_repository.py`
- Modify: `tests/control_api/test_postgres_repositories.py`
- Modify: `tests/control_api/test_dual_read.py`
- Modify: `apps/control_api/control_api/repositories/decisions.py`
- Modify: `apps/control_api/control_api/repositories/costs.py`
- Modify: shared normalization only if source analysis proves the legacy
  adapter currently performs an unsafe derivation.
- Create: `docs/implementation/phase-3b-contract-analysis.md`

**Interfaces:**
- PostgreSQL decision rows use stored nullable canonical values and deterministic
  shared semantics rather than signal-close fabrication.
- PostgreSQL costs return stable sorted symbols through `cost_session_assets`.

- [ ] **Step 1: Write failing repository and dual-read tests**

Assert exact stored decision values, explicit unknown behavior, deterministic
snippet parity, cost symbol parity, unchanged ordering/count/action filters,
and no GET writes.

- [ ] **Step 2: Verify RED**

Run repository and dual-read focused tests. Expected: failures on the four
known Phase 3B blockers.

- [ ] **Step 3: Implement minimal repository queries**

Select stored decision fields and aggregate normalized symbols ordered by
canonical symbol. Centralize only deterministic legacy extraction needed for
parity. Keep OpenAPI field shapes unchanged.

- [ ] **Step 4: Verify GREEN and contract generation**

Run focused tests and `uv run python scripts/generate_contracts.py --check`.
Expected: no public contract drift.

- [ ] **Step 5: Commit contract parity repair**

Commit message: `test: repair dual-read contract parity`

### Task 7: Real Dry-run and Guarded Apply

**Files:**
- Create: `docs/implementation/phase-3b-dry-run.md`
- Create: `docs/implementation/phase-3b-apply-results.md`

**Interfaces:**
- Consumes: approved checkpoint inventory and ADR policies.
- Produces: exact real dry-run/apply/idempotency evidence.

- [ ] **Step 1: Upgrade the real PostgreSQL schema**

Reconfirm checkpoint invariants and backup hash, then run Alembic upgrade to
`0003_contract_lineage_repair`. Verify canonical/quarantine counts unchanged.

- [ ] **Step 2: Run real dry-run**

Run all four domains without `--apply`; capture before/after table snapshots in
separate read-only transactions and prove zero writes. Record exact required
domain counts and stop on any review condition.

- [ ] **Step 3: Set scoped approval variables and apply**

Set only the four Phase 3B approval variables in the command environment. Run
all approved domains, capture result/run ID, and verify canonical action/count
invariants plus lineage/backfill counts.

- [ ] **Step 4: Prove idempotency**

Run the identical apply again. Expected: zero canonical updates and zero new
lineage/evidence rows; all evidence is unchanged/skipped.

- [ ] **Step 5: Relock guard**

Unset the four variables and run an apply attempt. Expected: explicit rejection
before writes. Verify original Phase 3 apply guard remains relocked too.

### Task 8: Acceptance, Runtime, and Rollback Evidence

**Files:**
- Create: `docs/implementation/phase-3b-dual-read-evidence.md`
- Create: `docs/implementation/phase-3b-test-evidence.md`
- Create: `docs/implementation/phase-3b-runtime-evidence.md`
- Create: `docs/implementation/phase-3b-rollback.md`
- Create: `docs/implementation/phase-3b-known-limitations.md`

**Interfaces:**
- Produces: final evidence bundle and GO/NO-GO conclusion.

- [ ] **Step 1: Run migration repository verification**

```bash
uv run pytest -q
uv run python scripts/generate_contracts.py --check
uv run alembic current
```

- [ ] **Step 2: Run candidate verification without restart**

Read its `AGENTS.md`, use its pinned Node version, and run `npm test`,
`npx tsc --noEmit`, `npm run lint`, and `npm run build`. Do not restart or
deploy the candidate.

- [ ] **Step 3: Run isolated backend and safety suites**

Run the documented 43/43 backend integration suite and Phase 1 safety suite
(expected 85 pass, 2 intended skips), recording exact current results. Verify
legacy hashes remain unchanged.

- [ ] **Step 4: Run PostgreSQL API smoke and explicit legacy rollback**

Start only isolated local test processes on non-active ports, run GET-only
smoke tests in PostgreSQL mode, stop them, then repeat/verify legacy mode.
Assert no GET writes and no exchange initialization.

- [ ] **Step 5: Capture final runtime evidence**

Recheck paper modes, false gates, inactive kill switch, orders/trades, PIDs,
port 3002, Cloudflare, scheduler state, approval relock, DB revision/counts, and
source hashes. Explain any external PID change; do not restart anything.

- [ ] **Step 6: Write rollback and known limitations**

Document restore command using the verified dump, legacy-mode rollback,
expected data loss boundary, and all unresolved `UNKNOWN`/conflict counts.

- [ ] **Step 7: Commit acceptance evidence**

Commit message: `docs: add phase 3b acceptance evidence`

- [ ] **Step 8: Apply exit criteria**

Issue GO only if every blocker is exact or explicitly justified unknown, all 17
assets have attributable lineage, the second apply is zero-change, contract
drift and all required checks pass, legacy/runtime safety is unchanged, and no
exchange/order call occurred. Otherwise issue the mandated NO-GO conclusion.
