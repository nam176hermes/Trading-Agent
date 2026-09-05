from pathlib import Path
import runpy

import pytest

from services.job_worker.p3_native_runner import PINNED_ENGINE_VERSION, native_parity_command


def test_native_adapter_is_pinned_and_provider_free() -> None:
    assert PINNED_ENGINE_VERSION == "1.231.0"
    command = native_parity_command(Path("/opt/p3/python"),Path("/opt/p3/release"))
    assert command == (
        "/opt/p3/python","-I","-B","/opt/p3/release/scripts/run_p3_nautilus_parity.py"
    )


def test_native_entrypoint_execs_only_the_p1_owned_pinned_composition(monkeypatch) -> None:
    module = runpy.run_path("scripts/run_p3_nautilus_parity.py", run_name="p3_native_test")
    observed = {}

    def fake_execve(executable, argv, environment):
        observed.update(executable=executable, argv=tuple(argv), environment=environment)
        raise RuntimeError("stop")

    monkeypatch.setattr(module["os"], "execve", fake_execve)
    with pytest.raises(RuntimeError, match="stop"):
        module["main"]()
    assert observed == {
        "executable": "/engine/bin/nautilus-entry-guard",
        "argv": (
            "/engine/bin/nautilus-entry-guard", "/usr/bin/python3.12", "-I", "-S",
            "/engine/runtime_v1/main.py", "--profile", "p1-real-backtest",
            "/inputs/request.json", "/inputs/request.sha256",
        ),
        "environment": {},
    }
