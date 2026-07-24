from __future__ import annotations

import hashlib
import fcntl
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event

import pytest

import scripts.build_phase4_semantic_manifest as builder


BACKEND_COMMIT = "a" * 40


@pytest.fixture
def secure_tmp_path(monkeypatch):
    path = Path(tempfile.mkdtemp(prefix="phase4-semantic-builder-", dir="/home/thenam176/.cache"))
    path.chmod(0o700)
    monkeypatch.setattr(builder, "ROOT_AUTHORITY_UID", os.geteuid(), raising=False)
    monkeypatch.setattr(builder, "ROOT_AUTHORITY_GID", os.getegid(), raising=False)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _parents(root: Path) -> tuple[Path, Path]:
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    inputs = root / "research-input"
    authority = root / "authority"
    inputs.mkdir(mode=0o700)
    authority.mkdir(mode=0o700)
    lock = authority / ".phase4-v1.json.lock"
    lock.write_bytes(b"")
    lock.chmod(0o600)
    return inputs, authority / "phase4-v1.json"


def _sources(root: Path, marker: str = "one") -> dict[str, Path]:
    source = root / f"explicit-{marker}"
    source.mkdir(parents=True, mode=0o700)
    result = {}
    for index, logical_name in enumerate(builder.LOGICAL_DESTINATIONS):
        path = source / f"{logical_name}.json"
        path.write_text(json.dumps({"logical": logical_name, "marker": marker, "index": index}))
        path.chmod(0o600)
        result[logical_name] = path
    (source / ".env").write_text("EXCHANGE_SECRET=never-copy\n")
    (source / ".mode").write_text("live\n")
    (source / ".kill_switch").write_text("ACTIVE\n")
    return result


def _kwargs(root: Path, marker: str = "one", minute: int = 0):
    destination, authority = _parents(root)
    return {
        "sources": _sources(root, marker),
        "destination_root": destination,
        "manifest_path": authority,
        "manifest_version": f"snapshot-20260712T12{minute:02d}00Z-{marker}",
        "backend_commit": BACKEND_COMMIT,
        "runtime_uid": os.geteuid(),
        "runtime_gid": os.getegid(),
        "generated_at": datetime(2026, 7, 12, 12, minute, tzinfo=timezone.utc),
        "validity_minutes": 30,
    }


def _apply(kwargs, monkeypatch, *, clock=None):
    plan = builder.build_semantic_manifest(**kwargs)
    monkeypatch.setattr(builder.os, "geteuid", lambda: 0)
    return builder.build_semantic_manifest(
        **kwargs,
        apply=True,
        approved_plan_digest=plan.plan_digest,
        clock=clock or (lambda: kwargs["generated_at"] + timedelta(minutes=1)),
    )


def test_dry_run_is_reusable_and_apply_requires_exact_approved_plan(
    secure_tmp_path, monkeypatch,
):
    kwargs = _kwargs(secure_tmp_path)
    plan = builder.build_semantic_manifest(**kwargs)
    assert plan.applied is False
    assert len(plan.plan_digest) == 64
    assert not kwargs["manifest_path"].exists()
    assert list(kwargs["destination_root"].iterdir()) == []

    monkeypatch.setattr(builder.os, "geteuid", lambda: 0)
    with pytest.raises(builder.SemanticManifestBuildError, match="approved dry-run plan"):
        builder.build_semantic_manifest(**kwargs, apply=True)
    kwargs["sources"]["macro_report"].write_text('{"changed":true}')
    with pytest.raises(builder.SemanticManifestBuildError, match="plan digest"):
        builder.build_semantic_manifest(
            **kwargs,
            apply=True,
            approved_plan_digest=plan.plan_digest,
            clock=lambda: kwargs["generated_at"] + timedelta(minutes=1),
        )


def test_expected_selected_source_attestation_must_match_actual_consumed_file(secure_tmp_path):
    kwargs = _kwargs(secure_tmp_path)
    attestations = {
        name: (path.stat().st_dev, path.stat().st_ino, path.stat().st_size, hashlib.sha256(path.read_bytes()).hexdigest())
        for name, path in kwargs["sources"].items()
    }
    path = kwargs["sources"]["macro_report"]
    path.write_text('{"same_inode":"mutated after selection"}')

    with pytest.raises(builder.SemanticManifestBuildError):
        builder.build_semantic_manifest(
            **kwargs, expected_source_attestations=attestations,
        )
    assert not kwargs["manifest_path"].exists()


