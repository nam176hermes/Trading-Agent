from __future__ import annotations

import ast
import hashlib
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow.parquet as pq
import pytest

from packages.data_catalog import (
    CatalogWorkspaceV1,
    CatalogMaterializationError,
    materialize_fixture_catalog,
    verify_materialized_catalog,
)
from packages.data_catalog import parquet as catalog_parquet
from packages.domain import (
    InstrumentId,
    MarketCandle,
    MarketDataProvenance,
    MarketSnapshot,
    MarketTimeframe,
    ProductType,
)


OPEN = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
RAW_EVIDENCE = b'{"fixture":"market-catalog-v1"}'


@pytest.fixture(autouse=True)
def private_catalog_directory(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)


def workspace(tmp_path: Path) -> CatalogWorkspaceV1:
    return CatalogWorkspaceV1.create(tmp_path)


def catalog_path(workspace: CatalogWorkspaceV1, name: str) -> Path:
    """White-box helper: production callers receive no catalog path."""

    return catalog_parquet._workspace_state(workspace).path / name


def snapshot() -> MarketSnapshot:
    instrument = InstrumentId("BTC-USD", ProductType.CRYPTO_SPOT, "ALPACA")
    return MarketSnapshot(
        instrument=instrument,
        timeframe=MarketTimeframe.ONE_MINUTE,
        candles=(
            MarketCandle(
                instrument=instrument,
                timeframe=MarketTimeframe.ONE_MINUTE,
                open_time=OPEN,
                open=Decimal("100.00"),
                high=Decimal("102.00"),
                low=Decimal("99.00"),
                close=Decimal("101.00"),
                volume=Decimal("12.50"),
            ),
            MarketCandle(
                instrument=instrument,
                timeframe=MarketTimeframe.ONE_MINUTE,
                open_time=datetime(2026, 8, 6, 12, 1, tzinfo=UTC),
                open=Decimal("101.00"),
                high=Decimal("103.00"),
                low=Decimal("100.00"),
                close=Decimal("102.00"),
                volume=Decimal("8.50"),
            ),
        ),
        provenance=MarketDataProvenance(
            provider="deterministic-fixture-v1",
            observed_at=datetime(2026, 8, 6, 12, 2, tzinfo=UTC),
            fetched_at=datetime(2026, 8, 6, 12, 2, tzinfo=UTC),
            raw_evidence_sha256=hashlib.sha256(RAW_EVIDENCE).hexdigest(),
            schema_version="market-data-v1",
            normalization_version="market-normalization-v1",
        ),
        known_at=datetime(2026, 8, 6, 12, 2, tzinfo=UTC),
        schema_version="market-snapshot-v1",
        normalization_version="market-normalization-v1",
    )


def test_materialized_fixture_catalog_round_trips_and_is_hash_bound(tmp_path: Path) -> None:
    source = snapshot()

    artifact = materialize_fixture_catalog(
        source,
        RAW_EVIDENCE,
        workspace=workspace(tmp_path),
        importer_version="fixture-catalog-v1",
    )

    assert catalog_path(artifact.workspace, artifact.parquet_name).is_file()
    assert catalog_path(artifact.workspace, artifact.manifest_name).is_file()
    assert verify_materialized_catalog(artifact) == source
    assert artifact.manifest.content_digest == source.digest


def test_catalog_verifier_rejects_tampered_parquet(tmp_path: Path) -> None:
    artifact = materialize_fixture_catalog(
        snapshot(),
        RAW_EVIDENCE,
        workspace=workspace(tmp_path),
        importer_version="fixture-catalog-v1",
    )
    parquet_path = catalog_path(artifact.workspace, artifact.parquet_name)
    parquet_path.write_bytes(parquet_path.read_bytes() + b"tamper")

    with pytest.raises(CatalogMaterializationError, match="parquet digest"):
        verify_materialized_catalog(artifact)


def test_catalog_round_trip_preserves_source_schema_versions(tmp_path: Path) -> None:
    source = snapshot().model_copy(
        update={
            "provenance": snapshot().provenance.model_copy(
                update={"schema_version": "fixture-source-v7"}
            ),
            "schema_version": "fixture-snapshot-v5",
        }
    )

    artifact = materialize_fixture_catalog(
        source,
        RAW_EVIDENCE,
        workspace=workspace(tmp_path),
        importer_version="fixture-catalog-v1",
    )

    assert verify_materialized_catalog(artifact) == source


def test_catalog_uses_a_utc_timestamp_parquet_column(tmp_path: Path) -> None:
    artifact = materialize_fixture_catalog(
        snapshot(), RAW_EVIDENCE, workspace=workspace(tmp_path), importer_version="fixture-catalog-v1"
    )

    assert str(pq.read_schema(catalog_path(artifact.workspace, artifact.parquet_name)).field("open_time").type) == "timestamp[us, tz=UTC]"


