from __future__ import annotations

from collections.abc import Iterator
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "engines/nautilus/engine-build-policy.json"
SCRIPT = ROOT / "scripts/build_nautilus_engine.py"


@pytest.fixture
def tmp_path() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="nautilus-engine-test-", dir="/tmp") as directory:
        yield Path(directory)


def _module():
    spec = importlib.util.spec_from_file_location("nautilus_engine_build", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_fixture_wheel(path: Path, native_names: tuple[str, ...]) -> None:
    dist_info = "nautilus_trader-1.227.0.dist-info"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as wheel:
        wheel.writestr("nautilus_trader/__init__.py", "__version__ = '1.227.0'\n")
        for index, name in enumerate(native_names):
            wheel.writestr(name, f"native-{index}".encode())
        wheel.writestr(
            f"{dist_info}/METADATA",
            "\n".join(
                (
                    "Metadata-Version: 2.4",
                    "Name: nautilus_trader",
                    "Version: 1.227.0",
                    "Requires-Python: >=3.12,<3.15",
                    "",
                )
            ),
        )
        wheel.writestr(
            f"{dist_info}/WHEEL",
            "Wheel-Version: 1.0\nRoot-Is-Purelib: false\nTag: cp312-cp312-linux_x86_64\n",
        )
        wheel.writestr(f"{dist_info}/RECORD", "")


def _write_dependency_wheel(path: Path, package: str, version: str) -> None:
    dist_info = f"{package.replace('-', '_')}-{version}.dist-info"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as wheel:
        wheel.writestr(f"{package.replace('-', '_')}/__init__.py", "")
        wheel.writestr(
            f"{dist_info}/METADATA",
            f"Metadata-Version: 2.4\nName: {package}\nVersion: {version}\n",
        )
        wheel.writestr(f"{dist_info}/WHEEL", "Wheel-Version: 1.0\nTag: py3-none-any\n")
        wheel.writestr(f"{dist_info}/RECORD", "")


def _sealed_wheel_cache(tmp_path: Path) -> tuple[Path, str]:
    cache = tmp_path / "wheel-cache"
    cache.mkdir(mode=0o700)
    versions = {
        "cython": "3.2.4",
        "numpy": "2.4.3",
        "packaging": "26.0",
        "pip": "26.1",
        "poetry-core": "2.3.1",
        "setuptools": "82.0.1",
    }
    records = []
    for package, version in versions.items():
        filename = f"{package.replace('-', '_')}-{version}-py3-none-any.whl"
        wheel = cache / filename
        _write_dependency_wheel(wheel, package, version)
        records.append(
            {
                "filename": filename,
                "package": package,
                "version": version,
                "role": "build",
                "sha256": _sha256(wheel),
                "size": wheel.stat().st_size,
            }
        )
        wheel.chmod(0o400)
    manifest = cache / "wheel-cache-manifest.json"
    manifest.write_text(
        json.dumps(
            {"schema_version": 1, "python_minor": "3.12", "artifacts": records},
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    manifest.chmod(0o400)
    digest = _sha256(manifest)
    cache.chmod(0o500)
    return cache, digest


def test_committed_policy_binds_python_and_upstream_provenance() -> None:
    module = _module()

    policy = module.load_policy(POLICY)

    assert policy["python_minor"] == "3.12"
    assert policy["engine_version"] == "1.227.0"
    assert policy["upstream_commit"] == "280ae1762df51a492a4ce71506a40b5c8706def5"
    assert policy["cargo_lock_sha256"] == "083652294183947a352d1443ed0245311bf7ee5a716b66ccc21e814be25851ed"
    assert policy["required_rust_version"] == "1.95.0"


def test_wrong_python_is_rejected_before_build(tmp_path: Path) -> None:
    module = _module()
    python = tmp_path / "python3.11"
    python.write_text("#!/bin/sh\nprintf 'CPython 3.11.15\\n'\n", encoding="utf-8")
    python.chmod(0o700)

    with pytest.raises(module.VerificationError, match="Python 3.12"):
        module.validate_python(python, "3.12")


def test_artifact_verifier_rejects_wrong_python(tmp_path: Path) -> None:
    module = _module()
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir(mode=0o700)
    wheel = artifacts / "nautilus_trader-1.227.0-cp312-cp312-linux_x86_64.whl"
    _write_fixture_wheel(
        wheel,
        ("nautilus_trader/core/nautilus_pyo3.cpython-312-x86_64-linux-gnu.so",),
    )
    policy = module.load_policy(POLICY)
    module.write_artifact_manifest(
        artifacts,
        wheel,
        policy,
        python_identity="CPython 3.12.3",
        cargo_identity="cargo 1.95.0 (fixture)",
        rustc_identity="rustc 1.95.0 (fixture)",
        input_cache_manifest_sha256="1" * 64,
        wheel_cache_manifest_sha256="2" * 64,
    )
    python = tmp_path / "python3.11"
    python.write_text("#!/bin/sh\nprintf 'CPython 3.11.15\\n'\n", encoding="utf-8")
    python.chmod(0o700)

    with pytest.raises(module.VerificationError, match="Python 3.12"):
        module.verify_artifacts(artifacts, policy, python=python)


def test_network_enabled_build_path_is_rejected_before_inputs_are_read(tmp_path: Path) -> None:
    module = _module()

    with pytest.raises(module.VerificationError, match="offline"):
        module.build_engine(
            policy_path=tmp_path / "missing-policy.json",
            input_cache=tmp_path / "missing-input-cache",
            wheel_cache=tmp_path / "missing-wheel-cache",
            wheel_cache_manifest_sha256="0" * 64,
            python=tmp_path / "missing-python",
            cargo=tmp_path / "missing-cargo",
            llvm_toolchain=tmp_path / "missing-llvm-toolchain",
            sandbox=tmp_path / "missing-sandbox",
            destination=tmp_path / "candidate",
            offline=False,
        )


def test_verifier_rejects_an_unmanifested_native_library() -> None:
    module = _module()
    with tempfile.TemporaryDirectory(prefix="nautilus-engine-test-", dir="/tmp") as directory:
        artifacts = Path(directory) / "artifacts"
        artifacts.mkdir(mode=0o700)
        wheel = artifacts / "nautilus_trader-1.227.0-cp312-cp312-linux_x86_64.whl"
        _write_fixture_wheel(
            wheel,
            ("nautilus_trader/core/nautilus_pyo3.cpython-312-x86_64-linux-gnu.so",),
        )
        policy = module.load_policy(POLICY)
        module.write_artifact_manifest(
            artifacts,
            wheel,
            policy,
            python_identity="CPython 3.12.3",
            cargo_identity="cargo 1.95.0 (fixture)",
            rustc_identity="rustc 1.95.0 (fixture)",
            input_cache_manifest_sha256="1" * 64,
            wheel_cache_manifest_sha256="2" * 64,
        )

        artifacts.chmod(0o700)
        wheel.chmod(0o600)
        with zipfile.ZipFile(wheel, "a", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("nautilus_trader/core/implant.cpython-312-x86_64-linux-gnu.so", b"implant")
        manifest = artifacts / "artifact-manifest.json"
        manifest.chmod(0o600)
        document = json.loads(manifest.read_text(encoding="utf-8"))
        document["wheel"]["sha256"] = _sha256(wheel)
        document["wheel"]["size"] = wheel.stat().st_size
        manifest.write_text(json.dumps(document, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        wheel.chmod(0o400)
        manifest.chmod(0o400)
        artifacts.chmod(0o500)

        with pytest.raises(module.VerificationError, match="unexpected native"):
            module.verify_artifacts(artifacts, policy, python=Path("/usr/bin/python3.12"))


def test_root_python_311_cannot_import_the_unactivated_engine() -> None:
    assert sys.version_info[:2] == (3, 11)
    environment = {
        "HOME": os.environ.get("HOME", "/tmp"),
        "LANG": "C.UTF-8",
        "PATH": "/usr/bin:/bin",
        "PYTHONNOUSERSITE": "1",
    }

    result = subprocess.run(
        [sys.executable, "-I", "-c", "import nautilus_trader"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "ModuleNotFoundError" in result.stderr


def test_artifact_verifier_accepts_the_exact_sealed_wheel() -> None:
    module = _module()
    with tempfile.TemporaryDirectory(prefix="nautilus-engine-test-", dir="/tmp") as directory:
        artifacts = Path(directory) / "artifacts"
        artifacts.mkdir(mode=0o700)
        wheel = artifacts / "nautilus_trader-1.227.0-cp312-cp312-linux_x86_64.whl"
        _write_fixture_wheel(
            wheel,
            ("nautilus_trader/core/nautilus_pyo3.cpython-312-x86_64-linux-gnu.so",),
        )
        policy = module.load_policy(POLICY)
        expected = module.write_artifact_manifest(
            artifacts,
            wheel,
            policy,
            python_identity="CPython 3.12.3",
            cargo_identity="cargo 1.95.0 (fixture)",
            rustc_identity="rustc 1.95.0 (fixture)",
            input_cache_manifest_sha256="1" * 64,
            wheel_cache_manifest_sha256="2" * 64,
        )

        observed = module.verify_artifacts(artifacts, policy, python=Path("/usr/bin/python3.12"))

        assert observed == expected
        assert stat.S_IMODE(artifacts.stat().st_mode) == 0o500
        assert stat.S_IMODE(wheel.stat().st_mode) == 0o400


def test_publish_preserves_seal_after_atomic_rename(tmp_path: Path) -> None:
    module = _module()
    staging = tmp_path / "staging"
    staging.mkdir(mode=0o700)
    wheel = staging / "nautilus_trader.whl"
    wheel.write_bytes(b"wheel")
    wheel.chmod(0o400)
    staging.chmod(0o500)
    destination = tmp_path / "published"

    module._publish_artifacts(staging, destination)

    assert not staging.exists()
    assert destination.is_dir()
    assert (destination / wheel.name).read_bytes() == b"wheel"
    assert stat.S_IMODE(destination.stat().st_mode) == 0o500
    assert stat.S_IMODE((destination / wheel.name).stat().st_mode) == 0o400


def test_wheel_cache_requires_the_operator_approved_manifest_and_exact_files(tmp_path: Path) -> None:
    module = _module()
    cache, digest = _sealed_wheel_cache(tmp_path)
    policy = module.load_policy(POLICY)

    document = module.verify_wheel_cache(cache, digest, policy)

    assert document["python_minor"] == "3.12"
    assert {record["package"] for record in document["artifacts"]} == {
        "cython",
        "numpy",
        "packaging",
        "pip",
        "poetry-core",
        "setuptools",
    }

    with pytest.raises(module.VerificationError, match="operator-approved"):
        module.verify_wheel_cache(cache, "0" * 64, policy)

    cache.chmod(0o700)
    unexpected = cache / "unapproved.whl"
    unexpected.write_bytes(b"not approved")
    unexpected.chmod(0o400)
    cache.chmod(0o500)
    with pytest.raises(module.VerificationError, match="unexpected"):
        module.verify_wheel_cache(cache, digest, policy)


def test_wheel_cache_rejects_hash_drift(tmp_path: Path) -> None:
    module = _module()
    cache, digest = _sealed_wheel_cache(tmp_path)
    policy = module.load_policy(POLICY)
    wheel = next(cache.glob("cython-*.whl"))
    cache.chmod(0o700)
    wheel.chmod(0o600)
    wheel.write_bytes(wheel.read_bytes() + b"drift")
    wheel.chmod(0o400)
    cache.chmod(0o500)

    with pytest.raises(module.VerificationError, match="digest or size drift"):
        module.verify_wheel_cache(cache, digest, policy)


def test_bubblewrap_build_boundary_has_no_host_network_route(tmp_path: Path) -> None:
    module = _module()
    stage = tmp_path / "stage"
    stage.mkdir()
    probe = (
        "from pathlib import Path; "
        "routes=Path('/proc/net/route').read_text().splitlines()[1:]; "
        "raise SystemExit(9 if any(line.split()[1] == '00000000' for line in routes) else 0)"
    )

    module._sandbox_run(
        Path("/usr/bin/bwrap"),
        stage,
        stage,
        {"PATH": "/usr/bin:/bin"},
        [sys.executable, "-I", "-c", probe],
        timeout=30,
    )


def test_bubblewrap_build_boundary_uses_a_private_writable_compiler_tempdir(
    tmp_path: Path,
) -> None:
    module = _module()
    stage = tmp_path / "stage"
    stage.mkdir(mode=0o700)
    environment = {"PATH": "/usr/bin:/bin"}
    environment.update(module._stage_compiler_temp_environment(stage))
    host_temporary_file = Path("/tmp") / f"nautilus-host-temp-{os.getpid()}"
    assert not host_temporary_file.exists()
    probe = f"""\
import os
from pathlib import Path

stage_temp = Path(os.environ["TMPDIR"])
assert os.environ["TEMP"] == str(stage_temp)
assert os.environ["TMP"] == str(stage_temp)
assert stage_temp.is_dir()
(stage_temp / "compiler-object").write_bytes(b"object")
host_temp = Path({str(host_temporary_file)!r})
assert not host_temp.exists()
try:
    host_temp.write_bytes(b"host")
except OSError:
    pass
else:
    raise SystemExit("host /tmp is writable inside the sandbox")
"""

    module._sandbox_run(
        Path("/usr/bin/bwrap"),
        stage,
        stage,
        environment,
        [sys.executable, "-I", "-c", probe],
        timeout=30,
    )

    assert (stage / "compiler-tmp" / "compiler-object").read_bytes() == b"object"
    assert not host_temporary_file.exists()


def test_make_build_and_verify_targets_preserve_the_offline_contract() -> None:
    variables = [
        "NAUTILUS_ENGINE_PYTHON=/approved/python3.12",
        "NAUTILUS_ENGINE_INPUT_CACHE=/approved/input-cache",
        "NAUTILUS_ENGINE_WHEEL_CACHE=/approved/wheel-cache",
        f"NAUTILUS_ENGINE_WHEEL_CACHE_MANIFEST_SHA256={'a' * 64}",
        "NAUTILUS_ENGINE_CARGO=/approved/toolchain/bin/cargo",
        "NAUTILUS_ENGINE_LLVM_TOOLCHAIN=/approved/llvm-toolchain",
        "NAUTILUS_ENGINE_ARTIFACTS=/approved/artifacts",
    ]

    build = subprocess.run(
        ["make", "-n", "build-nautilus-engine", *variables],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    verify = subprocess.run(
        ["make", "-n", "verify-nautilus-engine", *variables],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert build.returncode == 0, build.stderr
    assert "--build" in build.stdout
    assert "--offline" in build.stdout
    assert "--wheel-cache-manifest-sha256" in build.stdout
    assert '--llvm-toolchain "/approved/llvm-toolchain"' in build.stdout
    assert verify.returncode == 0, verify.stderr
    assert "--verify" in verify.stdout
    assert "--build" not in verify.stdout


def test_build_environment_selects_only_absolute_private_compilers(tmp_path: Path) -> None:
    module = _module()
    llvm_bin = tmp_path / "llvm" / "bin"
    cargo_bin = tmp_path / "rust" / "bin"
    venv_bin = tmp_path / "venv" / "bin"

    environment = module._build_tool_environment(llvm_bin, cargo_bin, venv_bin)

    assert environment["CC"] == str(llvm_bin / "clang")
    assert environment["CXX"] == str(llvm_bin / "clang++")
    assert environment["LD"] == str(llvm_bin / "ld.lld")
    assert environment["CARGO_BUILD_TARGET"] == "x86_64-unknown-linux-gnu"
    assert environment["CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER"] == str(
        llvm_bin / "clang"
    )
    assert environment["RUSTFLAGS"] == f"-C linker={llvm_bin / 'clang'}"
    assert environment["PATH"].split(":")[:3] == [
        str(llvm_bin),
        str(cargo_bin),
        str(venv_bin),
    ]


def test_build_rejects_an_ambient_compiler_fallback(tmp_path: Path) -> None:
    module = _module()
    ambient_bin = tmp_path / "ambient-bin"
    ambient_bin.mkdir()
    (ambient_bin / "clang").write_text("ambient", encoding="utf-8")

    with pytest.raises(module.VerificationError, match="ambient compiler fallback"):
        module._reject_ambient_compilers((ambient_bin,))