def test_publish_sets_complete_versioned_policy_and_exact_attestation(
    secure_tmp_path, monkeypatch,
):
    kwargs = _kwargs(secure_tmp_path)
    result = _apply(kwargs, monkeypatch)
    active = json.loads(kwargs["manifest_path"].read_text())
    manifest_path = kwargs["manifest_path"].parent / active["manifest_path"]
    plan_path = kwargs["manifest_path"].parent / active["plan_path"]
    manifest = json.loads(manifest_path.read_text())
    archived_plan_raw = plan_path.read_bytes()
    archived_plan = json.loads(archived_plan_raw)
    version_root = kwargs["destination_root"] / active["input_directory"]

    assert result.applied and result.plan_digest == active["plan_digest"]
    assert active == {
        "schema_version": 1,
        "classification": "READ_ONLY_EXTERNAL_INPUT",
        "generated_at": kwargs["generated_at"].isoformat(),
        "manifest_version": kwargs["manifest_version"],
        "manifest_path": manifest_path.name,
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "input_directory": version_root.name,
        "plan_digest": result.plan_digest,
        "plan_path": plan_path.name,
        "plan_sha256": hashlib.sha256(archived_plan_raw).hexdigest(),
    }
    assert stat.S_IMODE(kwargs["manifest_path"].stat().st_mode) == 0o444
    assert stat.S_IMODE(manifest_path.stat().st_mode) == 0o444
    assert stat.S_IMODE(plan_path.stat().st_mode) == 0o444
    assert archived_plan_raw.endswith(b"\n")
    assert archived_plan_raw == (
        json.dumps(archived_plan, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    assert hashlib.sha256(archived_plan_raw).hexdigest() == result.plan_digest
    assert set(archived_plan["sources"]) == set(builder.LOGICAL_DESTINATIONS)
    for source in archived_plan["sources"].values():
        assert set(source) == {
            "path", "runtime_path", "device", "inode", "size", "sha256",
        }
    assert manifest["classification"] == "READ_ONLY_EXTERNAL_INPUT"
    assert manifest["command"] == "SNAPSHOT"
    assert manifest["backend_commit"] == BACKEND_COMMIT
    assert manifest["manifest_version"] == kwargs["manifest_version"]
    assert manifest["plan_digest"] == result.plan_digest
    assert manifest["plan_path"] == plan_path.name
    assert manifest["plan_sha256"] == result.plan_digest
    assert datetime.fromisoformat(manifest["valid_until"]) - datetime.fromisoformat(manifest["generated_at"]) == timedelta(minutes=30)
    assert set(manifest["files"]) == set(builder.LOGICAL_DESTINATIONS)
    assert stat.S_IMODE(version_root.stat().st_mode) == 0o500
    assert version_root.stat().st_uid == kwargs["runtime_uid"]
    assert version_root.stat().st_gid == kwargs["runtime_gid"]
    copied = {path.relative_to(version_root).as_posix() for path in version_root.rglob("*") if path.is_file()}
    assert copied == set(builder.LOGICAL_DESTINATIONS.values())
    for logical_name, entry in manifest["files"].items():
        assert entry["required"] is True and entry["read_only"] is True
        output = version_root / entry["path"]
        assert stat.S_IMODE(output.stat().st_mode) == 0o400
        assert output.stat().st_uid == kwargs["runtime_uid"]
        assert output.stat().st_gid == kwargs["runtime_gid"]
        assert entry["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()


def test_refresh_rotates_active_only_after_complete_new_version(
    secure_tmp_path, monkeypatch,
):
    first = _kwargs(secure_tmp_path / "shared", "one", 0)
    _apply(first, monkeypatch)
    old_active = first["manifest_path"].read_bytes()
    old = json.loads(old_active)
    old_tree = first["destination_root"] / old["input_directory"]
    old_manifest = first["manifest_path"].parent / old["manifest_path"]

    second = dict(first)
    second.update(
        sources=_sources(secure_tmp_path / "shared", "two"),
        manifest_version="snapshot-20260712T122900Z-two",
        generated_at=datetime(2026, 7, 12, 12, 29, tzinfo=timezone.utc),
    )
    plan = builder.build_semantic_manifest(**second)
    second["sources"]["macro_report"].write_text('{"tampered":true}')
    with pytest.raises(builder.SemanticManifestBuildError, match="plan digest"):
        builder.build_semantic_manifest(
            **second, apply=True, approved_plan_digest=plan.plan_digest,
        )
    assert first["manifest_path"].read_bytes() == old_active
    assert old_tree.is_dir() and old_manifest.is_file()

    second["sources"] = _sources(secure_tmp_path / "shared", "three")
    second["manifest_version"] = "snapshot-20260712T122900Z-three"
    _apply(second, monkeypatch)
    current = json.loads(first["manifest_path"].read_text())
    assert current["manifest_version"] == second["manifest_version"]
    assert old_tree.is_dir() and old_manifest.is_file()


def test_concurrent_refresh_never_activates_partial_tree_or_manifest(
    secure_tmp_path, monkeypatch,
):
    base = secure_tmp_path / "concurrent"
    one = _kwargs(base, "one", 1)
    two = dict(one)
    two.update(
        sources=_sources(base, "two"),
        manifest_version="snapshot-20260712T120200Z-two",
        generated_at=datetime(2026, 7, 12, 12, 2, tzinfo=timezone.utc),
    )
    plans = [builder.build_semantic_manifest(**item) for item in (one, two)]
    monkeypatch.setattr(builder.os, "geteuid", lambda: 0)
    first_done = Event()

    def publish(item, plan, *, wait=False):
        if wait:
            assert first_done.wait(5)
        result = builder.build_semantic_manifest(
            **item,
            apply=True,
            approved_plan_digest=plan.plan_digest,
            clock=lambda: datetime(2026, 7, 12, 12, 3, tzinfo=timezone.utc),
        )
        first_done.set()
        return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(publish, one, plans[0]),
            pool.submit(publish, two, plans[1], wait=True),
        ]
        for future in futures:
            future.result()
    active = json.loads(one["manifest_path"].read_text())
    manifest_path = one["manifest_path"].parent / active["manifest_path"]
    version_root = one["destination_root"] / active["input_directory"]
    manifest = json.loads(manifest_path.read_text())
    assert hashlib.sha256(manifest_path.read_bytes()).hexdigest() == active["manifest_sha256"]
    assert manifest["manifest_version"] == active["manifest_version"]
    assert all((version_root / entry["path"]).is_file() for entry in manifest["files"].values())


def test_apply_clock_rejects_expired_or_future_plan_before_any_mutation(
    secure_tmp_path, monkeypatch,
):
    current = _kwargs(secure_tmp_path / "clock", "current", 0)
    _apply(current, monkeypatch)
    active_before = current["manifest_path"].read_bytes()
    entries_before = sorted(path.name for path in current["manifest_path"].parent.iterdir())

    expired = dict(current)
    expired.update(
        sources=_sources(secure_tmp_path / "clock", "expired"),
        manifest_version="snapshot-expired",
        generated_at=datetime(2026, 7, 12, 12, 31, tzinfo=timezone.utc),
    )
    expired_plan = builder.build_semantic_manifest(**expired)
    with pytest.raises(builder.SemanticManifestBuildError, match="expired"):
        builder.build_semantic_manifest(
            **expired,
            apply=True,
            approved_plan_digest=expired_plan.plan_digest,
            clock=lambda: datetime(2026, 7, 12, 13, 1, tzinfo=timezone.utc),
        )

    future = dict(expired)
    future.update(
        sources=_sources(secure_tmp_path / "clock", "future"),
        manifest_version="snapshot-future",
        generated_at=datetime(2026, 7, 12, 13, 5, tzinfo=timezone.utc),
    )
    future_plan = builder.build_semantic_manifest(**future)
    with pytest.raises(builder.SemanticManifestBuildError, match="future"):
        builder.build_semantic_manifest(
            **future,
            apply=True,
            approved_plan_digest=future_plan.plan_digest,
            clock=lambda: datetime(2026, 7, 12, 13, 4, tzinfo=timezone.utc),
        )
    assert current["manifest_path"].read_bytes() == active_before
    assert sorted(path.name for path in current["manifest_path"].parent.iterdir()) == entries_before


def test_expiry_is_checked_while_publication_lock_is_held(
    secure_tmp_path, monkeypatch,
):
    base = secure_tmp_path / "locked-clock"
    current = _kwargs(base, "current", 0)
    _apply(current, monkeypatch)
    active_before = current["manifest_path"].read_bytes()
    expired = dict(current)
    expired.update(
        sources=_sources(base, "expired"),
        manifest_version="snapshot-expired-after-lock",
        generated_at=datetime(2026, 7, 12, 12, 20, tzinfo=timezone.utc),
    )
    plan = builder.build_semantic_manifest(**expired)
    lock_path = current["manifest_path"].parent / f".{current['manifest_path'].name}.lock"

    def clock_while_locked():
        fd = os.open(lock_path, os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)
        return datetime(2026, 7, 12, 12, 50, tzinfo=timezone.utc)

    with pytest.raises(builder.SemanticManifestBuildError, match="expired"):
        builder.build_semantic_manifest(
            **expired,
            apply=True,
            approved_plan_digest=plan.plan_digest,
            clock=clock_while_locked,
        )
    assert current["manifest_path"].read_bytes() == active_before


def test_apply_requires_preprovisioned_root_lock_before_any_mutation(
    secure_tmp_path, monkeypatch,
):
    kwargs = _kwargs(secure_tmp_path / "missing-lock", "current", 0)
    lock = kwargs["manifest_path"].parent / f".{kwargs['manifest_path'].name}.lock"
    lock.unlink()
    plan = builder.build_semantic_manifest(**kwargs)
    monkeypatch.setattr(builder.os, "geteuid", lambda: 0)
    with pytest.raises(builder.SemanticManifestBuildError, match="publication lock"):
        builder.build_semantic_manifest(
            **kwargs,
            apply=True,
            approved_plan_digest=plan.plan_digest,
            clock=lambda: kwargs["generated_at"] + timedelta(minutes=1),
        )
    assert not kwargs["manifest_path"].exists()
    assert list(kwargs["destination_root"].iterdir()) == []


def test_activation_is_monotonic_and_exact_reapply_is_idempotent(
    secure_tmp_path, monkeypatch,
):
    base = secure_tmp_path / "monotonic"
    current = _kwargs(base, "current", 10)
    first = _apply(current, monkeypatch)
    active_before = current["manifest_path"].read_bytes()
    entries_before = sorted(path.name for path in current["manifest_path"].parent.iterdir())

    older = dict(current)
    older.update(
        sources=_sources(base, "older"),
        manifest_version="snapshot-older",
        generated_at=datetime(2026, 7, 12, 12, 9, tzinfo=timezone.utc),
    )
    older_plan = builder.build_semantic_manifest(**older)
    with pytest.raises(builder.SemanticManifestBuildError, match="monotonic"):
        builder.build_semantic_manifest(
            **older,
            apply=True,
            approved_plan_digest=older_plan.plan_digest,
            clock=lambda: datetime(2026, 7, 12, 12, 11, tzinfo=timezone.utc),
        )

    equal = dict(current)
    equal.update(sources=_sources(base, "equal"), manifest_version="snapshot-equal")
    equal_plan = builder.build_semantic_manifest(**equal)
    with pytest.raises(builder.SemanticManifestBuildError, match="monotonic"):
        builder.build_semantic_manifest(
            **equal,
            apply=True,
            approved_plan_digest=equal_plan.plan_digest,
            clock=lambda: datetime(2026, 7, 12, 12, 11, tzinfo=timezone.utc),
        )

    exact = builder.build_semantic_manifest(
        **current,
        apply=True,
        approved_plan_digest=first.plan_digest,
        clock=lambda: datetime(2026, 7, 12, 12, 11, tzinfo=timezone.utc),
    )
    assert exact.applied is False and exact.idempotent is True
    assert current["manifest_path"].read_bytes() == active_before
    assert sorted(path.name for path in current["manifest_path"].parent.iterdir()) == entries_before


def test_idempotent_reapply_requires_exact_internal_active_references(
    secure_tmp_path, monkeypatch,
):
    current = _kwargs(secure_tmp_path / "exact", "current", 10)
    first = _apply(current, monkeypatch)
    active = json.loads(current["manifest_path"].read_text())
    active["plan_path"] = "operator-controlled.json"
    current["manifest_path"].chmod(0o600)
    current["manifest_path"].write_text(json.dumps(active))
    current["manifest_path"].chmod(0o444)

    with pytest.raises(builder.SemanticManifestBuildError, match="exact|authority"):
        builder.build_semantic_manifest(
            **current,
            apply=True,
            approved_plan_digest=first.plan_digest,
            clock=lambda: datetime(2026, 7, 12, 12, 11, tzinfo=timezone.utc),
        )


@pytest.mark.parametrize(
    "probe",
    [
        "plan_missing", "plan_tamper", "manifest_missing", "manifest_tamper",
        "file_missing", "file_tamper", "file_mode", "extra_file",
    ],
)
def test_idempotent_noop_requires_complete_immutable_publication(
    secure_tmp_path, monkeypatch, probe,
):
    current = _kwargs(secure_tmp_path / probe, "current", 10)
    first = _apply(current, monkeypatch)
    active_before = current["manifest_path"].read_bytes()
    active = json.loads(active_before)
    plan_path = current["manifest_path"].parent / active["plan_path"]
    manifest_path = current["manifest_path"].parent / active["manifest_path"]
    version_root = current["destination_root"] / active["input_directory"]
    manifest = json.loads(manifest_path.read_text())
    file_path = version_root / manifest["files"]["macro_report"]["path"]

    if probe == "plan_missing":
        plan_path.unlink()
    elif probe == "plan_tamper":
        plan_path.chmod(0o600)
        plan_path.write_text('{"tampered":true}\n')
        plan_path.chmod(0o444)
    elif probe == "manifest_missing":
        manifest_path.unlink()
    elif probe == "manifest_tamper":
        manifest_path.chmod(0o600)
        manifest_path.write_text('{"tampered":true}\n')
        manifest_path.chmod(0o444)
    elif probe == "file_missing":
        file_path.parent.chmod(0o700)
        file_path.unlink()
        file_path.parent.chmod(0o500)
    elif probe == "file_tamper":
        file_path.parent.chmod(0o700)
        file_path.chmod(0o600)
        file_path.write_text('{"tampered":true}\n')
        file_path.chmod(0o400)
        file_path.parent.chmod(0o500)
    elif probe == "file_mode":
        file_path.chmod(0o600)
    else:
        version_root.chmod(0o700)
        extra = version_root / "extra.json"
        extra.write_text("{}\n")
        extra.chmod(0o400)
        version_root.chmod(0o500)

    with pytest.raises(builder.SemanticManifestBuildError, match="existing active publication"):
        builder.build_semantic_manifest(
            **current,
            apply=True,
            approved_plan_digest=first.plan_digest,
            clock=lambda: datetime(2026, 7, 12, 12, 11, tzinfo=timezone.utc),
        )
    assert current["manifest_path"].read_bytes() == active_before


def test_delayed_older_concurrent_refresh_cannot_overwrite_newer_active(
    secure_tmp_path, monkeypatch,
):
    base = secure_tmp_path / "delayed"
    older = _kwargs(base, "older", 19)
    newer = dict(older)
    newer.update(
        sources=_sources(base, "newer"),
        manifest_version="snapshot-newer",
        generated_at=datetime(2026, 7, 12, 12, 20, tzinfo=timezone.utc),
    )
    old_plan = builder.build_semantic_manifest(**older)
    new_plan = builder.build_semantic_manifest(**newer)
    monkeypatch.setattr(builder.os, "geteuid", lambda: 0)
    newer_done = Event()

    def publish_newer():
        result = builder.build_semantic_manifest(
            **newer,
            apply=True,
            approved_plan_digest=new_plan.plan_digest,
            clock=lambda: datetime(2026, 7, 12, 12, 21, tzinfo=timezone.utc),
        )
        newer_done.set()
        return result

    def publish_delayed_older():
        assert newer_done.wait(5)
        return builder.build_semantic_manifest(
            **older,
            apply=True,
            approved_plan_digest=old_plan.plan_digest,
            clock=lambda: datetime(2026, 7, 12, 12, 21, tzinfo=timezone.utc),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        new_future = pool.submit(publish_newer)
        old_future = pool.submit(publish_delayed_older)
        new_future.result()
        with pytest.raises(builder.SemanticManifestBuildError, match="monotonic"):
            old_future.result()
    assert json.loads(older["manifest_path"].read_text())["manifest_version"] == "snapshot-newer"


def test_sources_must_be_six_distinct_canonical_paths_and_inodes(secure_tmp_path):
    kwargs = _kwargs(secure_tmp_path)
    kwargs["sources"]["sentiment_report"] = kwargs["sources"]["macro_report"]
    with pytest.raises(builder.SemanticManifestBuildError, match="distinct"):
        builder.build_semantic_manifest(**kwargs)

    kwargs = _kwargs(secure_tmp_path / "hardlink")
    duplicate = kwargs["sources"]["sentiment_report"]
    duplicate.unlink()
    os.link(kwargs["sources"]["macro_report"], duplicate)
    with pytest.raises(builder.SemanticManifestBuildError, match="distinct"):
        builder.build_semantic_manifest(**kwargs)


@pytest.mark.parametrize("unsafe", ["input_symlink", "authority_symlink", "writable_input"])
def test_privileged_parents_reject_symlink_and_unsafe_mode(
    secure_tmp_path, unsafe,
):
    kwargs = _kwargs(secure_tmp_path)
    if unsafe == "input_symlink":
        real = kwargs["destination_root"].with_name("real-input")
        kwargs["destination_root"].rename(real)
        kwargs["destination_root"].symlink_to(real, target_is_directory=True)
    elif unsafe == "authority_symlink":
        parent = kwargs["manifest_path"].parent
        real = parent.with_name("real-authority")
        parent.rename(real)
        parent.symlink_to(real, target_is_directory=True)
    else:
        kwargs["destination_root"].chmod(0o777)
    with pytest.raises(builder.SemanticManifestBuildError, match="trusted|symlink|mode"):
        builder.build_semantic_manifest(**kwargs)


def test_parent_path_replacement_is_detected_before_active_rotation(
    secure_tmp_path, monkeypatch,
):
    kwargs = _kwargs(secure_tmp_path)
    plan = builder.build_semantic_manifest(**kwargs)
    original = kwargs["destination_root"]
    moved = original.with_name("moved-input")
    attacker = original.with_name("attacker-input")
    attacker.mkdir(mode=0o700)
    real_open = builder.os.open
    swapped = False

    def swapping_open(path, flags, *args, **kw):
        nonlocal swapped
        fd = real_open(path, flags, *args, **kw)
        if path == original.name and kw.get("dir_fd") is not None and not swapped:
            original.rename(moved)
            original.symlink_to(attacker, target_is_directory=True)
            swapped = True
        return fd

    monkeypatch.setattr(builder.os, "open", swapping_open)
    monkeypatch.setattr(builder.os, "geteuid", lambda: 0)
    with pytest.raises(builder.SemanticManifestBuildError, match="changed|symlink"):
        builder.build_semantic_manifest(
            **kwargs,
            apply=True,
            approved_plan_digest=plan.plan_digest,
            clock=lambda: kwargs["generated_at"] + timedelta(minutes=1),
        )
    assert swapped and not kwargs["manifest_path"].exists()


def test_trusted_parents_must_be_traversable_by_explicit_runtime_identity(
    secure_tmp_path,
):
    kwargs = _kwargs(secure_tmp_path)
    kwargs["runtime_uid"] = os.geteuid() + 10000
    kwargs["runtime_gid"] = os.getegid() + 10000
    with pytest.raises(builder.SemanticManifestBuildError, match="traverse"):
        builder.build_semantic_manifest(**kwargs)

    kwargs["destination_root"].chmod(0o701)
    kwargs["manifest_path"].parent.chmod(0o701)
    assert len(builder.build_semantic_manifest(**kwargs).plan_digest) == 64


def test_cli_rejection_redacts_paths_and_os_errors(secure_tmp_path):
    kwargs = _kwargs(secure_tmp_path)
    target = kwargs["sources"]["macro_report"]
    target.unlink()
    target.symlink_to(kwargs["sources"]["sentiment_report"])
    command = [sys.executable, "scripts/build_phase4_semantic_manifest.py"]
    for name, path in kwargs["sources"].items():
        command.extend((f"--{name.replace('_', '-')}", str(path)))
    command.extend((
        "--destination-root", str(kwargs["destination_root"]),
        "--manifest-path", str(kwargs["manifest_path"]),
        "--manifest-version", kwargs["manifest_version"],
        "--backend-commit", kwargs["backend_commit"],
        "--runtime-uid", str(kwargs["runtime_uid"]),
        "--runtime-gid", str(kwargs["runtime_gid"]),
        "--generated-at", kwargs["generated_at"].isoformat(),
    ))
    result = subprocess.run(command, capture_output=True, text=True)
    combined = result.stdout + result.stderr
    assert result.returncode == 2
    assert "macro_report.json" not in combined
    assert str(secure_tmp_path) not in combined
    assert combined.strip() == "semantic input publication rejected"
