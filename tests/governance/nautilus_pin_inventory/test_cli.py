"""Exact-commit command-line inventory controls using disposable Git repos."""

from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.nautilus_pin_inventory.registry import DEFAULT_REGISTRY
from test_git_source import GitFixture


CLI = Path(__file__).resolve().parents[3] / "scripts" / "inventory_nautilus_pins.py"


class InventoryRepo(GitFixture):
    """Small real repository containing a complete, engine-valid source tree."""

    def __init__(self, root: Path, *, object_format: str) -> None:
        super().__init__(root, object_format=object_format)
        for name in (
            "README.md", "engine-build-policy.json", "runtime-closure-policy.json",
            "llvm-toolchain-policy.json", "wheel-cache-policy.json",
        ):
            self.write(f"engines/nautilus/{name}", (
                b"engine-build-policy.json runtime-closure-policy.json llvm-toolchain-policy.json wheel-cache-policy.json\n"
                if name == "README.md" else b"{}\n"
            ))
        self.write("evidence.txt", "".join(
            f"Nautilus {identity.family}: {identity.value}\n"
            for identity in DEFAULT_REGISTRY.allowed_identities
        ).encode())
        self.git("add", ".")
        self.git("commit", "-qm", "source")

    def git(self, *arguments: str) -> str:
        return self._git(*arguments).decode().strip()

    def write(self, path: str, data: bytes) -> Path:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target

    def commit(self, message: str) -> str:
        self.git("add", ".")
        self.git("commit", "-qm", message)
        return self.head

    @property
    def head(self) -> str:
        return self.git("rev-parse", "HEAD")


def run_cli(repo: InventoryRepo, command: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), command, "--root", str(repo.root), *arguments],
        text=True, capture_output=True, check=False,
    )


def _custody(repo: InventoryRepo) -> tuple[tuple[str, ...], str, str, str]:
    refs = tuple(repo.git("for-each-ref", "--format=%(refname) %(objectname) %(objecttype)").splitlines())
    index = (repo.root / ".git/index").read_bytes()
    return refs, hashlib.sha256("\n".join(refs).encode()).hexdigest(), hashlib.sha256(index).hexdigest(), repo.head


def _inventory_commit(repo: InventoryRepo, source: str, output: Path) -> str:
    output.parent.mkdir(parents=True, exist_ok=True)
    generated = run_cli(repo, "generate", "--source-commit", source, "--output", str(output))
    assert generated.returncode == 0, generated.stderr
    repo.git("add", "--", output.relative_to(repo.root).as_posix())
    repo.git("commit", "-qm", "inventory")
    return repo.head


@pytest.mark.parametrize("object_format", ("sha1", "sha256"))
def test_documented_generate_grammar_uses_exact_commit_and_preserves_git_custody(tmp_path: Path, object_format: str) -> None:
    """Break caught: legacy argument order, mutable source, ref, or raw-index write is accepted."""
    repo = InventoryRepo(tmp_path / object_format, object_format=object_format)
    source = repo.head
    output = repo.root / "out" / "inventory.json"
    output.parent.mkdir()
    before = _custody(repo)

    result = run_cli(repo, "generate", "--source-commit", source, "--output", str(output))

    assert result.returncode == 0, result.stderr
    assert output.is_file()
    assert _custody(repo) == before
    legacy = subprocess.run(
        [sys.executable, str(CLI), "--root", str(repo.root), "generate", "--source-commit", source, "--output", str(repo.root / "legacy.json")],
        text=True, capture_output=True, check=False,
    )
    assert legacy.returncode == 2
    repo.write("evidence.txt", b"worktree drift\n")
    assert run_cli(repo, "generate", "--source-commit", source, "--output", str(repo.root / "again.json")).returncode == 0


