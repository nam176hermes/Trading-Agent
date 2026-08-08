from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / "engines/nautilus/sealed_uv_exec"
PRIVATE_RUST = Path(
    "/home/thenam176/.cache/trading-agent/nautilus/rust-1.95.0"
)
PRIVATE_LLVM = Path(
    "/home/thenam176/.cache/trading-agent/nautilus/llvm-22.1.3-resource-toolchain"
)
TARGET = "x86_64-unknown-linux-gnu"
FIXED_ENVIRONMENT = (
    "PATH=/usr/bin:/bin",
    "PYTHONDONTWRITEBYTECODE=1",
    "PYTHONHASHSEED=0",
    "PYTHONNOUSERSITE=1",
    "UV_OFFLINE=1",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _require_private_toolchains() -> tuple[Path, Path, Path]:
    cargo = PRIVATE_RUST / "bin/cargo"
    rustc = PRIVATE_RUST / "bin/rustc"
    clang = PRIVATE_LLVM / "bin/clang"
    if not all(path.is_file() for path in (cargo, rustc, clang)):
        pytest.skip("sealed private Rust 1.95.0 and LLVM toolchains are unavailable")
    return cargo, rustc, clang


def _build_helper(build_root: Path) -> Path:
    cargo, rustc, clang = _require_private_toolchains()
    cargo_home = build_root / "cargo-home"
    target_directory = build_root / "target"
    compiler_tmp = build_root / "tmp"
    for directory in (cargo_home, target_directory, compiler_tmp):
        directory.mkdir(mode=0o700)
    environment = {
        "CARGO_HOME": str(cargo_home),
        "CARGO_INCREMENTAL": "0",
        "CARGO_NET_OFFLINE": "true",
        "CARGO_TARGET_DIR": str(target_directory),
        "HOME": str(build_root),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": f"{PRIVATE_RUST / 'bin'}:{PRIVATE_LLVM / 'bin'}:/usr/bin:/bin",
        "RUSTC": str(rustc),
        "RUSTFLAGS": (
            f"-C linker={clang} -C link-arg=-fuse-ld=lld "
            "-C link-arg=-Wl,--build-id=none"
        ),
        "SOURCE_DATE_EPOCH": "0",
        "TEMP": str(compiler_tmp),
        "TMP": str(compiler_tmp),
        "TMPDIR": str(compiler_tmp),
    }
    subprocess.run(
        [
            str(cargo),
            "build",
            "--manifest-path",
            str(PROJECT / "Cargo.toml"),
            "--locked",
            "--offline",
            "--release",
            "--target",
            TARGET,
        ],
        check=True,
        cwd=PROJECT,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
    )
    return target_directory / TARGET / "release/nautilus-sealed-uv-exec"


@pytest.fixture(scope="session")
def helper(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _build_helper(tmp_path_factory.mktemp("sealed-uv-helper", numbered=True))


@pytest.fixture
def native_tmp_path() -> Path:
    root = Path(tempfile.mkdtemp(prefix="sealed-uv-fixture-", dir="/tmp"))
    root.chmod(0o700)
    try:
        yield root
    finally:
        shutil.rmtree(root)


def _compile_fixture(tmp_path: Path) -> tuple[Path, Path]:
    _, _, clang = _require_private_toolchains()
    marker = tmp_path / "fixture-executed"
    source = tmp_path / "fixture.c"
    binary = tmp_path / "fixture-uv"
    source.write_text(
        "#include <stdio.h>\n"
        "#include <stdlib.h>\n"
        "#include <unistd.h>\n"
        "extern char **environ;\n"
        "int main(int argc, char **argv) {\n"
        f'  FILE *marker = fopen("{marker}", "w");\n'
        "  if (marker == NULL) return 91;\n"
        "  char executable[4096];\n"
        "  ssize_t executable_length = readlink(\"/proc/self/exe\", executable, sizeof(executable) - 1);\n"
        "  if (executable_length < 0) return 93;\n"
        "  executable[executable_length] = '\\0';\n"
        "  fprintf(marker, \"exe=%s\\nargv=%s\\n\", executable, argv[0]);\n"
        "  fclose(marker);\n"
        "  for (char **entry = environ; *entry != NULL; ++entry)\n"
        "    puts(*entry);\n"
        "  return argc == 2 ? 0 : 92;\n"
        "}\n",
        encoding="ascii",
    )
    subprocess.run(
        [str(clang), "-O2", "-Wl,--build-id=none", "-o", str(binary), str(source)],
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )
    binary.chmod(0o755)
    return binary, marker


def _command(
    helper: Path,
    program: Path | str,
    cwd: Path | str,
    *,
    digest: str | None = None,
    uid: int | None = None,
    gid: int | None = None,
    mode: str = "0755",
    action: str = "version",
) -> list[str]:
    program_path = Path(program)
    info = program_path.stat() if program_path.exists() else None
    return [
        str(helper),
        "--program",
        str(program),
        "--sha256",
        _sha256(program_path) if digest is None and info is not None else digest or "0" * 64,
        "--uid",
        str(info.st_uid if uid is None and info is not None else uid if uid is not None else 0),
        "--gid",
        str(info.st_gid if gid is None and info is not None else gid if gid is not None else 0),
        "--mode",
        mode,
        "--cwd",
        str(cwd),
        "--action",
        action,
    ]


def _run(command: list[str], *, poison: bool = True) -> subprocess.CompletedProcess[bytes]:
    environment = {"POISON": "ambient-value"} if poison else {}
    return subprocess.run(
        command,
        check=False,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
    )


@pytest.mark.parametrize(
    ("mutation"),
    (
        "relative-program",
        "relative-cwd",
        "digest-mismatch",
        "owner-mismatch",
        "mode-mismatch",
        "arbitrary-action",
    ),
)
def test_native_helper_rejects_invalid_authority_without_executing_fixture(
    helper: Path, native_tmp_path: Path, mutation: str
) -> None:
    fixture, marker = _compile_fixture(native_tmp_path)
    program: Path | str = fixture
    cwd: Path | str = native_tmp_path
    options: dict[str, object] = {}
    if mutation == "relative-program":
        program = fixture.name
    elif mutation == "relative-cwd":
        cwd = "."
    elif mutation == "digest-mismatch":
        options["digest"] = "0" * 64
    elif mutation == "owner-mismatch":
        options["uid"] = fixture.stat().st_uid + 1
    elif mutation == "mode-mismatch":
        fixture.chmod(0o700)
    else:
        options["action"] = "arbitrary-command"

    completed = _run(_command(helper, program, cwd, **options), poison=False)

    assert completed.returncode != 0
    assert completed.stdout == b""
    assert len(completed.stderr) <= 256
    assert not marker.exists()


def test_native_helper_executes_a_sealed_image_with_exact_fixed_environment(
    helper: Path, native_tmp_path: Path
) -> None:
    fixture, marker = _compile_fixture(native_tmp_path)

    completed = _run(_command(helper, fixture, native_tmp_path), poison=True)

    assert completed.returncode == 0
    assert completed.stderr == b""
    marker_lines = marker.read_text(encoding="ascii").splitlines()
    assert marker_lines[0].startswith("exe=/memfd:nautilus-sealed-uv")
    assert str(fixture) not in marker_lines[0]
    assert marker_lines[1] == "argv=uv"
    assert completed.stdout.decode("ascii").splitlines() == list(FIXED_ENVIRONMENT)


def test_native_helper_discards_inherited_poison_before_exec(
    helper: Path, native_tmp_path: Path
) -> None:
    fixture, marker = _compile_fixture(native_tmp_path)

    completed = _run(_command(helper, fixture, native_tmp_path), poison=True)

    assert completed.returncode == 0
    assert completed.stderr == b""
    assert marker.exists()
    assert completed.stdout.decode("ascii").splitlines() == list(FIXED_ENVIRONMENT)


def test_native_helper_source_uses_only_execveat_for_the_sealed_image() -> None:
    source = (PROJECT / "src/main.rs").read_text(encoding="utf-8")

    assert "/proc/self/fd" not in source
    assert "fn execve(" not in source
    assert "SYS_EXECVEAT" in source
    assert "SYS_EXECVEAT,\n                fd," in source
    assert "AT_EMPTY_PATH," in source


def test_native_helper_build_is_reproducible_across_private_build_roots(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir(mode=0o700)
    second_root.mkdir(mode=0o700)

    first = _build_helper(first_root)
    second = _build_helper(second_root)

    assert first.stat().st_size == second.stat().st_size
    assert _sha256(first) == _sha256(second)
