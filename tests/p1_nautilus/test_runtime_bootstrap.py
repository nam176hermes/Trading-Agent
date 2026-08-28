from __future__ import annotations

import json
import stat
import sys
from inspect import signature
from pathlib import Path

import pytest


RUNTIME_PARENT = Path(__file__).parents[2] / "engines/nautilus"
sys.path.insert(0, str(RUNTIME_PARENT))

from runtime_v1.bootstrap import (  # noqa: E402
    EntryFacts,
    RuntimeBootstrapError,
    require_engine_version,
    load_product_lineage,
    require_product_lineage,
    validate_entry,
)
from runtime_v1.profile import P1_REAL_BACKTEST_PROFILE  # noqa: E402


MAIN = "/engine/runtime_v1/main.py"
REQUEST = "/inputs/request.json"
SIDECAR = "/inputs/request.sha256"
COMMAND = (
    "/usr/bin/python3.12",
    "-I",
    "-S",
    MAIN,
    "--profile",
    P1_REAL_BACKTEST_PROFILE,
    REQUEST,
    SIDECAR,
)


def _facts() -> EntryFacts:
    return EntryFacts(
        module_name="__main__",
        module_spec=None,
        module_file=MAIN,
        implementation_name="cpython",
        version=(3, 12),
        isolated=1,
        no_site=1,
        ignore_environment=1,
        no_user_site=1,
        safe_path=True,
        orig_argv=COMMAND,
        argv=COMMAND[3:],
        kernel_argv=tuple(value.encode() for value in COMMAND),
        environment=(("LC_CTYPE", "C.UTF-8"),),
        cwd="/",
        request_mode=stat.S_IFREG | 0o400,
        sidecar_mode=stat.S_IFREG | 0o400,
        sidecar_bytes=b"a" * 64 + b"\n",
    )


def test_exact_isolated_entry_and_lineage_pass() -> None:
    assert validate_entry(_facts()) is None
    assert require_engine_version("1.231.0") is None
    lineage = {
        "profile_manifest_schema_version": 8,
        "runtime_family": "cython-v1",
        "engine_version": "1.231.0",
        "profile": P1_REAL_BACKTEST_PROFILE,
        "event_schema": "nautilus-p1-event-stream-v1",
        "closure_sha256": "a" * 64,
        "runtime_inventory_sha256": "b" * 64,
    }
    assert tuple(signature(require_product_lineage).parameters) == ("observed",)
    assert require_product_lineage(lineage) is None


@pytest.mark.parametrize(
    "updates",
    (
        {"module_name": "runtime_v1.main"},
        {"module_spec": object()},
        {"implementation_name": "pypy"},
        {"version": (3, 11)},
        {"isolated": 0},
        {"no_site": 0},
        {"ignore_environment": 0},
        {"no_user_site": 0},
        {"safe_path": False},
        {"module_file": "/tmp/main.py"},
        {"orig_argv": COMMAND + ("--extra",)},
        {"argv": COMMAND[3:] + ("--extra",)},
        {"kernel_argv": tuple(value.encode() for value in COMMAND[:-1])},
        {"cwd": "/tmp"},
        {"environment": ()},
        {"environment": (("PYTHONPATH", "/tmp"),)},
        {"request_mode": stat.S_IFIFO | 0o400},
        {"sidecar_mode": stat.S_IFDIR | 0o500},
        {"sidecar_bytes": b"A" * 64 + b"\n"},
        {"sidecar_bytes": b"a" * 63 + b"\n"},
    ),
)
def test_entry_rejects_ambient_or_nonregular_authority(
    updates: dict[str, object]
) -> None:
    facts = _facts()
    changed = EntryFacts(
        **{**facts.__dict__, **updates},
    )
    with pytest.raises(RuntimeBootstrapError):
        validate_entry(changed)


def test_version_and_lineage_are_exact_and_closed() -> None:
    with pytest.raises(RuntimeBootstrapError, match="version"):
        require_engine_version("1.227.0")
    expected = {
        "profile_manifest_schema_version": 8,
        "runtime_family": "cython-v1",
        "engine_version": "1.231.0",
        "profile": P1_REAL_BACKTEST_PROFILE,
        "event_schema": "nautilus-p1-event-stream-v1",
        "closure_sha256": "a" * 64,
        "runtime_inventory_sha256": "b" * 64,
    }
    for mutation in (
        {**expected, "profile_manifest_schema_version": 7},
        {**expected, "closure_sha256": "A" * 64},
        {**expected, "runtime_inventory_sha256": "b" * 63},
        {**expected, "unknown": True},
    ):
        with pytest.raises(RuntimeBootstrapError, match="lineage"):
            require_product_lineage(mutation)


def test_product_lineage_loader_is_fixed_and_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lineage = {
        "profile_manifest_schema_version": 8,
        "runtime_family": "cython-v1",
        "engine_version": "1.231.0",
        "profile": P1_REAL_BACKTEST_PROFILE,
        "event_schema": "nautilus-p1-event-stream-v1",
        "closure_sha256": "a" * 64,
        "runtime_inventory_sha256": "b" * 64,
    }
    raw = (
        json.dumps(lineage, separators=(",", ":"), sort_keys=True) + "\n"
    ).encode()
    monkeypatch.setattr(
        "runtime_v1.bootstrap._read_regular",
        lambda path, maximum: raw,
    )
    assert load_product_lineage() == lineage
    monkeypatch.setattr(
        "runtime_v1.bootstrap._read_regular",
        lambda path, maximum: b'{}\n',
    )
    with pytest.raises(RuntimeBootstrapError, match="lineage"):
        load_product_lineage()
