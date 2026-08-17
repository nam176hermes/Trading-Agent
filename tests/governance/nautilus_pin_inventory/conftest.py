from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from typing import Callable, Protocol

import pytest

TEST_ROOT = Path(__file__).resolve().parent
REPO_ROOT = TEST_ROOT.parents[2]
KNOWN_BAD_COMMIT = "abaaeb6d873d134f7159bb60d6afc1c7a5fb849f"
KNOWN_BAD_TREE = "afa4e04a3fa325910a1bbc51622945f7d3b8ecf2"
KNOWN_BAD_BLOB = "ce6509bd778e8e41fe7123cf7c00d2cc9ca2df45"
KNOWN_BAD_SCRIPT_SHA256 = "dd80c9ec97d5ac68a0657aae294f7940f67a9c22c0e4c12db619d1de8f11d54b"
SUBJECT_ENV = "P1_U00_SUBJECT"

_oracle_spec = importlib.util.spec_from_file_location("p1_u00_required_identities", TEST_ROOT / "required_identities.py")
assert _oracle_spec and _oracle_spec.loader
required_identities = importlib.util.module_from_spec(_oracle_spec)
_oracle_spec.loader.exec_module(required_identities)


class Subject:
    """A test-only adapter for immutable RED and current-worktree GREEN runs."""

    def __init__(self, cli: Path, *, label: str) -> None:
        self.cli = cli
        self.label = label

    def run(self, root: Path, inventory: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(self.cli), "--root", str(root), "--inventory", str(inventory), "--fixture-filesystem", *arguments],
            cwd=REPO_ROOT, text=True, capture_output=True, check=False,
        )


class SourceIoSubject(Protocol):
    """Neutral observable custody operations; production API names stay unfrozen."""

    def snapshot(self, root: Path, path: Path, after_descriptor_bound: Callable[[str], None]) -> str: ...

    def publish(self, root: Path, inventory: Path, before_exchange: Callable[[], None]) -> None: ...


class _KnownBadSourceIoSubject:
    """RED-only bridge from neutral operations to abaaeb6 legacy helpers."""

    def __init__(self, subject: Subject) -> None:
        spec = importlib.util.spec_from_file_location("p1_u00_known_bad_source_io", subject.cli)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        self._module = module

    def snapshot(self, root: Path, path: Path, after_descriptor_bound: Callable[[str], None]) -> str:
        checked = self._module._safe_source_path(root, path.relative_to(root))
        original_open = os.open

        def observed_open(component, flags, *args, **kwargs):
            descriptor = original_open(component, flags, *args, **kwargs)
            if kwargs.get("dir_fd") is not None:
                after_descriptor_bound(str(component))
            return descriptor

        os.open = observed_open
        try:
            return self._module._read_source(root, checked)[1]
        finally:
            os.open = original_open

    def publish(self, root: Path, inventory: Path, before_exchange: Callable[[], None]) -> None:
        original_replace = os.replace
        fired = False

        def observed_replace(source, target, *args, **kwargs):
            nonlocal fired
            if not fired:
                fired = True
                before_exchange()
            return original_replace(source, target, *args, **kwargs)

        os.replace = observed_replace
        try:
            self._module.generate(root, inventory, fixture_filesystem=True)
        finally:
            os.replace = original_replace


class _CurrentSourceIoSubject:
    """Task 5 seam: map to public GitSourceSnapshot/InventoryPublisher there."""

    def snapshot(self, root: Path, path: Path, after_descriptor_bound: Callable[[str], None]) -> str:
        raise RuntimeError("current SourceIoSubject requires Task 5 public GitSourceSnapshot mapping")

    def publish(self, root: Path, inventory: Path, before_exchange: Callable[[], None]) -> None:
        raise RuntimeError("current SourceIoSubject requires Task 5 public InventoryPublisher mapping")


def source_io_subject(subject: Subject) -> SourceIoSubject:
    if subject.label == KNOWN_BAD_COMMIT:
        return _KnownBadSourceIoSubject(subject)
    return _CurrentSourceIoSubject()


