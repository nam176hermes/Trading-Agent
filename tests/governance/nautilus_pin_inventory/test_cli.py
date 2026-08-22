"""Exact-commit command-line inventory controls using disposable Git repos."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.nautilus_pin_inventory.registry import DEFAULT_REGISTRY
from scripts.nautilus_pin_inventory.engine import INVENTORY_PATH
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
        (self.root / INVENTORY_PATH).parent.mkdir(parents=True, exist_ok=True)
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


def _inventory_commit(repo: InventoryRepo, source: str) -> str:
    output = repo.root / INVENTORY_PATH
    output.parent.mkdir(parents=True, exist_ok=True)
    generated = run_cli(repo, "generate", "--source-commit", source, "--output", INVENTORY_PATH)
    assert generated.returncode == 0, generated.stderr
    repo.git("add", "--", INVENTORY_PATH)
    repo.git("commit", "-qm", "inventory")
    return repo.head


@pytest.mark.parametrize("object_format", ("sha1", "sha256"))
def test_documented_generate_grammar_uses_exact_commit_and_preserves_git_custody(tmp_path: Path, object_format: str) -> None:
    """Break caught: legacy argument order, mutable source, ref, or raw-index write is accepted."""
    repo = InventoryRepo(tmp_path / object_format, object_format=object_format)
    source = repo.head
    output = repo.root / INVENTORY_PATH
    output.parent.mkdir(parents=True, exist_ok=True)
    before = _custody(repo)

    result = run_cli(repo, "generate", "--source-commit", source, "--output", INVENTORY_PATH)

    assert result.returncode == 0, result.stderr
    assert output.is_file()
    assert _custody(repo) == before
    legacy = subprocess.run(
        [sys.executable, str(CLI), "--root", str(repo.root), "generate", "--source-commit", source, "--output", str(repo.root / "legacy.json")],
        text=True, capture_output=True, check=False,
    )
    assert legacy.returncode == 2
    repo.write("evidence.txt", b"worktree drift\n")


def test_cli_inventory_path_is_fixed_before_git_or_filesystem_work(tmp_path: Path) -> None:
    """Break caught: a caller moves authority to an absolute, outside, or alternate path."""
    repo = InventoryRepo(tmp_path / "repo", object_format="sha1")
    source = repo.head
    outside = tmp_path / "outside.json"
    (repo.root / INVENTORY_PATH).parent.mkdir(parents=True, exist_ok=True)
    for output in (str(repo.root / INVENTORY_PATH), str(outside), "pin-inventory.json", "docs/other.json"):
        result = run_cli(repo, "generate", "--source-commit", source, "--output", output)
        assert result.returncode == 2
        assert not outside.exists()
    assert run_cli(repo, "generate", "--source-commit", source, "--output", INVENTORY_PATH).returncode == 0


def test_generate_rejects_intermediate_output_parent_symlink_before_git_access(tmp_path: Path) -> None:
    """Break caught: a fixed lexical inventory path follows a worktree directory link outside root."""
    repo = InventoryRepo(tmp_path / "repo", object_format="sha1")
    outside = tmp_path / "outside"
    outside.mkdir()
    parent = (repo.root / INVENTORY_PATH).parent
    parent.rmdir()
    parent.symlink_to(outside, target_is_directory=True)
    before = _custody(repo)

    result = run_cli(repo, "generate", "--source-commit", "not-a-commit", "--output", INVENTORY_PATH)

    assert result.returncode == 2
    assert "output parent is not a regular directory" in result.stderr
    assert not (outside / "pin-inventory.json").exists()
    assert _custody(repo) == before


@pytest.mark.parametrize(
    ("command", "arguments"),
    (
        ("generate", ("--source-commit", "a" * 40, "--output", "pin-inventory.json")),
        ("verify", ("--source-commit", "a" * 40, "--inventory-commit", "b" * 40, "--inventory-path", "pin-inventory.json")),
    ),
)
def test_cli_rejects_alternate_inventory_path_before_root_access(
    tmp_path: Path, command: str, arguments: tuple[str, ...],
) -> None:
    """Break caught: root/Git access happens before the fixed authority path is checked."""
    result = subprocess.run(
        [sys.executable, str(CLI), command, "--root", str(tmp_path / "absent"), *arguments],
        text=True, capture_output=True, check=False,
    )
    assert result.returncode == 2
    assert "inventory path must be the required inventory path" in result.stderr


@pytest.mark.parametrize("object_format", ("sha1", "sha256"))
def test_documented_verify_grammar_is_sha_format_exact_and_worktree_independent(tmp_path: Path, object_format: str) -> None:
    """Break caught: verify accepts the wrong format, reads worktree paths, or rewrites custody."""
    repo = InventoryRepo(tmp_path / object_format, object_format=object_format)
    source = repo.head
    candidate = _inventory_commit(repo, source)
    before = _custody(repo)

    valid = run_cli(repo, "verify", "--source-commit", source, "--inventory-commit", candidate, "--inventory-path", INVENTORY_PATH)

    assert valid.returncode == 0, valid.stderr
    assert _custody(repo) == before
    for path in (str(repo.root / INVENTORY_PATH), "pin-inventory.json", "docs/other.json"):
        assert run_cli(repo, "verify", "--source-commit", source, "--inventory-commit", candidate, "--inventory-path", path).returncode == 2


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
            result = run_cli(repo, "generate", "--source-commit", selector, "--output", INVENTORY_PATH)
            assert result.returncode == 2
            assert "PIN_INVENTORY_USAGE" in result.stderr


def test_generate_classifies_unknown_and_missing_source_identities_as_stale(tmp_path: Path) -> None:
    """Break caught: governed source drift is reported as CLI execution failure."""
    repo = InventoryRepo(tmp_path / "unknown", object_format="sha1")
    repo.write("evidence.txt", (repo.root / "evidence.txt").read_bytes() + b"Nautilus engine_version: 9.999.0\n")
    unknown = repo.commit("unknown")
    result = run_cli(repo, "generate", "--source-commit", unknown, "--output", INVENTORY_PATH)
    assert result.returncode == 1
    assert "PIN_INVENTORY_STALE: unregistered governed identity: engine_version=9.999.0" in result.stderr

    repo = InventoryRepo(tmp_path / "missing", object_format="sha1")
    required = next(identity for identity in DEFAULT_REGISTRY.allowed_identities if identity.family == "engine_version" and identity.value == "1.227.0")
    repo.write("evidence.txt", "".join(
        f"Nautilus {identity.family}: {identity.value}\n"
        for identity in DEFAULT_REGISTRY.allowed_identities if identity != required
    ).encode())
    missing = repo.commit("missing")
    result = run_cli(repo, "generate", "--source-commit", missing, "--output", INVENTORY_PATH)
    assert result.returncode == 1
    assert "PIN_INVENTORY_STALE: required identity is missing: engine_version=1.227.0" in result.stderr


def test_generate_keeps_malformed_source_as_usage_error(tmp_path: Path) -> None:
    """Break caught: malformed source/schema failures are incorrectly called stale inventory."""
    repo = InventoryRepo(tmp_path / "repo", object_format="sha1")
    repo.write("engines/nautilus/engine-build-policy.json", b"{not-json}\n")
    malformed = repo.commit("malformed")
    result = run_cli(repo, "generate", "--source-commit", malformed, "--output", INVENTORY_PATH)
    assert result.returncode == 2
    assert "PIN_INVENTORY_USAGE" in result.stderr


def test_generate_rejects_source_inventory_and_output_collision(tmp_path: Path) -> None:
    """Break caught: the generated file becomes source authority or overwrites output."""
    repo = InventoryRepo(tmp_path / "repo", object_format="sha1")
    source = repo.head
    collision = repo.root / INVENTORY_PATH
    collision.parent.mkdir(parents=True, exist_ok=True)
    collision.write_bytes(b"keep")
    result = run_cli(repo, "generate", "--source-commit", source, "--output", INVENTORY_PATH)
    assert result.returncode == 1
    assert collision.read_bytes() == b"keep"
    repo.write("docs/implementation/p1-real-nautilus/upgrade/pin-inventory.json", b"{}\n")
    with_inventory = repo.commit("source inventory")
    result = run_cli(repo, "generate", "--source-commit", with_inventory, "--output", INVENTORY_PATH)
    assert result.returncode == 1


def test_generate_rejects_dangling_and_looped_output_symlinks_without_writes(tmp_path: Path) -> None:
    """Break caught: resolving an existing output link bypasses collision or leaks a traceback."""
    repo = InventoryRepo(tmp_path / "repo", object_format="sha1")
    source = repo.head
    target = repo.root / "unexpected-target.json"
    dangling = repo.root / INVENTORY_PATH
    dangling.parent.mkdir(parents=True, exist_ok=True)
    dangling.symlink_to(target)
    result = run_cli(repo, "generate", "--source-commit", source, "--output", INVENTORY_PATH)
    assert result.returncode == 1
    assert dangling.is_symlink()
    assert not target.exists()

    dangling.unlink()
    loop = repo.root / INVENTORY_PATH
    loop.symlink_to(loop.name)
    result = run_cli(repo, "generate", "--source-commit", source, "--output", INVENTORY_PATH)
    assert result.returncode == 2
    assert "PIN_INVENTORY_USAGE" in result.stderr
    assert "Traceback" not in result.stderr


def test_verify_classifies_schema_and_stale_bytes_separately(tmp_path: Path) -> None:
    """Break caught: invalid schema and valid-but-stale inventory bytes share one exit class."""
    mutations = (
        ("unknown-top-level", lambda value: value.update(unexpected=True), 2),
        ("missing-entry-field", lambda value: value["entries"][0].pop("id"), 2),
        ("wrong-span-type", lambda value: value["entries"][0]["spans"][0].update(start_line=True), 2),
        ("invalid-id", lambda value: value["entries"][0].update(id="PIN-not-hex"), 2),
        ("malformed-nested-record", lambda value: value["entries"][0].update(spans=[{}]), 2),
        ("stale-valid", lambda value: value.update(source_tree_oid="0" * 40), 1),
    )
    for name, mutate, expected in mutations:
        repo = InventoryRepo(tmp_path / name, object_format="sha1")
        source = repo.head
        target = repo.root / INVENTORY_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        assert run_cli(repo, "generate", "--source-commit", source, "--output", INVENTORY_PATH).returncode == 0
        payload = json.loads(target.read_text(encoding="utf-8"))
        mutate(payload)
        target.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        candidate = repo.commit(name)
        result = run_cli(repo, "verify", "--source-commit", source, "--inventory-commit", candidate, "--inventory-path", INVENTORY_PATH)
        assert result.returncode == expected


def test_verify_rejects_extra_range_replacement_wrong_mode_and_merge_parent(tmp_path: Path) -> None:
    """Break caught: a non-one-addition range, replacement, executable mode, or merge verifies."""
    repo = InventoryRepo(tmp_path / "extra", object_format="sha1")
    source = repo.head
    candidate = _inventory_commit(repo, source)
    repo.write("extra.txt", b"extra\n")
    assert run_cli(repo, "verify", "--source-commit", source, "--inventory-commit", repo.commit("extra"), "--inventory-path", INVENTORY_PATH).returncode == 1

    repo = InventoryRepo(tmp_path / "replacement", object_format="sha1")
    source = repo.head
    repo.write(INVENTORY_PATH, b"{}\n")
    assert run_cli(repo, "verify", "--source-commit", source, "--inventory-commit", repo.commit("replacement"), "--inventory-path", INVENTORY_PATH).returncode == 2

    repo = InventoryRepo(tmp_path / "mode", object_format="sha1")
    source = repo.head
    inventory = repo.root / INVENTORY_PATH
    inventory.parent.mkdir(parents=True, exist_ok=True)
    assert run_cli(repo, "generate", "--source-commit", source, "--output", INVENTORY_PATH).returncode == 0
    inventory.chmod(0o755)
    assert run_cli(repo, "verify", "--source-commit", source, "--inventory-commit", repo.commit("wrong mode"), "--inventory-path", INVENTORY_PATH).returncode == 1

    repo = InventoryRepo(tmp_path / "merge", object_format="sha1")
    source = repo.head
    branch = repo.git("branch", "--show-current")
    repo.git("checkout", "-qb", "side")
    repo.write("side.txt", b"side\n")
    repo.commit("side")
    repo.git("checkout", "-q", branch)
    _inventory_commit(repo, source)
    repo.git("merge", "--no-ff", "-qm", "merge inventory", "side")
    assert run_cli(repo, "verify", "--source-commit", source, "--inventory-commit", repo.head, "--inventory-path", INVENTORY_PATH).returncode == 1
