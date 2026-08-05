from __future__ import annotations

from collections.abc import Iterator
import json
import hashlib
import importlib.util
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import textwrap

import pytest


ROOT = Path(__file__).resolve().parents[2]
ENGINE_POLICY = ROOT / "engines/nautilus/engine-build-policy.json"
WHEEL_POLICY = ROOT / "engines/nautilus/wheel-cache-policy.json"
SCRIPT = ROOT / "scripts/prepare_nautilus_wheel_cache.py"


@pytest.fixture
def tmp_path() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="nautilus-wheel-test-", dir="/tmp") as directory:
        yield Path(directory)


def _module():
    spec = importlib.util.spec_from_file_location("nautilus_wheel_cache", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fake_python312(path: Path, *, audit: Path | None = None) -> Path:
    script = path / "python3.12"
    script.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/python3
            import os
            import json
            from pathlib import Path
            import sys
            import zipfile

            if "-c" in sys.argv:
                print("CPython 3.12.3")
                raise SystemExit(0)
            if sys.argv[1:4] != ["-I", "-m", "pip"] or "download" not in sys.argv:
                raise SystemExit("only pip download is permitted")
            audit = __AUDIT__
            if audit:
                Path(audit).write_text(json.dumps({
                    "argv": sys.argv[1:],
                    "environment": {
                        key: os.environ.get(key)
                        for key in (
                            "HOME", "PIP_CACHE_DIR", "PIP_CONFIG_FILE", "PIP_INDEX_URL",
                            "PIP_NO_INPUT", "PYTHONPATH",
                        )
                    },
                }))
            destination = Path(sys.argv[sys.argv.index("--dest") + 1])
            requirements = [value for value in sys.argv if "==" in value]
            filenames = {
                "cython": "cython-3.2.4-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl",
                "numpy": "numpy-2.4.3-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl",
                "packaging": "packaging-26.0-py3-none-any.whl",
                "pip": "pip-26.1-py3-none-any.whl",
                "poetry-core": "poetry_core-2.3.1-py3-none-any.whl",
                "setuptools": "setuptools-82.0.1-py3-none-any.whl",
            }
            for requirement in requirements:
                package, version = requirement.split("==", 1)
                filename = filenames[package]
                dist_info = f"{package.replace('-', '_')}-{version}.dist-info"
                with zipfile.ZipFile(destination / filename, "w") as wheel:
                    wheel.writestr(
                        f"{dist_info}/METADATA",
                        f"Metadata-Version: 2.4\\nName: {package}\\nVersion: {version}\\n",
                    )
                    wheel.writestr(f"{dist_info}/WHEEL", "Wheel-Version: 1.0\\n")
                    wheel.writestr(f"{dist_info}/RECORD", "")
                    if package == "setuptools":
                        wheel.writestr(
                            "setuptools/_vendor/packaging-26.0.dist-info/METADATA",
                            "Metadata-Version: 2.4\\nName: packaging\\nVersion: 26.0\\n",
                        )
            """
        ).replace("__AUDIT__", repr(str(audit)) if audit else "None"),
        encoding="utf-8",
    )
    script.chmod(0o700)
    return script


def test_wheel_policy_is_the_exact_task3_python312_build_closure() -> None:
    engine = json.loads(ENGINE_POLICY.read_text(encoding="utf-8"))
    wheel = json.loads(WHEEL_POLICY.read_text(encoding="utf-8"))

    assert wheel == {
        "schema_version": 1,
        "python_implementation": "CPython",
        "python_minor": "3.12",
        "index_url": "https://pypi.org/simple",
        "engine_policy_sha256": "e1a9292997b9b4ac821b1292f8e340d92aeffd49422a7b379f7d76dce443b0dd",
        "packages": {
            "cython": "3.2.4",
            "numpy": "2.4.3",
            "packaging": "26.0",
            "pip": "26.1",
            "poetry-core": "2.3.1",
            "setuptools": "82.0.1",
        },
    }
    assert {
        name: wheel["packages"][name]
        for name in engine["required_build_wheels"]
    } == engine["required_build_wheels"]
    assert set(engine["required_unpinned_build_wheels"]) == {
        "numpy",
        "packaging",
        "pip",
        "setuptools",
    }


def test_acquire_writes_task3_manifest_and_seals_the_exact_wheel_set(tmp_path: Path) -> None:
    module = _module()
    cache = tmp_path / "wheel-cache"

    document, digest = module.acquire(
        cache,
        python=_fake_python312(tmp_path),
        policy_path=WHEEL_POLICY,
        engine_policy_path=ENGINE_POLICY,
    )

    assert document == json.loads((cache / "wheel-cache-manifest.json").read_text(encoding="utf-8"))
    assert set(document) == {"schema_version", "python_minor", "artifacts"}
    assert document["schema_version"] == 1
    assert document["python_minor"] == "3.12"
    assert [(item["package"], item["version"]) for item in document["artifacts"]] == [
        ("cython", "3.2.4"),
        ("numpy", "2.4.3"),
        ("packaging", "26.0"),
        ("pip", "26.1"),
        ("poetry-core", "2.3.1"),
        ("setuptools", "82.0.1"),
    ]
    assert digest == _sha256(cache / "wheel-cache-manifest.json")
    assert stat.S_IMODE(cache.stat().st_mode) == 0o500
    assert {path.name for path in cache.iterdir()} == {
        "wheel-cache-manifest.json",
        *(item["filename"] for item in document["artifacts"]),
    }
    for item in document["artifacts"]:
        assert set(item) == {"filename", "package", "version", "role", "sha256", "size"}
        assert item["role"] == "build"
        artifact = cache / item["filename"]
        assert item["sha256"] == _sha256(artifact)
        assert item["size"] == artifact.stat().st_size
        assert stat.S_IMODE(artifact.stat().st_mode) == 0o400
    assert stat.S_IMODE((cache / "wheel-cache-manifest.json").stat().st_mode) == 0o400
    assert module.verify(
        cache,
        expected_manifest_sha256=digest,
        policy_path=WHEEL_POLICY,
        engine_policy_path=ENGINE_POLICY,
    ) == document


def test_offline_verifier_rejects_a_py3_wheel_with_a_cp311_abi(tmp_path: Path) -> None:
    module = _module()
    cache = tmp_path / "wheel-cache"
    document, _digest = module.acquire(
        cache,
        python=_fake_python312(tmp_path),
        policy_path=WHEEL_POLICY,
        engine_policy_path=ENGINE_POLICY,
    )
    cache.chmod(0o700)
    manifest = cache / "wheel-cache-manifest.json"
    manifest.chmod(0o600)
    record = next(item for item in document["artifacts"] if item["package"] == "packaging")
    source = cache / record["filename"]
    source.chmod(0o600)
    incompatible = cache / "packaging-26.0-py3-cp311-any.whl"
    source.rename(incompatible)
    record["filename"] = incompatible.name
    incompatible.chmod(0o400)
    manifest.write_text(json.dumps(document, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    digest = _sha256(manifest)
    manifest.chmod(0o400)
    cache.chmod(0o500)

    with pytest.raises(module.VerificationError, match="incompatible with Python 3.12"):
        module.verify(
            cache,
            expected_manifest_sha256=digest,
            policy_path=WHEEL_POLICY,
            engine_policy_path=ENGINE_POLICY,
        )


def test_acquisition_uses_a_private_temporary_pip_cache_and_only_exact_requirements(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    audit = tmp_path / "pip-audit.json"
    monkeypatch.setenv("PIP_CACHE_DIR", "/ambient/pip-cache")
    monkeypatch.setenv("PIP_INDEX_URL", "https://private.invalid/simple")
    monkeypatch.setenv("PYTHONPATH", "/ambient/python")

    module.acquire(
        tmp_path / "wheel-cache",
        python=_fake_python312(tmp_path, audit=audit),
        policy_path=WHEEL_POLICY,
        engine_policy_path=ENGINE_POLICY,
    )

    observed = json.loads(audit.read_text(encoding="utf-8"))
    argv = observed["argv"]
    assert argv[:4] == ["-I", "-m", "pip", "download"]
    assert argv[argv.index("--index-url") + 1] == "https://pypi.org/simple"
    assert argv[argv.index("--python-version") + 1] == "3.12"
    assert "--only-binary=:all:" in argv
    assert "--no-deps" in argv
    assert sorted(value for value in argv if "==" in value) == [
        "cython==3.2.4",
        "numpy==2.4.3",
        "packaging==26.0",
        "pip==26.1",
        "poetry-core==2.3.1",
        "setuptools==82.0.1",
    ]
    environment = observed["environment"]
    assert environment["PIP_CACHE_DIR"].startswith(str(tmp_path / ".nautilus-wheel-cache."))
    assert environment["PIP_CACHE_DIR"].endswith("/.pip-cache")
    assert environment["PIP_CONFIG_FILE"] == "/dev/null"
    assert environment["PIP_INDEX_URL"] is None
    assert environment["PYTHONPATH"] is None
    assert environment["PIP_NO_INPUT"] == "1"


def test_acquisition_rejects_python311_before_downloading(tmp_path: Path) -> None:
    module = _module()
    python = tmp_path / "python3.11"
    python.write_text("#!/bin/sh\nprintf 'CPython 3.11.15\\n'\n", encoding="utf-8")
    python.chmod(0o700)

    with pytest.raises(module.VerificationError, match="explicit CPython 3.12"):
        module.acquire(
            tmp_path / "wheel-cache",
            python=python,
            policy_path=WHEEL_POLICY,
            engine_policy_path=ENGINE_POLICY,
        )

    assert not (tmp_path / "wheel-cache").exists()


def test_verification_is_offline_and_task3_accepts_the_generated_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    cache = tmp_path / "wheel-cache"
    document, digest = module.acquire(
        cache,
        python=_fake_python312(tmp_path),
        policy_path=WHEEL_POLICY,
        engine_policy_path=ENGINE_POLICY,
    )

    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: pytest.fail("network/process used"))
    assert module.verify(
        cache,
        expected_manifest_sha256=digest,
        policy_path=WHEEL_POLICY,
        engine_policy_path=ENGINE_POLICY,
    ) == document

    engine_module_spec = importlib.util.spec_from_file_location(
        "nautilus_engine_build_for_wheel_cache", ROOT / "scripts/build_nautilus_engine.py"
    )
    assert engine_module_spec and engine_module_spec.loader
    engine_module = importlib.util.module_from_spec(engine_module_spec)
    engine_module_spec.loader.exec_module(engine_module)
    engine_policy = engine_module.load_policy(ENGINE_POLICY)
    assert engine_module.verify_wheel_cache(cache, digest, engine_policy) == document


def test_offline_verifier_rejects_digest_drift_symlinks_and_unexpected_files(tmp_path: Path) -> None:
    module = _module()
    cache = tmp_path / "wheel-cache"
    _document, digest = module.acquire(
        cache,
        python=_fake_python312(tmp_path),
        policy_path=WHEEL_POLICY,
        engine_policy_path=ENGINE_POLICY,
    )
    wheel = next(path for path in cache.iterdir() if path.suffix == ".whl")
    cache.chmod(0o700)
    wheel.chmod(0o600)
    wheel.write_bytes(wheel.read_bytes() + b"tamper")
    wheel.chmod(0o400)
    cache.chmod(0o500)
    with pytest.raises(module.VerificationError, match="digest or size drift"):
        module.verify(
            cache,
            expected_manifest_sha256=digest,
            policy_path=WHEEL_POLICY,
            engine_policy_path=ENGINE_POLICY,
        )

    cache.chmod(0o700)
    wheel.chmod(0o600)
    wheel.write_bytes(wheel.read_bytes()[: -len(b"tamper")])
    wheel.chmod(0o400)
    unexpected = cache / "unexpected.whl"
    unexpected.write_bytes(b"unexpected")
    unexpected.chmod(0o400)
    cache.chmod(0o500)
    with pytest.raises(module.VerificationError, match="unexpected"):
        module.verify(
            cache,
            expected_manifest_sha256=digest,
            policy_path=WHEEL_POLICY,
            engine_policy_path=ENGINE_POLICY,
        )

    cache.chmod(0o700)
    unexpected.unlink()
    wheel.chmod(0o600)
    outside = tmp_path / "outside.whl"
    wheel.rename(outside)
    wheel.symlink_to(outside)
    cache.chmod(0o500)
    with pytest.raises(module.VerificationError, match="regular non-symlink"):
        module.verify(
            cache,
            expected_manifest_sha256=digest,
            policy_path=WHEEL_POLICY,
            engine_policy_path=ENGINE_POLICY,
        )


def test_cli_requires_python_only_for_acquisition_and_verifies_with_controller_python(tmp_path: Path) -> None:
    cache = tmp_path / "wheel-cache"
    acquire = subprocess.run(
        [
            sys.executable,
            "-I",
            str(SCRIPT),
            "--policy",
            str(WHEEL_POLICY),
            "--engine-policy",
            str(ENGINE_POLICY),
            "--cache",
            str(cache),
            "--python",
            str(_fake_python312(tmp_path)),
            "--acquire",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert acquire.returncode == 0, acquire.stderr
    digest = acquire.stdout.strip().rsplit(" ", 1)[-1]
    verify = subprocess.run(
        [
            sys.executable,
            "-I",
            str(SCRIPT),
            "--policy",
            str(WHEEL_POLICY),
            "--engine-policy",
            str(ENGINE_POLICY),
            "--cache",
            str(cache),
            "--manifest-sha256",
            digest,
            "--verify",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert verify.returncode == 0, verify.stderr
    assert verify.stdout.strip() == "nautilus wheel cache verification: PASS"
