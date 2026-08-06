# WS-04A Fixture and Catalog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Materialize validated provider-free candle fixtures into a local Parquet catalog whose manifest binds every data, provenance, continuity, and file digest.

**Architecture:** Keep the existing immutable `MarketSnapshot` as the sole normalized ingress. A new `packages.data_catalog` boundary creates canonical Parquet rows and a strict manifest, writes only to an explicit caller-owned directory, then re-reads and verifies the artifact before it is returned. The catalog is local/offline and carries no provider, credential, broker, engine, or runtime authority.

**Tech Stack:** Python 3.11, Pydantic 2, Decimal, PyArrow, pytest.

## Global Constraints

- Paper-only; no network, provider, exchange, broker, account, order, database, engine, or service activation.
- Use the root dependency graph only; add the approved `pyarrow` production dependency via `uv add` and never hand-edit `uv.lock`.
- Market input must be a validated `MarketSnapshot`; do not accept dictionaries or floats.
- Manifest, evidence, canonical rows, and Parquet bytes are SHA-256 bound; unknown/missing/tampered fields fail closed.
- Continuity issues are recorded, never repaired or synthesized; duplicate source rows are rejected.
- No catalog writes outside an explicit existing caller-owned destination directory; no path traversal or symlink destination.

---

### Task 1: Establish the strict catalog manifest contract

**Files:**
- Create: `packages/data_catalog/__init__.py`
- Create: `packages/data_catalog/manifests.py`
- Test: `tests/data_catalog/test_manifests.py`

**Interfaces:**
- Produces `MarketDatasetManifestV1`, `MarketDatasetContinuityV1`, and `CatalogManifestError`.
- `MarketDatasetManifestV1.from_snapshot(snapshot: MarketSnapshot, *, raw_evidence_sha256: str, importer_version: str, parquet_sha256: str, canonical_rows_sha256: str) -> MarketDatasetManifestV1` binds provider, instrument, timeframe, schema/normalization/importer versions, first/last event time, row count, content/raw evidence/Parquet/row digests, and exact gap/duplicate reports.

- [ ] **Step 1: Write failing manifest tests**

```python
def test_manifest_binds_snapshot_identity_and_continuity() -> None:
    manifest = MarketDatasetManifestV1.from_snapshot(
        snapshot(), raw_evidence_sha256="a" * 64,
        importer_version="fixture-catalog-v1",
        parquet_sha256="b" * 64,
        canonical_rows_sha256="c" * 64,
    )
    assert manifest.row_count == 2
    assert manifest.first_event_at == OPEN
    assert manifest.gap_report == ()
    assert manifest.content_digest == snapshot().digest
```

- [ ] **Step 2: Run the manifest test and confirm RED**

Run: `uv run pytest -q tests/data_catalog/test_manifests.py`

Expected: FAIL because `packages.data_catalog` does not exist.

- [ ] **Step 3: Implement strict frozen manifest models**

```python
class MarketDatasetManifestV1(_CatalogModel):
    schema_version: Literal["market-dataset-manifest-v1"]
    provider: str
    instrument: InstrumentId
    timeframe: MarketTimeframe
    first_event_at: datetime
    last_event_at: datetime
    row_count: StrictInt
    content_digest: str
    raw_evidence_sha256: str
    canonical_rows_sha256: str
    parquet_sha256: str
    gap_report: tuple[datetime, ...]
    duplicate_report: tuple[datetime, ...]
    importer_version: str
```

Validate all timestamps as UTC/aligned, count exactly equals snapshot candle count, report values are sorted/unique, and every digest is lowercase SHA-256.

- [ ] **Step 4: Run focused tests and commit**

Run: `uv run pytest -q tests/data_catalog/test_manifests.py tests/domain/test_market_data.py`

Commit: `git commit -m "feat(data): add hash-bound dataset manifest"`

### Task 2: Materialize and verify one local Parquet fixture catalog

**Files:**
- Create: `packages/data_catalog/parquet.py`
- Test: `tests/data_catalog/test_parquet_catalog.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock` (only via `uv add`)

