from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

from scripts import prepare_runtime_release_wheelhouse as preparation


def test_repository_git_metadata_accepts_directory_and_worktree_file(tmp_path: Path) -> None:
    standalone = tmp_path / "standalone"
    standalone.mkdir()
    (standalone / ".git").mkdir()
    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / ".git").write_text("gitdir: /safe/external/gitdir\n", encoding="utf-8")
    unsafe = tmp_path / "unsafe"
    unsafe.mkdir()
    (unsafe / ".git").symlink_to(standalone / ".git", target_is_directory=True)

    assert preparation._repository_has_git_metadata(standalone)
    assert preparation._repository_has_git_metadata(linked)
    assert not preparation._repository_has_git_metadata(unsafe)


def test_preparation_pins_pip_tool_to_validated_python(
    tmp_path: Path, monkeypatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    lock = repo / "uv.lock"
    lock.write_text("version = 1\n", encoding="utf-8")
    destination = tmp_path / hashlib.sha256(lock.read_bytes()).hexdigest()
    target_python = "/approved/cpython-3.11/bin/python"
    commands: list[list[str]] = []

    def fake_run(
        command: list[str], *, cwd: Path, env: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        stdout = ""
        if command[:3] == ["git", "rev-parse", "--show-toplevel"]:
            stdout = f"{repo}\n"
        elif command[:3] == [target_python, "-I", "-c"]:
            stdout = "CPython 3.11.15\n"
        elif command[:2] == ["fixture-uv", "export"]:
            output = Path(command[command.index("--output-file") + 1])
            output.write_text("demo==1.0\n", encoding="utf-8")
        elif command[:3] == ["fixture-uv", "tool", "run"]:
            if command[-2:] == ["pip", "--version"]:
                stdout = "pip 25.1.1 from fixture\n"
            else:
                wheelhouse = Path(command[command.index("--dest") + 1])
                (wheelhouse / "demo-1.0-py3-none-any.whl").write_bytes(b"fixture")
        elif command[:4] == [target_python, "-m", "venv", "--without-pip"]:
            probe = Path(command[-1])
            (probe / "bin").mkdir(parents=True)
            (probe / "bin/python").write_bytes(b"fixture")
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    digest = "a" * 64

    def fake_write_manifest(wheelhouse: Path, *_args, **_kwargs) -> Path:
        manifest = wheelhouse / preparation.MANIFEST_NAME
        manifest.write_text(
            json.dumps({"aggregate_sha256": digest, "artifacts": ["fixture"]}),
            encoding="utf-8",
        )
        return manifest

    monkeypatch.setattr(preparation, "_run", fake_run)
    monkeypatch.setattr(preparation, "write_wheelhouse_manifest", fake_write_manifest)
    monkeypatch.setattr(preparation, "verify_offline_wheelhouse", lambda *_args: digest)

    result = preparation._prepare(
        repo,
        destination,
        python=target_python,
        uv="fixture-uv",
    )

    tool_commands = [
        command
        for command in commands
        if command[:3] == ["fixture-uv", "tool", "run"]
    ]
    assert len(tool_commands) == 2
    for command in tool_commands:
        assert command[3:9] == [
            "--python",
            target_python,
            "--no-python-downloads",
            "--from",
            "pip==25.1.1",
            "pip",
        ]
    assert result == {"aggregate_sha256": digest, "artifacts": 1, "reused": False}
