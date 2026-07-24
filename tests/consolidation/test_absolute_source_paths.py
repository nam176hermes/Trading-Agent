from __future__ import annotations

import subprocess
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[2]
COMPONENT_ROOTS = ("apps/dashboard/", "legacy/research-backend/")
SOURCE_SUFFIXES = {
    ".cjs",
    ".js",
    ".json",
    ".mjs",
    ".py",
    ".sh",
    ".toml",
    ".ts",
    ".tsx",
    ".yaml",
    ".yml",
}
SOURCE_NAMES = {"AGENTS.md", "Makefile", "README.md"}
HISTORICAL_PREFIXES = (
    ".superpowers/",
    "apps/dashboard/audit/",
    "docs/implementation/",
    "legacy/research-backend/audit/",
)
HISTORICAL_FILES = {
    "docs/superpowers/plans/2026-07-13-canonical-source-consolidation.md",
}
FORBIDDEN_SOURCE_PATHS = (
    b".hermes",
    b"/home/thenam176/" + b".hermes",
    b"~/" + b".hermes",
    b".local/share/" + b"codex-worktrees",
    b"/home/thenam176/projects/" + b"trading-dashboard",
    b"/home/thenam176/projects/" + b"trading-agent-migration",
)


def _contains_legacy_source_path(content: bytes) -> bool:
    return any(marker in content for marker in FORBIDDEN_SOURCE_PATHS)


def _tracked_paths() -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z"],
        check=True,
        stdout=subprocess.PIPE,
    )
    return [
        raw.decode("utf-8")
        for raw in result.stdout.split(b"\0")
        if raw
    ]


def _is_current_component_source(relative: str) -> bool:
    if not relative.startswith(COMPONENT_ROOTS):
        return False
    if relative in HISTORICAL_FILES or relative.startswith(HISTORICAL_PREFIXES):
        return False
    path = PurePosixPath(relative)
    return path.suffix in SOURCE_SUFFIXES or path.name in SOURCE_NAMES


def test_tracked_component_sources_do_not_name_legacy_source_checkouts() -> None:
    failures: list[str] = []
    for relative in _tracked_paths():
        if not _is_current_component_source(relative):
            continue
        content = (ROOT / relative).read_bytes()
        if _contains_legacy_source_path(content):
            failures.append(relative)

    assert failures == [], "legacy source checkout paths:\n" + "\n".join(failures)


def test_scan_rejects_split_hermes_constructions_and_json_config() -> None:
    adversarial_sources = (
        b"path.join(home, '.hermes', 'crypto-research')",
        b"Path.home() / '.hermes' / 'crypto-research'",
        b'{"runtime_root":".hermes/crypto-research"}',
    )

    assert all(_contains_legacy_source_path(source) for source in adversarial_sources)