def test_catalog_accepts_zero_offset_zoneinfo_utc_snapshot(tmp_path: Path) -> None:
    source = snapshot().model_copy(
        update={
            "candles": tuple(
                candle.model_copy(update={"open_time": candle.open_time.replace(tzinfo=ZoneInfo("Etc/UTC"))})
                for candle in snapshot().candles
            ),
            "provenance": snapshot().provenance.model_copy(
                update={
                    "observed_at": snapshot().provenance.observed_at.replace(tzinfo=ZoneInfo("Etc/UTC")),
                    "fetched_at": snapshot().provenance.fetched_at.replace(tzinfo=ZoneInfo("Etc/UTC")),
                }
            ),
            "known_at": snapshot().known_at.replace(tzinfo=ZoneInfo("Etc/UTC")),
        }
    )

    artifact = materialize_fixture_catalog(
        source, RAW_EVIDENCE, workspace=workspace(tmp_path), importer_version="fixture-catalog-v1"
    )

    assert verify_materialized_catalog(artifact) == source


def test_catalog_rejects_destination_with_an_intermediate_symlink(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    via = tmp_path / "via"
    via.symlink_to(outside, target_is_directory=True)
    requested = via / "nested"
    (outside / "nested").mkdir()

    with pytest.raises(CatalogMaterializationError, match="symlink"):
        materialize_fixture_catalog(
            snapshot(), RAW_EVIDENCE, workspace=CatalogWorkspaceV1.create(requested), importer_version="fixture-catalog-v1"
        )

    assert list(outside.joinpath("nested").iterdir()) == []


def test_catalog_verifier_rejects_oversized_artifact_before_parquet_loading(tmp_path: Path) -> None:
    artifact = materialize_fixture_catalog(
        snapshot(), RAW_EVIDENCE, workspace=workspace(tmp_path), importer_version="fixture-catalog-v1"
    )
    catalog_path(artifact.workspace, artifact.parquet_name).write_bytes(b"x" * (8 * 1024 * 1024 + 1))

    with pytest.raises(CatalogMaterializationError, match="bounded regular-file policy"):
        verify_materialized_catalog(artifact)


def test_catalog_cleanup_never_unlinks_an_inode_replaced_by_another_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = snapshot()
    parquet_name = f"market-{source.digest}.parquet"
    injected = b"unrelated-writer-artifact"
    catalog_workspace = workspace(tmp_path)

    def replace_then_fail(directory_fd: int, _name: str, _manifest: object) -> None:
        import os

        os.unlink(parquet_name, dir_fd=directory_fd)
        fd = os.open(parquet_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory_fd)
        try:
            os.write(fd, injected)
        finally:
            os.close(fd)
        raise RuntimeError("force cleanup")

    monkeypatch.setattr(catalog_parquet, "_write_new_manifest", replace_then_fail)

    with pytest.raises(RuntimeError, match="force cleanup"):
        materialize_fixture_catalog(
            source, RAW_EVIDENCE, workspace=catalog_workspace, importer_version="fixture-catalog-v1"
        )

    assert catalog_path(catalog_workspace, parquet_name).read_bytes() == injected


def test_catalog_workspace_owns_a_private_child_not_the_caller_parent(tmp_path: Path) -> None:
    catalog_workspace = workspace(tmp_path)
    private_path = catalog_path(catalog_workspace, ".")

    assert private_path.parent == tmp_path
    assert private_path != tmp_path
    assert private_path.is_dir()
    assert private_path.is_symlink() is False


def test_catalog_workspace_is_an_opaque_unforgeable_public_capability(tmp_path: Path) -> None:
    issued = workspace(tmp_path)

    assert not hasattr(issued, "path")
    assert not hasattr(issued, "_token")
    assert not hasattr(issued, "duplicate_directory_fd")
    with pytest.raises(TypeError, match="created only by CatalogWorkspaceV1.create"):
        CatalogWorkspaceV1()  # type: ignore[call-arg]


def test_catalog_workspace_close_retires_the_capability(tmp_path: Path) -> None:
    catalog_workspace = workspace(tmp_path)
    artifact = materialize_fixture_catalog(
        snapshot(), RAW_EVIDENCE, workspace=catalog_workspace, importer_version="fixture-catalog-v1"
    )

    catalog_workspace.close()
    catalog_workspace.close()

    with pytest.raises(CatalogMaterializationError, match="not registered"):
        verify_materialized_catalog(artifact)


def test_catalog_artifact_exposes_only_capability_and_artifact_names(tmp_path: Path) -> None:
    artifact = materialize_fixture_catalog(
        snapshot(), RAW_EVIDENCE, workspace=workspace(tmp_path), importer_version="fixture-catalog-v1"
    )

    assert set(artifact.__dataclass_fields__) == {"workspace", "manifest", "parquet_name", "manifest_name"}


def test_data_catalog_source_has_no_runtime_or_provider_imports() -> None:
    root = Path(__file__).resolve().parents[2] / "packages" / "data_catalog"
    forbidden = {
        "httpx",
        "nautilus_trader",
        "psycopg",
        "requests",
        "services.market_data",
        "socket",
        "sqlalchemy",
        "subprocess",
        "urllib",
    }
    imports: set[str] = set()

    for source in root.rglob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)

    assert not any(
        name == blocked or name.startswith(f"{blocked}.")
        for name in imports
        for blocked in forbidden
    )
