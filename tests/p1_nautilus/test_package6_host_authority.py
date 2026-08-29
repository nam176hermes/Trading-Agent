from __future__ import annotations

import ast
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.build_p1_package6_host_authority as builder


def test_builder_never_imports_test_helpers() -> None:
    source = Path(builder.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }

    assert not any(name == "tests" or name.startswith("tests.") for name in imported)


def test_offline_stage_build_argv_is_fixed_to_production_builder() -> None:
    arguments = Namespace(
        source_commit="1" * 40,
        prior_release_sha256="2" * 64,
        python_runtime_archive=Path("/authority/python.tar.zst"),
        uv=Path("/authority/uv"),
        wheelhouse=Path("/authority/wheelhouse.tar"),
    )
    clone = Path("/tmp/p1-host/source")

    argv = builder._offline_build_argv(arguments, clone, Path("/tmp/p1-host/stage"))

    assert argv == [
        "/bin/bash",
        "/tmp/p1-host/source/ops/release-v2/build-stage.sh",
        "--repo", "/tmp/p1-host/source",
        "--commit", "1" * 40,
        "--output", "/tmp/p1-host/stage",
        "--prior-release-sha256", "2" * 64,
        "--python-runtime-archive", "/authority/python.tar.zst",
        "--uv", "/authority/uv",
        "--wheelhouse", "/authority/wheelhouse.tar",
    ]


