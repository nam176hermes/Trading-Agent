from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from contextlib import nullcontext
import sys

import pytest


RUNTIME_PARENT = Path(__file__).parents[2] / "engines/nautilus"
sys.path.insert(0, str(RUNTIME_PARENT))

from runtime_v1 import main as runtime_main  # noqa: E402
from runtime_v1.errors import (  # noqa: E402
    ERROR_FAMILIES,
    RuntimeFailure,
    diagnostic_line,
    guarded,
)


def test_runtime_failure_keeps_internal_cause_but_renders_only_stable_family() -> None:
    cause = ValueError("/inputs/private/request.json: native object Secret(...)")

    with pytest.raises(RuntimeFailure) as raised:
        guarded("INPUT_INVALID", lambda: (_ for _ in ()).throw(cause))

    assert raised.value.__cause__ is cause
    assert str(raised.value) == "INPUT_INVALID"
    assert diagnostic_line(raised.value.family) == b"P1_RUNTIME:INPUT_INVALID\n"
    assert diagnostic_line(
        raised.value.family,
        engine_version="1.231.0",
        closure_digest="a" * 64,
    ) == (b"P1_RUNTIME:INPUT_INVALID:1.231.0:" + b"a" * 64 + b"\n")
    assert ERROR_FAMILIES == {
        "INPUT_INVALID",
        "PROFILE_UNSUPPORTED",
        "ENGINE_SETUP_FAILED",
        "ENGINE_EXECUTION_FAILED",
        "EVENT_PROJECTION_FAILED",
        "FINAL_STATE_MISMATCH",
        "OUTPUT_FAILED",
    }
    for family in ERROR_FAMILIES:
        line = diagnostic_line(family)
        assert line.isascii() and len(line) <= 128
        assert b"/" not in line and b"\\" not in line


def _configure_main(
    monkeypatch: pytest.MonkeyPatch, failing_stage: str | None
) -> list[str]:
    calls: list[str] = []
    lineage = {
        "profile_manifest_schema_version": 8,
        "runtime_family": "cython-v1",
        "engine_version": "1.231.0",
        "profile": "p1-real-backtest",
        "event_schema": "nautilus-p1-event-stream-v1",
        "closure_sha256": "a" * 64,
        "runtime_inventory_sha256": "b" * 64,
    }

    def stage(name: str, result: object):
        def call(*args: object, **kwargs: object) -> object:
            del args, kwargs
            calls.append(name)
            if failing_stage == name:
                raise ValueError("/private/path NativeSecret(...)")
            return result

        return call

    monkeypatch.setattr(
        runtime_main, "require_runtime_entry", stage("entry", None)
    )
    monkeypatch.setattr(runtime_main, "sealed_wheel_imports", nullcontext)
    monkeypatch.setattr(runtime_main, "package_version", stage("version", "1.231.0"))
    monkeypatch.setattr(
        runtime_main, "require_engine_version", stage("profile", None)
    )
    monkeypatch.setattr(runtime_main, "load_product_lineage", stage("lineage", lineage))
    inputs = SimpleNamespace(request=object())
    run = object()
    completion = object()
    stream = SimpleNamespace(jsonl=b'{"complete":true}\n')

    def project(*args: object, **kwargs: object) -> object:
        assert args == (inputs, run, completion)
        assert kwargs == {
            "closure_digest": "a" * 64,
            "upstream_commit": "27a8e54e7ac3c57d6cbf8891f0283dfbaee97317",
        }
        return stage("project", stream)()

    def write(observed: object) -> int:
        assert observed is stream
        calls.append("write")
        if failing_stage == "write":
            raise OSError("/private/output")
        sys.stdout.buffer.write(stream.jsonl)
        sys.stdout.buffer.flush()
        return len(stream.jsonl)

    components = (
        stage("inputs", inputs),
        stage("run", run),
        stage("final", completion),
        project,
        write,
    )
    monkeypatch.setattr(runtime_main, "_runtime_components", stage("imports", components))
    return calls


def test_main_writes_only_the_fully_prevalidated_stream(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    calls = _configure_main(monkeypatch, None)

    assert runtime_main.main() == 0

    captured = capfd.readouterr()
    assert captured.out == '{"complete":true}\n'
    assert captured.err == ""
    assert calls == [
        "entry",
        "version",
        "profile",
        "lineage",
        "imports",
        "inputs",
        "run",
        "final",
        "project",
        "write",
    ]


@pytest.mark.parametrize(
    ("stage", "family"),
    (
        ("entry", "ENGINE_SETUP_FAILED"),
        ("version", "ENGINE_SETUP_FAILED"),
        ("profile", "PROFILE_UNSUPPORTED"),
        ("lineage", "PROFILE_UNSUPPORTED"),
        ("imports", "ENGINE_SETUP_FAILED"),
        ("inputs", "INPUT_INVALID"),
        ("run", "ENGINE_EXECUTION_FAILED"),
        ("final", "FINAL_STATE_MISMATCH"),
        ("project", "EVENT_PROJECTION_FAILED"),
        ("write", "OUTPUT_FAILED"),
    ),
)
def test_main_classifies_failure_without_stdout_or_secret(
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
    stage: str,
    family: str,
) -> None:
    _configure_main(monkeypatch, stage)

    assert runtime_main.main() != 0

    captured = capfd.readouterr()
    assert captured.out == ""
    if stage in {"entry", "version", "profile", "lineage"}:
        assert captured.err == f"P1_RUNTIME:{family}\n"
    else:
        assert captured.err == f"P1_RUNTIME:{family}:1.231.0:{'a' * 64}\n"
    assert "private" not in captured.err and "NativeSecret" not in captured.err
