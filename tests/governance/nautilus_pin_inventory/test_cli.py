"""Exact-commit command-line inventory controls using disposable Git repos."""

from __future__ import annotations

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
        return self.git("rev-parse", "HEAD")

    @property
    def head(self) -> str:
        return self.git("rev-parse", "HEAD")


def run_cli(repo: InventoryRepo, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(CLI), "--root", str(repo.root), *arguments], text=True, capture_output=True, check=False)


@pytest.mark.parametrize("object_format", ("sha1", "sha256"))
def test_generate_uses_an_exact_commit_and_leaves_git_state_unchanged(tmp_path: Path, object_format: str) -> None:
    """Break caught: CLI scans HEAD/worktree or writes index/ref state."""
    repo = InventoryRepo(tmp_path / object_format, object_format=object_format)
    source = repo.head
    output = repo.root / "out" / "inventory.json"
    output.parent.mkdir()
    before = (repo.head, repo.git("diff", "--cached", "--quiet"))

    result = run_cli(repo, "generate", "--source-commit", source, "--output", str(output))

    assert result.returncode == 0, result.stderr
    assert output.is_file()
    assert (repo.head, repo.git("diff", "--cached", "--quiet")) == before
    repo.write("evidence.txt", b"worktree drift\n")
    assert run_cli(repo, "generate", "--source-commit", source, "--output", str(repo.root / "again.json")).returncode == 0


def _inventory_commit(repo: InventoryRepo, source: str, output: Path) -> str:
    generated = run_cli(repo, "generate", "--source-commit", source, "--output", str(output))
    assert generated.returncode == 0, generated.stderr
    repo.git("add", "--", output.relative_to(repo.root).as_posix())
    repo.git("commit", "-qm", "inventory")
    return repo.head


def test_verify_requires_exact_parent_shape_and_exact_bytes(tmp_path: Path) -> None:
    """Break caught: verify accepts a mutable source, wrong range, or stale inventory bytes."""
    repo = InventoryRepo(tmp_path / "repo", object_format="sha1")
    source = repo.head
    inventory = repo.root / "pin-inventory.json"
    candidate = _inventory_commit(repo, source, inventory)

    valid = run_cli(repo, "verify", "--source-commit", source, "--inventory-commit", candidate, "--inventory-path", "pin-inventory.json")
    assert valid.returncode == 0, valid.stderr
    repo.write("extra.txt", b"extra\n")
    bad = repo.commit("extra")
    result = run_cli(repo, "verify", "--source-commit", source, "--inventory-commit", bad, "--inventory-path", "pin-inventory.json")
    assert result.returncode == 1


def test_cli_rejects_refs_abbreviations_tags_and_wrong_objects(tmp_path: Path) -> None:
    """Break caught: refs, abbreviations, or uppercase OIDs select authority."""
    repo = InventoryRepo(tmp_path / "repo", object_format="sha1")
    source = repo.head
    repo.git("branch", "source-ref", source)
    repo.git("tag", "source-tag", source)
    blob = repo.git("rev-parse", f"{source}:evidence.txt")
    for selector in ("HEAD", "source-ref", "source-tag", source[:8], source.upper(), blob):
        result = run_cli(repo, "generate", "--source-commit", selector, "--output", str(repo.root / f"{selector}.json"))
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


def test_verify_rejects_replacement_wrong_mode_and_merge_parent(tmp_path: Path) -> None:
    """Break caught: an inventory replacement, executable mode, or merge commit verifies."""
    repo = InventoryRepo(tmp_path / "repo", object_format="sha1")
    source = repo.head
    inventory = repo.root / "pin-inventory.json"

    inventory.write_bytes(b"{}\n")
    replacement = repo.commit("replacement")
    assert run_cli(repo, "verify", "--source-commit", source, "--inventory-commit", replacement, "--inventory-path", "pin-inventory.json").returncode == 1

    repo = InventoryRepo(tmp_path / "mode", object_format="sha1")
    source = repo.head
    inventory = repo.root / "pin-inventory.json"
    generated = run_cli(repo, "generate", "--source-commit", source, "--output", str(inventory))
    assert generated.returncode == 0, generated.stderr
    inventory.chmod(0o755)
    wrong_mode = repo.commit("wrong mode")
    assert run_cli(repo, "verify", "--source-commit", source, "--inventory-commit", wrong_mode, "--inventory-path", "pin-inventory.json").returncode == 1

    repo = InventoryRepo(tmp_path / "merge", object_format="sha1")
    source = repo.head
    branch = repo.git("branch", "--show-current")
    repo.git("checkout", "-qb", "side")
    repo.write("side.txt", b"side\n")
    repo.commit("side")
    repo.git("checkout", "-q", branch)
    inventory = repo.root / "pin-inventory.json"
    generated = run_cli(repo, "generate", "--source-commit", source, "--output", str(inventory))
    assert generated.returncode == 0, generated.stderr
    repo.commit("inventory")
    repo.git("merge", "--no-ff", "-qm", "merge inventory", "side")
    assert run_cli(repo, "verify", "--source-commit", source, "--inventory-commit", repo.head, "--inventory-path", "pin-inventory.json").returncode == 1