def test_p1_semantic_operation_is_exact_backtest_0013(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    digests = iter(("3" * 64, "4" * 64))
    monkeypatch.setattr(builder, "_digest_file", lambda _path: next(digests))

    document = builder._p1_semantic_document(
        Path("/tmp/source"),
        Path("/tmp/stage/application/.venv/bin/python3.11"),
        "1" * 40,
        "2" * 40,
    )

    operation = document["p1_operation"]
    assert isinstance(operation, dict)
    assert operation["job_type"] == "BACKTEST"
    assert operation["database_revision"] == "0013_engine_backtest_enqueue_authority"
    assert operation["operation_id"] == "p1-vertical-slice.execute-once"
    assert operation["execution_steps"] == [
        "AUTHENTICATED_JOB_API_ENQUEUE",
        "EXACTLY_ONE_WORKER_RUN_ONCE",
        "DURABLE_SUCCESS_AND_PARITY",
    ]
    assert "SNAPSHOT" not in str(document)
    assert "0011_engine_backtest_worker_authority" not in str(document)

    first = builder._p1_semantic_policy_digest(
        "1" * 40, Path("/tmp/semantic/active.json"), Path("/tmp/semantic/input"), "5" * 64
    )
    changed = builder._p1_semantic_policy_digest(
        "1" * 40, Path("/tmp/semantic/active.json"), Path("/tmp/semantic/input"), "6" * 64
    )
    assert first != changed


def test_prepare_static_rejects_dirty_source_before_any_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []
    monkeypatch.setattr(builder, "_source_identity", lambda _source: (_ for _ in ()).throw(builder.HostAuthorityError("source checkout is dirty")))
    monkeypatch.setattr(builder.subprocess, "run", lambda *args, **kwargs: calls.append(args))
    arguments = Namespace(source_root=tmp_path)

    with pytest.raises(builder.HostAuthorityError, match="dirty"):
        builder._prepare_static(arguments)

    assert calls == []


def test_prepare_static_rejects_postgres_approval_before_any_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path("/tmp/p1-host-authority-test")
    calls: list[object] = []
    monkeypatch.setattr(builder, "_source_identity", lambda _source: ("1" * 40, "2" * 40))
    monkeypatch.setattr(builder, "_private_root", lambda _path: root)
    monkeypatch.setattr(builder, "_require_digest", lambda *_args: None)
    monkeypatch.setattr(
        builder,
        "load_protected_approval_record",
        lambda _path: (_ for _ in ()).throw(ValueError("invalid approval")),
    )
    monkeypatch.setattr(builder.subprocess, "run", lambda *args, **kwargs: calls.append(args))
    arguments = Namespace(
        source_root=tmp_path,
        source_commit="1" * 40,
        source_tree="2" * 40,
        operator_identity="operator.example",
        reviewer_identity="reviewer.example",
        disposable_root=root,
        pgdata=Path("/tmp/phase4-postgres-p1-host-authority/data"),
        pg_port=18432,
        python_runtime_archive=tmp_path,
        python_runtime_archive_sha256="3" * 64,
        uv=tmp_path,
        uv_sha256="4" * 64,
        postgres_approval=tmp_path,
        postgres_approval_sha256="5" * 64,
    )

    with pytest.raises(ValueError, match="invalid approval"):
        builder._prepare_static(arguments)

    assert calls == []


def test_prepare_static_accepts_exact_planned_postgres_slot_before_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path("/tmp/p1-host-authority-test")
    pgdata = Path("/tmp/phase4-postgres-p1-vertical-slice/data")
    calls: list[object] = []
    monkeypatch.setattr(builder, "_source_identity", lambda _source: ("1" * 40, "2" * 40))
    monkeypatch.setattr(builder, "_private_root", lambda _path: root)
    monkeypatch.setattr(builder, "_require_digest", lambda *_args: None)
    monkeypatch.setattr(builder, "load_protected_approval_record", lambda _path: {})
    monkeypatch.setattr(builder, "_runtime_setting_names", lambda: frozenset())
    monkeypatch.setattr(
        builder,
        "validate_disposable_postgres_approval_record",
        lambda *_args, **_kwargs: calls.append("record"),
    )
    monkeypatch.setattr(
        builder,
        "validate_disposable_postgres_approval",
        lambda _record, context: calls.append(("context", context.pgdata)),
    )
    monkeypatch.setattr(
        builder,
        "validate_postgres_source_binding_files",
        lambda *_args: calls.append("bindings"),
    )

    def stop_at_build(*_args: object, **_kwargs: object) -> str:
        raise RuntimeError("later build seam reached")

    monkeypatch.setattr(builder, "verify_offline_wheelhouse", stop_at_build)
    monkeypatch.setattr(builder.subprocess, "run", lambda *args, **kwargs: calls.append(args))
    arguments = Namespace(
        source_root=tmp_path,
        source_commit="1" * 40,
        source_tree="2" * 40,
        operator_identity="operator.example",
        reviewer_identity="reviewer.example",
        disposable_root=root,
        pgdata=pgdata,
        pg_port=18432,
        python_runtime_archive=tmp_path,
        python_runtime_archive_sha256="3" * 64,
        uv=tmp_path,
        uv_sha256="4" * 64,
        postgres_approval=tmp_path,
        postgres_approval_sha256="5" * 64,
        prior_release_sha256="6" * 64,
        wheelhouse=tmp_path,
        wheelhouse_sha256="7" * 64,
    )

    with pytest.raises(RuntimeError, match="later build seam reached"):
        builder._prepare_static(arguments)

    assert calls == [
        "record",
        ("context", "/tmp/phase4-postgres-p1-vertical-slice/data"),
        "bindings",
    ]


@pytest.mark.parametrize(
    "pgdata",
    [
        Path("/tmp/p1-host-authority-test/disposable/pgdata"),
        Path("/tmp/phase4-postgres-p1-vertical-slice/pgdata"),
        Path("/tmp/phase4-postgres-p1-vertical-slice/nested/data"),
        Path("/tmp/not-phase4-postgres-p1-vertical-slice/data"),
        Path("relative/phase4-postgres-p1-vertical-slice/data"),
    ],
)
def test_planned_postgres_slot_rejects_noncanonical_paths(pgdata: Path) -> None:
    with pytest.raises(builder.HostAuthorityError, match="planned slot"):
        builder._planned_postgres_slot_root(pgdata)


def test_activate_refreshes_safety_then_injects_exact_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_paths = SimpleNamespace(safety_snapshot=Path("/tmp/safety-state.json"))
    runtime = SimpleNamespace(
        scope="PACKAGE6_STAGING_ONLY",
        application_python=Path(builder.sys.executable),
        installation_root=Path("/tmp/p1-host/stage"),
        runtime_paths=runtime_paths,
        source_commit="1" * 40,
        safety=SimpleNamespace(source_fingerprint="2" * 64),
    )
    initial = SimpleNamespace(runtime_authority=runtime)
    operation = object()
    calls: list[object] = []
    monkeypatch.setattr(builder.os, "environ", {
        "TRADING_PACKAGE6_STAGING_SCOPE": "PACKAGE6_STAGING_ONLY",
        "TRADING_PACKAGE6_STAGING_AUTHORITY_PATH": "/tmp/p1-host/authority/static.json",
        "TRADING_PACKAGE6_STAGING_ACTIVATION_PATH": "/tmp/p1-host/authority/activation.json",
        "TRADING_PACKAGE6_APPROVAL_SHA256": "3" * 64,
    })
    monkeypatch.setattr(builder, "attest_worker_runtime_authority", lambda: calls.append("attest") or initial)
    monkeypatch.setattr(
        builder,
        "_validate_execution_capability",
        lambda selected, arguments: calls.append(
            ("approve", selected, tuple(arguments))
        )
        or operation,
    )
    monkeypatch.setattr(builder, "_digest_file", lambda _path: "4" * 64)
    monkeypatch.setattr(builder, "_replace_activation", lambda selected, digest: calls.append(("activation", selected, digest)))
    class Exporter:
        def __init__(self, **_kwargs: object) -> None:
            calls.append("exporter")

        def export_once(self) -> None:
            calls.append("export")

    monkeypatch.setattr(builder, "SafetyStateExporter", Exporter)
    def consume(
        selected: object,
        authority: object,
        arguments: object,
        *,
        refresh_dynamic_evidence: object,
    ) -> int:
        calls.append(("consume", selected, authority, tuple(arguments)))
        assert callable(refresh_dynamic_evidence)
        refresh_dynamic_evidence()
        return 0

    monkeypatch.setattr(builder, "_consume_p1_operation", consume)

    assert builder._activate_and_exec(["--execute"]) == 0
    assert calls == [
        "attest",
        ("approve", runtime, ("--execute",)),
        ("consume", operation, initial, ("--execute",)),
        "exporter",
        "export",
        ("activation", runtime, "4" * 64),
    ]


def test_activate_rejects_ambient_loader_control_before_attestation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(builder.os, "environ", {"LD_PRELOAD": "/poison.so"})
    monkeypatch.setattr(builder, "attest_worker_runtime_authority", lambda: calls.append("attest"))

    with pytest.raises(builder.HostAuthorityError, match="allowlisted"):
        builder._activate_and_exec([])

    assert calls == []


@pytest.mark.parametrize("mutation", ("arguments", "static", "semantic"))
def test_p1_operation_capability_must_equal_performed_operation(
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    issued = object()
    operation = builder._ValidatedP1Operation(
        package6_capability=issued,  # type: ignore[arg-type]
        authority_pin=("static",),
        semantic_sha256="1" * 64,
        arguments=("--execute",),
    )
    authority = SimpleNamespace(
        authority_pin=("other",) if mutation == "static" else ("static",),
        semantic_evidence=SimpleNamespace(
            active_authority_sha256=(
                "2" * 64 if mutation == "semantic" else "1" * 64
            )
        ),
    )
    arguments = ("--dry-run",) if mutation == "arguments" else ("--execute",)
    calls: list[str] = []
    monkeypatch.setattr(
        builder, "is_issued_capability", lambda selected: selected is issued
    )
    builder._ISSUED_P1_OPERATIONS.add(operation)
    import scripts.run_p1_nautilus_vertical_slice as vertical
    monkeypatch.setattr(
        vertical,
        "main",
        lambda *_args, **_kwargs: calls.append("vertical") or 0,
    )

    with pytest.raises(builder.HostAuthorityError, match="capability changed"):
        builder._consume_p1_operation(
            operation,
            authority,  # type: ignore[arg-type]
            arguments,
            refresh_dynamic_evidence=lambda: None,
        )

    assert calls == []


def test_p1_operation_capability_requires_execute_before_authority_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reads: list[Path] = []
    monkeypatch.setattr(
        builder,
        "_read_canonical",
        lambda path, **_kwargs: reads.append(path),
    )

    with pytest.raises(builder.HostAuthorityError, match="not exact"):
        builder._validate_execution_capability(  # type: ignore[arg-type]
            SimpleNamespace(), ["--dry-run"]
        )

    assert reads == []


def test_p1_safety_refresher_must_bind_the_consumed_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issued = object()
    operation = builder._ValidatedP1Operation(
        package6_capability=issued,  # type: ignore[arg-type]
        authority_pin=("static",),
        semantic_sha256="1" * 64,
        arguments=("--execute",),
    )
    authority = SimpleNamespace(
        authority_pin=("static",),
        semantic_evidence=SimpleNamespace(active_authority_sha256="1" * 64),
    )
    refresher = SimpleNamespace(matches_operation=lambda **_kwargs: False)
    builder._ISSUED_P1_OPERATIONS.add(operation)
    monkeypatch.setattr(
        builder, "is_issued_capability", lambda selected: selected is issued
    )
    monkeypatch.setattr(
        builder,
        "_issue_p1_staging_safety_authority_refresher",
        lambda *_args, **_kwargs: refresher,
    )

    with pytest.raises(builder.HostAuthorityError, match="safety capability changed"):
        builder._consume_p1_operation(  # type: ignore[arg-type]
            operation,
            authority,
            ["--execute"],
            refresh_dynamic_evidence=lambda: None,
        )

    assert operation in builder._ISSUED_P1_OPERATIONS


def test_p1_operation_capability_is_consumed_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issued = object()
    operation = builder._ValidatedP1Operation(
        package6_capability=issued,  # type: ignore[arg-type]
        authority_pin=("static",),
        semantic_sha256="1" * 64,
        arguments=("--execute",),
    )
    authority = SimpleNamespace(
        authority_pin=("static",),
        semantic_evidence=SimpleNamespace(active_authority_sha256="1" * 64),
    )
    builder._ISSUED_P1_OPERATIONS.add(operation)
    monkeypatch.setattr(
        builder, "is_issued_capability", lambda selected: selected is issued
    )
    import scripts.run_p1_nautilus_vertical_slice as vertical
    received: dict[str, object] = {}
    monkeypatch.setattr(
        vertical,
        "main",
        lambda *_args, **kwargs: received.update(kwargs) or 0,
    )
    refresher = SimpleNamespace(
        refresh=lambda selected: selected,
        matches_operation=lambda **_kwargs: True,
    )
    monkeypatch.setattr(
        builder,
        "_issue_p1_staging_safety_authority_refresher",
        lambda *_args, **_kwargs: refresher,
    )

    assert builder._consume_p1_operation(  # type: ignore[arg-type]
        operation,
        authority,
        ["--execute"],
        refresh_dynamic_evidence=lambda: None,
    ) == 0
    assert received == {
        "safety_authority_refresher": refresher,
        "worker_authority": authority,
    }
    with pytest.raises(builder.HostAuthorityError, match="capability changed"):
        builder._consume_p1_operation(  # type: ignore[arg-type]
            operation,
            authority,
            ["--execute"],
            refresh_dynamic_evidence=lambda: None,
        )


def test_host_authority_is_not_selectable_by_vertical_cli() -> None:
    import scripts.run_p1_nautilus_vertical_slice as vertical

    help_text = vertical._parser().format_help()

    assert "host-authority" not in help_text
    assert "worker-authority" not in help_text
