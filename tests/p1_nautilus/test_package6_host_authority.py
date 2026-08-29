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
    refreshed = object()
    calls: list[object] = []
    monkeypatch.setattr(builder.os, "environ", {
        "TRADING_PACKAGE6_STAGING_SCOPE": "PACKAGE6_STAGING_ONLY",
        "TRADING_PACKAGE6_STAGING_AUTHORITY_PATH": "/tmp/p1-host/authority/static.json",
        "TRADING_PACKAGE6_STAGING_ACTIVATION_PATH": "/tmp/p1-host/authority/activation.json",
        "TRADING_PACKAGE6_APPROVAL_SHA256": "3" * 64,
    })
    monkeypatch.setattr(builder, "attest_worker_runtime_authority", lambda: calls.append("attest") or initial)
    monkeypatch.setattr(builder, "_digest_file", lambda _path: "4" * 64)
    monkeypatch.setattr(builder, "_replace_activation", lambda selected, digest: calls.append(("activation", selected, digest)))
    monkeypatch.setattr(builder, "refresh_staging_worker_runtime_authority", lambda selected: calls.append(("refresh", selected)) or refreshed)

    class Exporter:
        def __init__(self, **_kwargs: object) -> None:
            calls.append("exporter")

        def export_once(self) -> None:
            calls.append("export")

    monkeypatch.setattr(builder, "SafetyStateExporter", Exporter)
    import scripts.run_p1_nautilus_vertical_slice as vertical
    monkeypatch.setattr(vertical, "main", lambda argv, worker_authority=None: calls.append(("vertical", argv, worker_authority)) or 0)

    assert builder._activate_and_exec(["--execute"]) == 0
    assert calls == [
        "attest",
        "exporter",
        "export",
        ("activation", runtime, "4" * 64),
        ("refresh", initial),
        ("vertical", ["--execute"], refreshed),
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


def test_host_authority_is_not_selectable_by_vertical_cli() -> None:
    import scripts.run_p1_nautilus_vertical_slice as vertical

    help_text = vertical._parser().format_help()

    assert "host-authority" not in help_text
    assert "worker-authority" not in help_text
