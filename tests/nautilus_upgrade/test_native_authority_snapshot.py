from __future__ import annotations

from contextlib import contextmanager
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
from types import SimpleNamespace

import pytest

import scripts.build_nautilus_engine as builder


MODULE = "scripts.materialize_nautilus_native_authority"


def _snapshot_module():
    assert importlib.util.find_spec(MODULE) is not None, "native snapshot module is missing"
    return importlib.import_module(MODULE)


def _canonical(document: object) -> bytes:
    return (
        json.dumps(document, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("ascii")


def _fixture_authority(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    snapshot = _snapshot_module()
    source = tmp_path / "source-include"
    nested = source / "nested"
    nested.mkdir(parents=True)
    data = source / "header.h"
    executable = nested / "tool"
    data.write_bytes(b"reviewed-header\n")
    executable.write_bytes(b"reviewed-tool\n")
    (nested / "header-link").symlink_to("../header.h")
    data.chmod(0o444)
    executable.chmod(0o555)
    nested.chmod(0o555)
    source.chmod(0o555)
    root = tmp_path / "native-authority"
    receipt = tmp_path / "native-authority-receipt.json"
    mappings = ((str(source), "/usr/include"),)
    monkeypatch.setattr(snapshot, "_SNAPSHOT_ROOT", root)
    monkeypatch.setattr(snapshot, "_RECEIPT_PATH", receipt)
    monkeypatch.setattr(snapshot, "_SOURCE_DESTINATION_MAPPINGS", mappings)
    return snapshot, source, root, receipt, mappings


def _policy(snapshot, root: Path, receipt: Path, mappings):
    raw = receipt.read_bytes()
    document = json.loads(raw)
    return {
        "authority": "P1_U04_IMMUTABLE_NATIVE_AUTHORITY_SNAPSHOT_V1",
        "mappings": [
            {"destination": destination, "source": source}
            for source, destination in mappings
        ],
        "payload_tree_sha256": document["payload_tree_sha256"],
        "receipt_path": str(receipt),
        "receipt_sha256": hashlib.sha256(raw).hexdigest(),
        "root": str(root),
        "schema_version": 1,
        "threat_model": "COOPERATIVE_HOST",
    }


def _unseal(root: Path) -> None:
    for current, directories, _files in os.walk(root):
        Path(current).chmod(0o700)
        for name in directories:
            (Path(current) / name).chmod(0o700)


def _reseal(root: Path) -> None:
    for current, directories, _files in os.walk(root, topdown=False):
        for name in directories:
            (Path(current) / name).chmod(0o500)
        Path(current).chmod(0o500)


def test_native_snapshot_uses_the_exact_fixed_fourteen_mappings() -> None:
    snapshot = _snapshot_module()

    assert snapshot.SOURCE_DESTINATION_MAPPINGS == (
        ("/usr/bin/python3.12", "/usr/bin/python3.12"),
        ("/usr/lib/python3.12", "/usr/lib/python3.12"),
        ("/usr/lib/x86_64-linux-gnu", "/usr/lib/x86_64-linux-gnu"),
        ("/usr/lib/gcc/x86_64-linux-gnu/13", "/usr/lib/gcc/x86_64-linux-gnu/13"),
        ("/usr/libexec/gcc/x86_64-linux-gnu/13", "/usr/libexec/gcc/x86_64-linux-gnu/13"),
        ("/usr/include", "/usr/include"),
        ("/usr/local/include", "/usr/local/include"),
        ("/usr/bin/ar", "/usr/bin/ar"),
        ("/usr/bin/ld", "/usr/bin/ld"),
        ("/usr/bin/strip", "/usr/bin/strip"),
        ("/usr/bin/x86_64-linux-gnu-ar", "/usr/bin/x86_64-linux-gnu-ar"),
        ("/usr/bin/x86_64-linux-gnu-ld", "/usr/bin/x86_64-linux-gnu-ld"),
        ("/usr/bin/x86_64-linux-gnu-ld.bfd", "/usr/bin/x86_64-linux-gnu-ld.bfd"),
        ("/usr/bin/x86_64-linux-gnu-strip", "/usr/bin/x86_64-linux-gnu-strip"),
    )


def test_materializer_publishes_canonical_sealed_payload_and_one_way_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot, _source, root, receipt, mappings = _fixture_authority(
        tmp_path, monkeypatch
    )

    document = snapshot.materialize()

    assert receipt.parent == root.parent and not receipt.is_relative_to(root)
    assert receipt.read_bytes() == _canonical(document)
    assert document["mappings"] == [
        {"destination": destination, "source": source}
        for source, destination in mappings
    ]
    assert "receipt_sha256" not in document
    assert stat.S_IMODE(root.lstat().st_mode) == 0o500
    assert stat.S_IMODE((root / "usr/include/header.h").lstat().st_mode) == 0o400
    assert stat.S_IMODE((root / "usr/include/nested/tool").lstat().st_mode) == 0o500
    assert (root / "usr/include/header.h").lstat().st_nlink == 1
    policy = _policy(snapshot, root, receipt, mappings)
    with snapshot.verify_and_open(policy) as verified:
        assert stat.S_ISDIR(os.fstat(verified.root_fd).st_mode)
        assert [(mount.destination, stat.S_IFMT(os.fstat(mount.fd).st_mode)) for mount in verified.mounts] == [
            ("/usr/include", stat.S_IFDIR)
        ]


def test_materializer_rejects_destination_byte_mismatch_despite_stable_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot, source, root, receipt, _mappings = _fixture_authority(
        tmp_path, monkeypatch
    )
    real_copy = snapshot._copy_regular

    def drifted_copy(*args, **kwargs):
        record = real_copy(*args, **kwargs)
        destination_fd = args[2]
        destination_name = args[3]
        os.chmod(destination_name, 0o600, dir_fd=destination_fd)
        fd = os.open(destination_name, os.O_WRONLY, dir_fd=destination_fd)
        try:
            os.pwrite(fd, b"X", 0)
        finally:
            os.close(fd)
        os.chmod(
            destination_name,
            int(str(args[5]["snapshot_mode"]), 8),
            dir_fd=destination_fd,
        )
        return record

    monkeypatch.setattr(snapshot, "_copy_regular", drifted_copy)

    with pytest.raises(snapshot.SnapshotError, match="three-way equality"):
        snapshot.materialize()

    assert source.exists()
    assert not root.exists()
    assert not receipt.exists()


def test_materializer_rejects_source_identity_drift_and_existing_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot, source, root, _receipt, _mappings = _fixture_authority(
        tmp_path, monkeypatch
    )
    root.mkdir()
    root.chmod(0o500)
    with pytest.raises(snapshot.SnapshotError, match="already exists"):
        snapshot.materialize()
    root.chmod(0o700)
    root.rmdir()
    real_copy = snapshot._copy_regular

    def drifting_copy(*args, **kwargs):
        record = real_copy(*args, **kwargs)
        path = source / "header.h"
        path.chmod(0o600)
        path.write_bytes(b"source-drifted!\n")
        path.chmod(0o444)
        return record

    monkeypatch.setattr(snapshot, "_copy_regular", drifting_copy)
    with pytest.raises(snapshot.SnapshotError, match="source.*drift"):
        snapshot.materialize()
    assert not root.exists()


@pytest.mark.parametrize("drift", ("extra", "missing", "writable", "hardlink", "fifo"))
def test_verifier_rejects_extra_missing_special_writable_and_multiply_linked_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drift: str
) -> None:
    snapshot, _source, root, receipt, mappings = _fixture_authority(
        tmp_path, monkeypatch
    )
    snapshot.materialize()
    policy = _policy(snapshot, root, receipt, mappings)
    _unseal(root)
    target = root / "usr/include/header.h"
    if drift == "extra":
        extra = root / "usr/include/extra.h"
        extra.write_bytes(b"extra")
        extra.chmod(0o400)
    elif drift == "missing":
        target.unlink()
    elif drift == "writable":
        target.chmod(0o600)
    elif drift == "hardlink":
        os.link(target, root / "usr/include/alias.h")
    else:
        os.mkfifo(root / "usr/include/special")
    _reseal(root)

    with pytest.raises(snapshot.SnapshotError):
        with snapshot.verify_and_open(policy):
            pass


def test_symlink_policy_allows_exact_three_dead_etc_targets_and_no_fourth() -> None:
    snapshot = _snapshot_module()
    mappings = (
        ("/source/python", "/usr/lib/python3.12"),
        ("/source/native", "/usr/lib/x86_64-linux-gnu"),
    )
    valid = [
        {
            "path": "/usr/lib/python3.12/sitecustomize.py",
            "target": "/etc/python3.12/sitecustomize.py",
            "type": "symlink",
        },
        {
            "path": "/usr/lib/x86_64-linux-gnu/libblas.so.3",
            "target": "/etc/alternatives/libblas.so.3-x86_64-linux-gnu",
            "type": "symlink",
        },
        {
            "path": "/usr/lib/x86_64-linux-gnu/liblapack.so.3",
            "target": "/etc/alternatives/liblapack.so.3-x86_64-linux-gnu",
            "type": "symlink",
        },
    ]
    snapshot._validate_symlinks(valid, mappings)

    for target in ("/etc/passwd", "../../../../etc/passwd", "missing.so"):
        invalid = [
            {
                "path": "/usr/lib/x86_64-linux-gnu/libfourth.so",
                "target": target,
                "type": "symlink",
            }
        ]
        with pytest.raises(snapshot.SnapshotError, match="symlink"):
            snapshot._validate_symlinks(invalid, mappings)


@pytest.mark.parametrize(
    "field,value",
    (
        ("root", "/tmp/foreign"),
        ("receipt_sha256", "0" * 64),
        ("payload_tree_sha256", "0" * 64),
        ("mappings", []),
        ("threat_model", "HOSTILE_HOST"),
    ),
)
def test_verifier_rejects_each_policy_binding_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
) -> None:
    snapshot, _source, root, receipt, mappings = _fixture_authority(
        tmp_path, monkeypatch
    )
    snapshot.materialize()
    policy = _policy(snapshot, root, receipt, mappings)
    policy[field] = value

    with pytest.raises(snapshot.SnapshotError, match="policy"):
        with snapshot.verify_and_open(policy):
            pass


def test_candidate_sandbox_uses_only_snapshot_ro_bind_fds_for_native_destinations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    physical_stage = tmp_path / "physical-stage"
    source = physical_stage / "source"
    source.mkdir(parents=True)
    source.chmod(0o700)
    physical_stage.chmod(0o700)
    native = tmp_path / "native"
    native.mkdir()
    native_fd = os.open(native, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW)
    observed: dict[str, object] = {}

    @contextmanager
    def verified_snapshot(_engine):
        yield SimpleNamespace(
            root_fd=native_fd,
            mounts=(SimpleNamespace(fd=native_fd, destination="/usr/include"),),
        )

    def fake_run(command, **kwargs):
        observed["command"] = command
        observed["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(builder, "_verified_candidate_native_snapshot", verified_snapshot)
    monkeypatch.setattr(builder.subprocess, "run", fake_run)
    monkeypatch.setattr(builder, "_validate_sandbox", lambda _sandbox: "bubblewrap fixture")
    try:
        engine = builder._candidate_json(builder._CANDIDATE_ENGINE_POLICY)
        logical_stage = (
            builder._candidate_roots(engine)["candidate_build_root"]
            / "stage-0000000000000000"
        )
        builder._candidate_sandbox_run(
            physical_stage=physical_stage,
            logical_stage=logical_stage,
            action="policy-probe",
        )
    finally:
        os.close(native_fd)

    command = observed["command"]
    assert isinstance(command, list)
    triples = [command[index : index + 3] for index in range(len(command) - 2)]
    pairs = [command[index : index + 2] for index in range(len(command) - 1)]
    assert ["--ro-bind-fd", str(native_fd), "/usr/include"] in triples
    assert ["--ro-bind", "/usr/include", "/usr/include"] not in triples
    assert ["--dir", "/etc"] in pairs
    assert ["--dir", "/etc/python3.12"] in pairs
    assert ["--dir", "/etc/alternatives"] in pairs
    stage_bind = command.index("--bind-fd")
    root_remount = command.index("--remount-ro")
    assert command[root_remount : root_remount + 2] == ["--remount-ro", "/"]
    assert stage_bind < root_remount < command.index("--chdir")
    assert "--remount-ro-recursive" not in command
    assert "os.listdir('/etc/python3.12')==[]" in command[-1]
    assert "os.listdir('/etc/alternatives')==[]" in command[-1]
    for path, target in (
        (
            "/usr/lib/python3.12/sitecustomize.py",
            "/etc/python3.12/sitecustomize.py",
        ),
        (
            "/usr/lib/x86_64-linux-gnu/libblas.so.3",
            "/etc/alternatives/libblas.so.3-x86_64-linux-gnu",
        ),
        (
            "/usr/lib/x86_64-linux-gnu/liblapack.so.3",
            "/etc/alternatives/liblapack.so.3-x86_64-linux-gnu",
        ),
    ):
        assert repr(path) in command[-1]
        assert repr(target) in command[-1]
    assert "os.makedirs(os.path.dirname(target),exist_ok=True)" in command[-1]
    assert "os.O_CREAT|os.O_EXCL" in command[-1]
    assert "exc.errno!=errno.EROFS" in command[-1]
    assert ".p1-u04-stage-write-probe" in command[-1]
    assert "os.unlink(stage_probe)" in command[-1]
    assert all(
        not (
            triple[0] == "--ro-bind"
            and triple[2].startswith("/usr/")
            and triple[2]
            in {destination for _source, destination in _snapshot_module().SOURCE_DESTINATION_MAPPINGS}
        )
        for triple in triples
    )
    assert native_fd in observed["kwargs"]["pass_fds"]


def test_verified_snapshot_fds_survive_snapshot_path_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot, _source, root, receipt, mappings = _fixture_authority(
        tmp_path, monkeypatch
    )
    snapshot.materialize()
    policy = _policy(snapshot, root, receipt, mappings)

    with snapshot.verify_and_open(policy) as verified:
        held = os.fstat(verified.root_fd)
        moved = root.with_name("held-native-authority")
        root.rename(moved)
        root.mkdir(mode=0o500)
        assert (os.fstat(verified.root_fd).st_dev, os.fstat(verified.root_fd).st_ino) == (
            held.st_dev,
            held.st_ino,
        )
        assert os.fstat(verified.mounts[0].fd).st_ino == (moved / "usr/include").stat().st_ino