@pytest.mark.parametrize("object_format", ("sha1", "sha256"))
def test_documented_verify_grammar_is_sha_format_exact_and_worktree_independent(tmp_path: Path, object_format: str) -> None:
    """Break caught: verify accepts the wrong format, reads worktree paths, or rewrites custody."""
    repo = InventoryRepo(tmp_path / object_format, object_format=object_format)
    source = repo.head
    inventory = repo.root / "nested/pins/inventory.json"
    candidate = _inventory_commit(repo, source, inventory)
    before = _custody(repo)

    valid = run_cli(repo, "verify", "--source-commit", source, "--inventory-commit", candidate, "--inventory-path", "nested//pins/./inventory.json")

    assert valid.returncode == 0, valid.stderr
    assert _custody(repo) == before
    moved = repo.root / "nested-real"
    (repo.root / "nested").rename(moved)
    outside = tmp_path / "outside"
    outside.mkdir()
    (repo.root / "nested").symlink_to(outside, target_is_directory=True)
    drift = run_cli(repo, "verify", "--source-commit", source, "--inventory-commit", candidate, "--inventory-path", "nested/pins/inventory.json")
    assert drift.returncode == 0, drift.stderr


def test_cli_rejects_refs_tags_trees_wrong_objects_and_widths_for_both_formats(tmp_path: Path) -> None:
    """Break caught: anything other than a full lowercase commit object selects authority."""
    for object_format, width in (("sha1", 40), ("sha256", 64)):
        repo = InventoryRepo(tmp_path / object_format, object_format=object_format)
        source = repo.head
        repo.git("branch", "source-ref", source)
        repo.git("tag", "source-tag", source)
        blob = repo.git("rev-parse", f"{source}:evidence.txt")
        tree = repo.git("rev-parse", f"{source}^{{tree}}")
        for selector in ("HEAD", "source-ref", "source-tag", source[:8], source.upper(), blob, tree, "a" * width, "a" * (width - 1), "a" * (width + 1)):
            result = run_cli(repo, "generate", "--source-commit", selector, "--output", str(repo.root / "out.json"))
            assert result.returncode == 2
            assert "PIN_INVENTORY_USAGE" in result.stderr


def test_generate_classifies_unknown_and_missing_source_identities_as_stale(tmp_path: Path) -> None:
    """Break caught: governed source drift is reported as CLI execution failure."""
    repo = InventoryRepo(tmp_path / "unknown", object_format="sha1")
    repo.write("evidence.txt", (repo.root / "evidence.txt").read_bytes() + b"Nautilus engine_version: 9.999.0\n")
    unknown = repo.commit("unknown")
    result = run_cli(repo, "generate", "--source-commit", unknown, "--output", str(repo.root / "unknown.json"))
    assert result.returncode == 1
    assert "PIN_INVENTORY_STALE: unregistered governed identity: engine_version=9.999.0" in result.stderr

    repo = InventoryRepo(tmp_path / "missing", object_format="sha1")
    required = next(identity for identity in DEFAULT_REGISTRY.allowed_identities if identity.family == "engine_version" and identity.value == "1.227.0")
    repo.write("evidence.txt", "".join(
        f"Nautilus {identity.family}: {identity.value}\n"
        for identity in DEFAULT_REGISTRY.allowed_identities if identity != required
    ).encode())
    missing = repo.commit("missing")
    result = run_cli(repo, "generate", "--source-commit", missing, "--output", str(repo.root / "missing.json"))
    assert result.returncode == 1
    assert "PIN_INVENTORY_STALE: required identity is missing: engine_version=1.227.0" in result.stderr


def test_generate_keeps_malformed_source_as_usage_error(tmp_path: Path) -> None:
    """Break caught: malformed source/schema failures are incorrectly called stale inventory."""
    repo = InventoryRepo(tmp_path / "repo", object_format="sha1")
    repo.write("engines/nautilus/engine-build-policy.json", b"{not-json}\n")
    malformed = repo.commit("malformed")
    result = run_cli(repo, "generate", "--source-commit", malformed, "--output", str(repo.root / "bad.json"))
    assert result.returncode == 2
    assert "PIN_INVENTORY_USAGE" in result.stderr


