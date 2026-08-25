"""Explicit host-authority tests, addressed only by the required-runtime runner."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import runpy
import stat
import subprocess
import sys
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[3]
_CANDIDATE = runpy.run_path(
    str(ROOT / "tests/nautilus_upgrade/test_v1231_candidate_closure.py")
)
_TOOLCHAIN = runpy.run_path(
    str(ROOT / "tests/nautilus_upgrade/test_v1231_toolchain_policy.py")
)
builder = _CANDIDATE["builder"]
ENGINE_POLICY = _CANDIDATE["ENGINE_POLICY"]
_logical_stage = _CANDIDATE["_logical_stage"]
toolchain = _TOOLCHAIN["toolchain"]
roots_path = _TOOLCHAIN["roots_path"]
_build_environment = _TOOLCHAIN["_build_environment"]
CACHE_ENV = _TOOLCHAIN["CACHE_ENV"]
INPUT_POLICY = _TOOLCHAIN["INPUT_POLICY"]
GENERATOR = _TOOLCHAIN["GENERATOR"]
MANIFEST = _TOOLCHAIN["MANIFEST"]
pytestmark = pytest.mark.host_coupled


@pytest.fixture
def verified_source_fd(tmp_path: Path) -> int:
    source = tmp_path / "verified-source"
    source.mkdir()
    descriptor = os.open(source, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        yield descriptor
    finally:
        os.close(descriptor)


def test_candidate_bwrap_has_only_admitted_runtime_surface(
    tmp_path: Path,
) -> None:
    physical_stage = tmp_path / "physical-stage"
    source = physical_stage / "source"
    source.mkdir(parents=True)
    source.chmod(0o700)
    physical_stage.chmod(0o700)

    identity = builder._candidate_sandbox_run(
        physical_stage=physical_stage,
        logical_stage=_logical_stage(),
        action="policy-probe",
        timeout=30,
    )

    assert set(identity) == {
        "P1_U04_SOURCE_ST_DEV",
        "P1_U04_SOURCE_ST_INO",
    }


def test_candidate_bwrap_uses_sealed_linker_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    physical_stage = tmp_path / "physical-stage"
    source = physical_stage / "source"
    scratch = physical_stage / "tmp"
    source.mkdir(parents=True)
    scratch.mkdir()
    (source / "probe.c").write_text("int main(void) { return 0; }\n", encoding="ascii")
    source.chmod(0o700)
    scratch.chmod(0o700)
    physical_stage.chmod(0o700)
    logical_stage = _logical_stage()
    engine = builder._candidate_json(ENGINE_POLICY)
    inputs = builder._candidate_json(builder._CANDIDATE_TOOLCHAIN_INPUTS)
    router = tmp_path / "router"
    builder._materialize_candidate_router(inputs, router)
    sealed_router = builder._candidate_roots(engine)["candidate_toolchain_root"]
    real_run = subprocess.run

    def run_with_scratch_router(command, **kwargs):
        invocation = list(command)
        for index in range(len(invocation) - 2):
            if invocation[index : index + 3] == [
                "--ro-bind",
                str(sealed_router),
                str(sealed_router),
            ]:
                invocation[index + 1] = str(router)
                break
        return real_run(invocation, **kwargs)

    def clang_host_link(action, stage, _inputs, _environment=None):
        assert action == "policy-probe"
        assert stage == logical_stage
        return (
            "clang",
            str(stage / "source/probe.c"),
            "-o",
            str(stage / "tmp/probe"),
        )

    monkeypatch.setattr(builder.subprocess, "run", run_with_scratch_router)
    monkeypatch.setattr(builder, "_candidate_command", clang_host_link)
    try:
        builder._candidate_sandbox_run(
            physical_stage=physical_stage,
            logical_stage=logical_stage,
            action="policy-probe",
            timeout=30,
        )
    finally:
        (router / "bin").chmod(0o700)
        router.chmod(0o700)

    assert (scratch / "probe").is_file()


def test_bwrap_routes_injected_pwd_to_exact_cargo() -> None:
    sandbox = Path("/usr/bin/bwrap")
    try:
        sandbox_st = sandbox.lstat()
        sandbox_raw = sandbox.read_bytes()
        version = subprocess.run(
            [str(sandbox), "--version"],
            env={},
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        pytest.fail(f"reviewed Bubblewrap authority is unavailable: {exc}")
    assert {
        "gid": sandbox_st.st_gid,
        "mode": f"{stat.S_IMODE(sandbox_st.st_mode):04o}",
        "sha256": hashlib.sha256(sandbox_raw).hexdigest(),
        "size": sandbox_st.st_size,
        "type": "file" if stat.S_ISREG(sandbox_st.st_mode) else "other",
        "uid": sandbox_st.st_uid,
        "version": version.stdout.strip() if version.returncode == 0 else "",
    } == {
        "gid": 0,
        "mode": "0755",
        "sha256": "52231e1caf55bcbc667b269f49c63599a6f7db4767ae6a039580d0ff853db712",
        "size": 72160,
        "type": "file",
        "uid": 0,
        "version": "bubblewrap 0.9.0",
    }

    engine = toolchain.load_json(ENGINE_POLICY)
    policy = engine["native_build_environment"]
    stage = Path(roots_path(engine, "candidate_build_root")) / (
        "stage-ffeeddccbbaa0099"
    )
    source = stage / "source"
    wrapper = next(
        entry
        for entry in engine["command_router"]["entries"]
        if entry["name"] == "cargo"
    )
    router_cargo = Path(roots_path(engine, "candidate_toolchain_root")) / (
        "bin/cargo"
    )
    rust_cargo = Path(wrapper["exec_target"]["path"])

    with tempfile.TemporaryDirectory(prefix="p1-u03-bwrap-", dir="/tmp") as raw:
        fixture = Path(raw)

        def host_path(sandbox_path: Path) -> Path:
            return fixture / sandbox_path.relative_to("/")

        host_stage = host_path(stage)
        host_router_cargo = host_path(router_cargo)
        host_rust_cargo = host_path(rust_cargo)
        authorized_stage = fixture / "authorized-stage"
        authorized_source = authorized_stage / "source"
        host_stage.mkdir(parents=True)
        authorized_source.mkdir(parents=True)
        host_router_cargo.parent.mkdir(parents=True)
        host_rust_cargo.parent.mkdir(parents=True)
        source_fd = os.open(
            authorized_source, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW
        )
        try:
            contract = toolchain._verify_build_environment(
                policy,
                {},
                stage,
                verified_source_fd=source_fd,
                mount_destinations=[stage],
            )
        finally:
            os.close(source_fd)
        host_router_cargo.write_text(wrapper["contents"], encoding="ascii")
        host_router_cargo.chmod(0o500)
        fake_cargo = (
            "#!/usr/bin/python3.12 -IS\n"
            "import os\n"
            "import sys\n"
            f"CARGO = {str(rust_cargo)!r}\n"
            f"SOURCE = {str(source)!r}\n"
            "if os.getcwd() != SOURCE or os.environ.get('PWD') != SOURCE:\n"
            "    raise SystemExit(90)\n"
            "if sys.argv != [CARGO, 'build', '--locked', '--offline', '--release']:\n"
            "    raise SystemExit(91)\n"
            "source = os.stat('.')\n"
            "if (str(source.st_dev), str(source.st_ino)) != (\n"
            "    os.environ['P1_U04_SOURCE_ST_DEV'],\n"
            "    os.environ['P1_U04_SOURCE_ST_INO'],\n"
            "):\n"
            "    raise SystemExit(92)\n"
        )
        host_rust_cargo.write_text(fake_cargo, encoding="ascii")
        host_rust_cargo.chmod(0o500)

        command = [
            str(sandbox),
            "--die-with-parent",
            "--unshare-net",
            "--ro-bind",
            "/",
            "/",
            "--ro-bind",
            str(fixture / "home"),
            "/home",
            "--bind-fd",
            "PLACEHOLDER_STAGE_FD",
            str(stage),
            "--dev",
            "/dev",
            "--proc",
            "/proc",
            "--chdir",
            str(source),
            "--clearenv",
        ]
        for name, value in sorted(contract["effective_environment"].items()):
            if name != "PWD":
                command.extend(("--setenv", name, value))
        command.extend(("--", str(router_cargo), "build", "--release"))
        stage_fd = os.open(
            authorized_stage, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW
        )
        try:
            command[command.index("PLACEHOLDER_STAGE_FD")] = str(stage_fd)
            result = subprocess.run(
                command,
                env={},
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
                pass_fds=(stage_fd,),
            )
        finally:
            os.close(stage_fd)

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""


def test_bwrap_rejects_foreign_physical_cwd(
    verified_source_fd: int,
) -> None:
    sandbox = Path("/usr/bin/bwrap")
    try:
        sandbox_st = sandbox.lstat()
        sandbox_raw = sandbox.read_bytes()
        version = subprocess.run(
            [str(sandbox), "--version"],
            env={},
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        pytest.fail(f"reviewed Bubblewrap authority is unavailable: {exc}")
    assert {
        "gid": sandbox_st.st_gid,
        "mode": f"{stat.S_IMODE(sandbox_st.st_mode):04o}",
        "sha256": hashlib.sha256(sandbox_raw).hexdigest(),
        "size": sandbox_st.st_size,
        "type": "file" if stat.S_ISREG(sandbox_st.st_mode) else "other",
        "uid": sandbox_st.st_uid,
        "version": version.stdout.strip() if version.returncode == 0 else "",
    } == {
        "gid": 0,
        "mode": "0755",
        "sha256": "52231e1caf55bcbc667b269f49c63599a6f7db4767ae6a039580d0ff853db712",
        "size": 72160,
        "type": "file",
        "uid": 0,
        "version": "bubblewrap 0.9.0",
    }

    engine = toolchain.load_json(ENGINE_POLICY)
    policy = engine["native_build_environment"]
    stage = Path(roots_path(engine, "candidate_build_root")) / (
        "stage-1122334455667788"
    )
    source = stage / "source"
    contract = _build_environment(policy, {}, stage, verified_source_fd)
    wrapper = next(
        entry
        for entry in engine["command_router"]["entries"]
        if entry["name"] == "cargo"
    )
    router_cargo = Path(roots_path(engine, "candidate_toolchain_root")) / (
        "bin/cargo"
    )
    rust_cargo = Path(wrapper["exec_target"]["path"])

    with tempfile.TemporaryDirectory(prefix="p1-u03-bwrap-cwd-", dir="/tmp") as raw:
        fixture = Path(raw)

        def host_path(sandbox_path: Path) -> Path:
            return fixture / sandbox_path.relative_to("/")

        host_stage = host_path(stage)
        host_router_cargo = host_path(router_cargo)
        host_rust_cargo = host_path(rust_cargo)
        foreign_source = fixture / "foreign-source"
        host_stage.mkdir(parents=True)
        host_router_cargo.parent.mkdir(parents=True)
        host_rust_cargo.parent.mkdir(parents=True)
        foreign_source.mkdir()
        (host_stage / "source").symlink_to("/tmp")
        host_router_cargo.write_text(wrapper["contents"], encoding="ascii")
        host_router_cargo.chmod(0o500)
        fake_cargo = (
            "#!/usr/bin/python3.12 -IS\n"
            "import os\n"
            "import sys\n"
            f"CARGO = {str(rust_cargo)!r}\n"
            f"LOGICAL_SOURCE = {str(source)!r}\n"
            "if os.getcwd() != '/tmp':\n"
            "    raise SystemExit(90)\n"
            "if os.environ.get('PWD') != LOGICAL_SOURCE:\n"
            "    raise SystemExit(91)\n"
            "if sys.argv != [CARGO, 'build', '--locked', '--offline', '--release']:\n"
            "    raise SystemExit(92)\n"
        )
        host_rust_cargo.write_text(fake_cargo, encoding="ascii")
        host_rust_cargo.chmod(0o500)

        command = [
            str(sandbox),
            "--die-with-parent",
            "--unshare-net",
            "--ro-bind",
            "/",
            "/",
            "--ro-bind",
            str(fixture / "home"),
            "/home",
            "--ro-bind",
            str(foreign_source),
            "/tmp",
            "--dev",
            "/dev",
            "--proc",
            "/proc",
            "--chdir",
            str(source),
            "--clearenv",
        ]
        for name, value in sorted(contract["effective_environment"].items()):
            if name != "PWD":
                command.extend(("--setenv", name, value))
        command.extend(("--", str(router_cargo), "build", "--release"))
        result = subprocess.run(
            command,
            env={},
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

    assert result.returncode != 0
    assert result.stdout == ""
    assert result.stderr == "cargo wrapper working directory is not exact\n"


@pytest.mark.parametrize("attack", ["direct-source", "whole-stage"])
def test_bwrap_rejects_foreign_bind_before_cargo(attack: str) -> None:
    sandbox = Path("/usr/bin/bwrap")
    engine = toolchain.load_json(ENGINE_POLICY)
    policy = engine["native_build_environment"]
    stage = Path(roots_path(engine, "candidate_build_root")) / (
        "stage-22446688aaccee00"
    )
    source = stage / "source"
    wrapper = next(
        entry
        for entry in engine["command_router"]["entries"]
        if entry["name"] == "cargo"
    )
    router_cargo = Path(roots_path(engine, "candidate_toolchain_root")) / (
        "bin/cargo"
    )
    rust_cargo = Path(wrapper["exec_target"]["path"])

    with tempfile.TemporaryDirectory(prefix="p1-u03-bwrap-bind-", dir="/tmp") as raw:
        fixture = Path(raw)

        def host_path(sandbox_path: Path) -> Path:
            return fixture / sandbox_path.relative_to("/")

        host_stage = host_path(stage)
        host_router_cargo = host_path(router_cargo)
        host_rust_cargo = host_path(rust_cargo)
        foreign_source = fixture / "foreign-source"
        foreign_stage = fixture / "foreign-stage"
        (host_stage / "source").mkdir(parents=True)
        (foreign_stage / "source").mkdir(parents=True)
        foreign_source.mkdir()
        host_router_cargo.parent.mkdir(parents=True)
        host_rust_cargo.parent.mkdir(parents=True)
        source_fd = os.open(
            host_stage / "source",
            os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
        try:
            contract = toolchain._verify_build_environment(
                policy,
                {},
                stage,
                verified_source_fd=source_fd,
                mount_destinations=[stage],
            )
        finally:
            os.close(source_fd)
        host_router_cargo.write_text(wrapper["contents"], encoding="ascii")
        host_router_cargo.chmod(0o500)
        host_rust_cargo.write_text(
            "#!/usr/bin/python3.12 -IS\n"
            "import os\n"
            "import sys\n"
            f"CARGO = {str(rust_cargo)!r}\n"
            f"SOURCE = {str(source)!r}\n"
            "if os.getcwd() != SOURCE or os.environ.get('PWD') != SOURCE:\n"
            "    raise SystemExit(90)\n"
            "if sys.argv != [CARGO, 'build', '--locked', '--offline', '--release']:\n"
            "    raise SystemExit(91)\n"
            "print('FAKE_CARGO_REACHED')\n",
            encoding="ascii",
        )
        host_rust_cargo.chmod(0o500)

        command = [
            str(sandbox),
            "--die-with-parent",
            "--unshare-net",
            "--ro-bind",
            "/",
            "/",
            "--ro-bind",
            str(fixture / "home"),
            "/home",
        ]
        if attack == "direct-source":
            command.extend(("--ro-bind", str(foreign_source), str(source)))
        else:
            command.extend(("--ro-bind", str(foreign_stage), str(stage)))
        command.extend(("--dev", "/dev", "--proc", "/proc", "--chdir", str(source), "--clearenv"))
        for name, value in sorted(contract["effective_environment"].items()):
            if name != "PWD":
                command.extend(("--setenv", name, value))
        command.extend(("--", str(router_cargo), "build", "--release"))
        result = subprocess.run(
            command,
            env={},
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

    assert result.returncode != 0
    assert result.stdout == ""
    assert result.stderr == "cargo wrapper source identity is not exact\n"


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (b'["rustc", "--version"]', b'["ambient-rustc", "--version"]'),
        (
            b'os.environ["CC"] = "sccache clang" if USE_SCCACHE else "clang"',
            b'os.environ["CC"] = "ambient-cc"',
        ),
        (
            b'os.environ["CXX"] = "sccache clang++" if USE_SCCACHE else "clang++"',
            b'os.environ["CXX"] = "ambient-cxx"',
        ),
        (
            b'os.environ["LDSHARED"] = "clang -shared"',
            b'os.environ["LDSHARED"] = "ambient-cc -shared"',
        ),
    ],
)
def test_build_script_trace_is_bound_to_external_source(
    before: bytes, after: bytes
) -> None:
    configured = os.environ[CACHE_ENV]
    engine = toolchain.load_json(ENGINE_POLICY)
    inputs = toolchain.load_json(INPUT_POLICY)
    source_record = inputs["source"]["artifact"]
    source = toolchain._source_members(
        Path(configured) / "source-inputs" / source_record["filename"], inputs
    )["build.py"]
    trace = engine["native_build_environment"]["sealed_source_trace"]["build.py"]
    toolchain._verify_build_script_trace(source, trace)

    with pytest.raises(toolchain.VerificationError, match="build.py source trace"):
        toolchain._verify_build_script_trace(source.replace(before, after), trace)


def test_external_cache_generation_matches_manifest(
    tmp_path: Path,
) -> None:
    configured = os.environ[CACHE_ENV]
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    for output in (first, second):
        result = subprocess.run(
            [
                sys.executable,
                str(GENERATOR),
                "--evidence-cache",
                configured,
                "--output",
                str(output),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.startswith("NAUTILUS_TOOLCHAIN_INPUTS=PASS ")
    assert first.read_bytes() == second.read_bytes() == MANIFEST.read_bytes()

    checked = subprocess.run(
        [
            sys.executable,
            str(GENERATOR),
            "--evidence-cache",
            configured,
            "--check",
            str(MANIFEST),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert checked.returncode == 0, checked.stderr
    assert checked.stdout.startswith("NAUTILUS_TOOLCHAIN_INPUTS=PASS ")

    existing = subprocess.run(
        [
            sys.executable,
            str(GENERATOR),
            "--evidence-cache",
            configured,
            "--output",
            str(first),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert existing.returncode == 2
    assert "output path must be explicitly absent" in existing.stderr