def _git(*arguments: str) -> bytes:
    completed = subprocess.run(["git", *arguments], cwd=REPO_ROOT, capture_output=True, check=False)
    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")
    return completed.stdout


def _materialized_known_bad() -> Subject:
    """Materialize reviewed abaaeb6 bytes after commit/tree/blob/digest verification."""
    assert _git("rev-parse", f"{KNOWN_BAD_COMMIT}^{{commit}}").decode().strip() == KNOWN_BAD_COMMIT
    assert _git("rev-parse", f"{KNOWN_BAD_COMMIT}^{{tree}}").decode().strip() == KNOWN_BAD_TREE
    assert _git("rev-parse", f"{KNOWN_BAD_COMMIT}:scripts/inventory_nautilus_pins.py").decode().strip() == KNOWN_BAD_BLOB
    source = _git("show", f"{KNOWN_BAD_COMMIT}:scripts/inventory_nautilus_pins.py")
    assert hashlib.sha256(source).hexdigest() == KNOWN_BAD_SCRIPT_SHA256
    directory = TemporaryDirectory(prefix="p1-u00-abaaeb6-")
    cli = Path(directory.name) / "inventory_nautilus_pins.py"
    cli.write_bytes(source)
    subject = Subject(cli, label=KNOWN_BAD_COMMIT)
    subject._temporary_directory = directory  # type: ignore[attr-defined]
    return subject


def selected_subject() -> Subject:
    """Return the explicitly selected RED or GREEN command-line subject."""
    selected = os.environ.get(SUBJECT_ENV, "current")
    if selected == "abaaeb6":
        return _materialized_known_bad()
    if selected == "current":
        cli = REPO_ROOT / "scripts/inventory_nautilus_pins.py"
        assert cli.is_file(), "current recovery implementation is not yet available"
        return Subject(cli, label="current")
    raise AssertionError(f"unsupported {SUBJECT_ENV} subject: {selected}")


@pytest.fixture(scope="session")
def subject() -> Subject:
    return selected_subject()


def run_subject(subject: Subject, root: Path, inventory: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subject.run(root, inventory, *arguments)


def run_bad(root: Path, inventory: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Compatibility helper for RED-only controls; it binds exact Git bytes."""
    return _materialized_known_bad().run(root, inventory, *arguments)


def run_current(root: Path, inventory: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Normal GREEN execution targets the current recovery implementation."""
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts/inventory_nautilus_pins.py"), "--root", str(root), "--inventory", str(inventory), "--fixture-filesystem", *arguments],
        cwd=REPO_ROOT, text=True, capture_output=True, check=False,
    )


def fixture_root(tmp_path: Path, source: str, *, name: str = "surface.md") -> tuple[Path, Path, Path]:
    root = tmp_path / "fixture"
    root.mkdir()
    nautilus = root / "engines/nautilus"
    nautilus.mkdir(parents=True)
    (nautilus / "README.md").write_text(
        "engine-build-policy.json llvm-toolchain-policy.json wheel-cache-policy.json\n",
        encoding="utf-8",
    )
    (nautilus / "engine-build-policy.json").write_text(
        '{"engine_version":"1.227.0","upstream_commit":"280ae1762df51a492a4ce71506a40b5c8706def5","profile_manifest_schema_version":6}\n',
        encoding="utf-8",
    )
    (nautilus / "llvm-toolchain-policy.json").write_text('{}\n', encoding="utf-8")
    (nautilus / "wheel-cache-policy.json").write_text('{}\n', encoding="utf-8")
    surface = root / name
    surface.parent.mkdir(parents=True, exist_ok=True)
    surface.write_text(source, encoding="utf-8")
    return root, root / "pin-inventory.json", surface


def generate_baseline(subject: Subject, root: Path, inventory: Path) -> None:
    result = run_subject(subject, root, inventory, "--generate")
    assert result.returncode == 0, result.stderr


def assert_mutation_rejected(subject: Subject, root: Path, inventory: Path) -> None:
    result = run_subject(subject, root, inventory)
    assert result.returncode != 0, (
        "known-bad scanner returned a false green after an identity/custody mutation\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )


@pytest.fixture
def oracle():
    return required_identities