**Interfaces:**
- Produces `materialize_fixture_catalog(snapshot: MarketSnapshot, raw_evidence: bytes, *, destination: Path, importer_version: str) -> MaterializedMarketDatasetV1`.
- `MaterializedMarketDatasetV1` contains `manifest`, `parquet_path`, and `manifest_path`.
- Reads require `verify_materialized_catalog(materialized: MaterializedMarketDatasetV1) -> MarketSnapshot`.

- [ ] **Step 1: Add failing materialization tests**

```python
def test_materialized_fixture_catalog_round_trips_and_is_hash_bound(tmp_path: Path) -> None:
    artifact = materialize_fixture_catalog(snapshot(), evidence(), destination=tmp_path, importer_version="fixture-catalog-v1")
    assert verify_materialized_catalog(artifact) == snapshot()

def test_materialization_rejects_tampered_parquet(tmp_path: Path) -> None:
    artifact = materialize_fixture_catalog(snapshot(), evidence(), destination=tmp_path, importer_version="fixture-catalog-v1")
    artifact.parquet_path.write_bytes(artifact.parquet_path.read_bytes() + b"tamper")
    with pytest.raises(CatalogMaterializationError):
        verify_materialized_catalog(artifact)
```

Also cover duplicate raw rows, gap recording without synthesis, invalid evidence digest, unsafe destination, symlink destination, malformed Parquet schema, UTC preservation, Decimal preservation, and deterministic row ordering.

- [ ] **Step 2: Run tests and confirm RED**

Run: `uv run pytest -q tests/data_catalog/test_parquet_catalog.py`

Expected: FAIL because the materializer does not exist.

- [ ] **Step 3: Add the approved Parquet dependency**

Run: `uv add pyarrow`

Verify: `uv run python -c "import pyarrow; print(pyarrow.__version__)"`

- [ ] **Step 4: Implement a canonical Parquet schema and verifier**

Use only six fixed columns: `open_time` UTC timestamp, `open`, `high`, `low`, `close`, and `volume` as Decimal-compatible canonical strings. Sort rows by `open_time`; serialize manifest as canonical sorted JSON; atomically create new regular files below the validated destination. Re-read Parquet, reconstruct `MarketSnapshot`, and compare all manifest-bound digests before success.

- [ ] **Step 5: Run focused tests and commit**

Run: `uv run pytest -q tests/data_catalog tests/domain/test_market_data.py`

Commit: `git commit -m "feat(data): materialize verified fixture parquet catalog"`

### Task 3: Preserve Phase-4 safety and contract boundaries

**Files:**
- Modify: `tests/data_catalog/test_parquet_catalog.py`
- Modify: `packages/data_catalog/__init__.py`

**Interfaces:**
- Keeps catalog materialization provider-free and isolated from Job API/PostgreSQL/Nautilus runtime imports.

- [ ] **Step 1: Write the boundary test**

```python
def test_data_catalog_source_has_no_runtime_or_provider_imports() -> None:
    assert forbidden_imports(Path("packages/data_catalog")) == []
```

The forbidden set includes `nautilus_trader`, `psycopg`, `sqlalchemy`, `requests`, `httpx`, `socket`, `urllib`, `subprocess`, and `services.market_data`.

- [ ] **Step 2: Run it to confirm RED, implement the AST scan, then rerun GREEN**

Run: `uv run pytest -q tests/data_catalog/test_parquet_catalog.py -k boundary`

- [ ] **Step 3: Run packet gates and commit evidence**

Run: `make check-contracts && make audit && git diff --check`

Commit: `git commit -m "test(data): guard fixture catalog authority boundary"`

## Packet completion gate

Run focused catalog/domain tests, `make check-contracts`, `make audit`, and `git diff --check`. Then request independent adversarial Codex review, repair findings, run canonical `TMPDIR=/tmp TEMP=/tmp TMP=/tmp make ci`, request independent final gate, and only then fast-forward local `main` to the accepted candidate. No remote push or runtime activation.