def test_generate_rejects_source_inventory_and_output_collision(tmp_path: Path) -> None:
    """Break caught: the generated file becomes source authority or overwrites output."""
    repo = InventoryRepo(tmp_path / "repo", object_format="sha1")
    source = repo.head
    collision = repo.root / "collision.json"
    collision.write_bytes(b"keep")
    result = run_cli(repo, "generate", "--source-commit", source, "--output", str(collision))
    assert result.returncode == 1
    assert collision.read_bytes() == b"keep"
    repo.write("docs/implementation/p1-real-nautilus/upgrade/pin-inventory.json", b"{}\n")
    with_inventory = repo.commit("source inventory")
    result = run_cli(repo, "generate", "--source-commit", with_inventory, "--output", str(repo.root / "new.json"))
    assert result.returncode == 1


def test_generate_rejects_dangling_and_looped_output_symlinks_without_writes(tmp_path: Path) -> None:
    """Break caught: resolving an existing output link bypasses collision or leaks a traceback."""
    repo = InventoryRepo(tmp_path / "repo", object_format="sha1")
    source = repo.head
    target = repo.root / "unexpected-target.json"
    dangling = repo.root / "dangling.json"
    dangling.symlink_to(target)
    result = run_cli(repo, "generate", "--source-commit", source, "--output", str(dangling))
    assert result.returncode == 1
    assert dangling.is_symlink()
    assert not target.exists()

    loop = repo.root / "loop.json"
    loop.symlink_to(loop.name)
    result = run_cli(repo, "generate", "--source-commit", source, "--output", str(loop))
    assert result.returncode == 2
    assert "PIN_INVENTORY_USAGE" in result.stderr
    assert "Traceback" not in result.stderr


def test_verify_classifies_schema_and_stale_bytes_separately(tmp_path: Path) -> None:
    """Break caught: invalid schema and valid-but-stale inventory bytes share one exit class."""
    for name, payload, expected in (("schema", b"{not-json}\n", 2), ("stale", b"{}\n", 1)):
        repo = InventoryRepo(tmp_path / name, object_format="sha1")
        source = repo.head
        repo.write("pin-inventory.json", payload)
        candidate = repo.commit(name)
        result = run_cli(repo, "verify", "--source-commit", source, "--inventory-commit", candidate, "--inventory-path", "pin-inventory.json")
        assert result.returncode == expected


def test_verify_rejects_extra_range_replacement_wrong_mode_and_merge_parent(tmp_path: Path) -> None:
    """Break caught: a non-one-addition range, replacement, executable mode, or merge verifies."""
    repo = InventoryRepo(tmp_path / "extra", object_format="sha1")
    source = repo.head
    candidate = _inventory_commit(repo, source, repo.root / "pin-inventory.json")
    repo.write("extra.txt", b"extra\n")
    assert run_cli(repo, "verify", "--source-commit", source, "--inventory-commit", repo.commit("extra"), "--inventory-path", "pin-inventory.json").returncode == 1

    repo = InventoryRepo(tmp_path / "replacement", object_format="sha1")
    source = repo.head
    repo.write("pin-inventory.json", b"{}\n")
    assert run_cli(repo, "verify", "--source-commit", source, "--inventory-commit", repo.commit("replacement"), "--inventory-path", "pin-inventory.json").returncode == 1

    repo = InventoryRepo(tmp_path / "mode", object_format="sha1")
    source = repo.head
    inventory = repo.root / "pin-inventory.json"
    assert run_cli(repo, "generate", "--source-commit", source, "--output", str(inventory)).returncode == 0
    inventory.chmod(0o755)
    assert run_cli(repo, "verify", "--source-commit", source, "--inventory-commit", repo.commit("wrong mode"), "--inventory-path", "pin-inventory.json").returncode == 1

    repo = InventoryRepo(tmp_path / "merge", object_format="sha1")
    source = repo.head
    branch = repo.git("branch", "--show-current")
    repo.git("checkout", "-qb", "side")
    repo.write("side.txt", b"side\n")
    repo.commit("side")
    repo.git("checkout", "-q", branch)
    _inventory_commit(repo, source, repo.root / "pin-inventory.json")
    repo.git("merge", "--no-ff", "-qm", "merge inventory", "side")
    assert run_cli(repo, "verify", "--source-commit", source, "--inventory-commit", repo.head, "--inventory-path", "pin-inventory.json").returncode == 1
