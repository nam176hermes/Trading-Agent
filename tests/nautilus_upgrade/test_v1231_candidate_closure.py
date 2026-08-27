from __future__ import annotations

import ast
import base64
import copy
import csv
import errno
from collections.abc import Callable, Iterator
from contextlib import nullcontext
from dataclasses import fields, replace
import gzip
import hashlib
import json
import os
import io
from pathlib import Path, PurePosixPath
import py_compile
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import zipfile

import pytest

import scripts.build_nautilus_engine as builder
import scripts.materialize_nautilus_runtime_closure as materializer
import scripts.verify_nautilus_release_provenance as provenance
from services.job_worker.engine_spawn import (
    CompleteEngineClosureAttestation,
    NativeEntryGuardAttestation,
    OsSandboxProof,
    ReadOnlyClosureMount,
)


ROOT = Path(__file__).resolve().parents[2]
ENGINE_POLICY = ROOT / "engines/nautilus/candidates/v1.231/engine-build-policy.json"
WHEEL_FILENAME = "nautilus_trader-1.231.0-cp312-cp312-manylinux_2_39_x86_64.whl"
HOST_AUTHORITY_RUNNER = ROOT / "scripts/verify_p1_u04_host_authority.py"
U04_TEST_ROOT = ROOT / "tests/nautilus_upgrade"


@pytest.fixture
def x4_posix_tmp_path() -> Iterator[Path]:
    path = Path(tempfile.mkdtemp(prefix="p1-u04-x4-test-", dir="/tmp"))
    try:
        yield path
    finally:
        for current, directories, files in os.walk(path, topdown=False):
            for name in files:
                (Path(current) / name).chmod(0o600)
            for name in directories:
                (Path(current) / name).chmod(0o700)
        path.chmod(0o700)
        shutil.rmtree(path)


def _portable_u04_test_modules() -> tuple[Path, ...]:
    return tuple(sorted(U04_TEST_ROOT.glob("test_*.py")))


def _collect_u04_nodes(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", str(path)],
        cwd=ROOT,
        env={"PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_u04_portable_tests_have_no_host_authority_dependencies() -> None:
    forbidden = (
        "/".join(("", "home", "thenam176")),
        ".cache/" + "p1-u03-toolchain-policy-20260823",
        "/usr/bin/" + "bwrap",
    )
    violations = []
    for path in _portable_u04_test_modules():
        source = path.read_text(encoding="utf-8")
        violations.extend(
            (path.relative_to(ROOT).as_posix(), token)
            for token in (*forbidden, "pytest." + "skip(")
            if token in source
        )

    assert violations == []


def test_u04_host_lane_is_excluded_from_canonical_discovery() -> None:
    host_directory = ROOT / "tests/nautilus_upgrade/host_authority"
    discoverable_host_files = sorted(
        path.relative_to(ROOT).as_posix() for path in host_directory.glob("test*.py")
    )
    marked_portable_tests = []
    for path in _portable_u04_test_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("test_"):
                continue
            markers = {ast.unparse(item) for item in node.decorator_list}
            if "pytest.mark.host_coupled" in markers:
                marked_portable_tests.append(
                    (path.relative_to(ROOT).as_posix(), node.name)
                )

    assert discoverable_host_files == []
    assert marked_portable_tests == []


def test_u04_host_implementations_are_physically_separated() -> None:
    violations = []
    for path in _portable_u04_test_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        violations.extend(
            (path.relative_to(ROOT).as_posix(), node.name)
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("_host_")
        )

    assert violations == []


def test_u04_canonical_collection_contains_only_portable_modules() -> None:
    result = _collect_u04_nodes(U04_TEST_ROOT)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "host_authority/" not in result.stdout
    collected_modules = {
        line.split("::", 1)[0]
        for line in result.stdout.splitlines()
        if "::" in line
    }
    assert collected_modules == {
        path.relative_to(ROOT).as_posix() for path in _portable_u04_test_modules()
    }


def test_u04_collection_control_observes_new_discoverable_host_file(
    tmp_path: Path,
) -> None:
    mutation_root = tmp_path / "nautilus_upgrade"
    host_directory = mutation_root / "host_authority"
    host_directory.mkdir(parents=True)
    mutation = host_directory / "test_u04_host_mutation.py"
    mutation.write_text(
        "def test_indirect_host_route():\n    pass\n",
        encoding="ascii",
    )

    result = _collect_u04_nodes(mutation_root)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "host_authority/test_u04_host_mutation.py::test_indirect_host_route" in (
        result.stdout
    )


def test_u04_host_authority_runner_defers_when_authority_is_absent() -> None:
    result = subprocess.run(
        [sys.executable, str(HOST_AUTHORITY_RUNNER)],
        cwd=ROOT,
        env={"PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 3
    assert json.loads(result.stdout) == {
        "lane": "HOST_EXTERNAL_AUTHORITY",
        "outcome": "DEFERRED",
        "reason": "EVIDENCE_CACHE_NOT_SUPPLIED",
        "schema": "p1-u04-host-authority-receipt-v1",
    }
    assert result.stderr == ""


def test_u04_host_authority_runner_fails_for_supplied_malformed_cache(
    tmp_path: Path,
) -> None:
    cache = tmp_path / "malformed-cache"
    cache.mkdir()
    result = subprocess.run(
        [
            sys.executable,
            str(HOST_AUTHORITY_RUNNER),
            "--evidence-cache",
            str(cache),
        ],
        cwd=ROOT,
        env={"PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 1
    assert json.loads(result.stdout) == {
        "lane": "HOST_EXTERNAL_AUTHORITY",
        "outcome": "FAIL",
        "reason": "EVIDENCE_CACHE_PATH_NOT_EXACT",
        "schema": "p1-u04-host-authority-receipt-v1",
    }
    assert result.stderr == ""


def _candidate_requires_dist() -> tuple[str, ...]:
    engine = builder._candidate_json(ENGINE_POLICY)
    authority = engine["candidate_wheel_metadata"]
    assert isinstance(authority, dict)
    requirements = authority["requires_dist"]
    assert isinstance(requirements, list)
    return tuple(requirements)


def _bind_candidate_requires_dist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    requirements: list[str],
) -> None:
    engine = builder._candidate_json(ENGINE_POLICY)
    authority = engine["candidate_wheel_metadata"]
    assert isinstance(authority, dict)
    authority["requires_dist"] = requirements
    engine_path = tmp_path / "engine-build-policy.json"
    engine_path.write_text(
        json.dumps(engine, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="ascii",
    )
    inputs = builder._candidate_json(builder._CANDIDATE_TOOLCHAIN_INPUTS)
    policy_hashes = inputs["policy_hashes"]
    assert isinstance(policy_hashes, dict)
    policy_hashes["engine_build_policy_sha256"] = hashlib.sha256(
        engine_path.read_bytes()
    ).hexdigest()
    inputs_path = tmp_path / "toolchain-inputs.json"
    inputs_path.write_text(
        json.dumps(inputs, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="ascii",
    )
    monkeypatch.setattr(builder, "_CANDIDATE_ENGINE_POLICY", engine_path)
    monkeypatch.setattr(builder, "_CANDIDATE_TOOLCHAIN_INPUTS", inputs_path)


def _elf64(
    *,
    elf_type: int = 3,
    needed: tuple[str, ...] = (),
    soname: str | None = "fixture.so",
    interpreter: str | None = None,
    dynamic: bool = True,
    duplicate_soname: bool = False,
    terminate_dynamic: bool = True,
    strsz: int | None = None,
    terminate_interpreter: bool = True,
    gnu_relro: bool = True,
    gnu_relro_size: int | None = None,
    gnu_relro_vaddr: int | None = None,
    gnu_stack_flags: int | None = 6,
    bind_now: bool = True,
    dynamic_flags: int | None = None,
    dynamic_flags_1: int | None = None,
) -> bytes:
    strings = bytearray(b"\0")

    def string_offset(value: str) -> int:
        offset = len(strings)
        strings.extend(value.encode("ascii") + b"\0")
        return offset

    needed_offsets = [string_offset(value) for value in needed]
    soname_offset = None if soname is None else string_offset(soname)
    phnum = (
        1
        + int(dynamic)
        + int(interpreter is not None)
        + int(gnu_relro)
        + int(gnu_stack_flags is not None)
    )
    dynamic_offset = 64 + phnum * 56
    entries: list[tuple[int, int]] = [(1, offset) for offset in needed_offsets]
    if soname_offset is not None:
        entries.append((14, soname_offset))
        if duplicate_soname:
            entries.append((14, soname_offset))
    if bind_now:
        entries.append((24, 0))
    if dynamic_flags is not None:
        entries.append((30, dynamic_flags))
    if dynamic_flags_1 is not None:
        entries.append((0x6FFFFFFB, dynamic_flags_1))
    string_offset_in_file = dynamic_offset + (
        len(entries) + 2 + int(terminate_dynamic)
    ) * 16
    entries.extend(
        (
            (5, 0x400000 + string_offset_in_file),
            (10, len(strings) if strsz is None else strsz),
        )
    )
    if terminate_dynamic:
        entries.append((0, 0))
    dynamic_bytes = b"".join(struct.pack("<qQ", *entry) for entry in entries)
    interpreter_bytes = b""
    if interpreter is not None:
        interpreter_bytes = interpreter.encode("ascii") + (
            b"\0" if terminate_interpreter else b""
        )
    payload_size = string_offset_in_file + len(strings) + len(interpreter_bytes)
    ident = b"\x7fELF\x02\x01\x01" + b"\0" * 9
    header = struct.pack(
        "<16sHHIQQQIHHHHHH",
        ident,
        elf_type,
        62,
        1,
        0,
        64,
        0,
        0,
        64,
        56,
        phnum,
        0,
        0,
        0,
    )
    program_headers = [
        struct.pack(
            "<IIQQQQQQ", 1, 5, 0, 0x400000, 0x400000, payload_size, payload_size, 0x1000
        )
    ]
    if dynamic:
        program_headers.append(
            struct.pack(
                "<IIQQQQQQ",
                2,
                6,
                dynamic_offset,
                0x400000 + dynamic_offset,
                0x400000 + dynamic_offset,
                len(dynamic_bytes),
                len(dynamic_bytes),
                8,
            )
        )
    if gnu_relro:
        relro_size = (
            len(dynamic_bytes) if gnu_relro_size is None else gnu_relro_size
        )
        program_headers.append(
            struct.pack(
                "<IIQQQQQQ",
                0x6474E552,
                4,
                dynamic_offset,
                (
                    0x400000 + dynamic_offset
                    if gnu_relro_vaddr is None
                    else gnu_relro_vaddr
                ),
                (
                    0x400000 + dynamic_offset
                    if gnu_relro_vaddr is None
                    else gnu_relro_vaddr
                ),
                relro_size,
                relro_size,
                1,
            )
        )
    if gnu_stack_flags is not None:
        program_headers.append(
            struct.pack(
                "<IIQQQQQQ", 0x6474E551, gnu_stack_flags, 0, 0, 0, 0, 0, 16
            )
        )
    if interpreter is not None:
        interp_offset = string_offset_in_file + len(strings)
        program_headers.append(
            struct.pack(
                "<IIQQQQQQ",
                3,
                4,
                interp_offset,
                0x400000 + interp_offset,
                0x400000 + interp_offset,
                len(interpreter_bytes),
                len(interpreter_bytes),
                1,
            )
        )
    return b"".join((header, *program_headers, dynamic_bytes, bytes(strings), interpreter_bytes))


def _logical_stage() -> Path:
    engine = builder._candidate_json(ENGINE_POLICY)
    build_root = builder._candidate_roots(engine)["candidate_build_root"]
    return build_root / "stage-0123456789abcdef"


def _mock_candidate_native_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        builder,
        "_verified_candidate_native_snapshot",
        lambda _engine: nullcontext(SimpleNamespace(root_fd=-1, mounts=())),
    )


def _write_candidate_wheel(
    path: Path,
    inputs: dict[str, object],
    *,
    dist_info: str = "nautilus_trader-1.231.0.dist-info",
    extra_member: str | None = None,
    package_member: str = "nautilus_trader/__init__.py",
    package_payload: bytes = b"",
    name: str = "nautilus_trader",
    tag: str = "cp312-cp312-manylinux_2_39_x86_64",
    requirements: tuple[str, ...] | None = None,
) -> None:
    if requirements is None:
        requirements = _candidate_requires_dist()
    metadata = [
        "Metadata-Version: 2.4",
        f"Name: {name}",
        "Version: 1.231.0",
        "Requires-Python: >=3.12,<3.15",
        *(f"Requires-Dist: {requirement}" for requirement in requirements),
        "",
        "",
    ]
    members = {
        package_member: package_payload,
        f"{dist_info}/METADATA": "\n".join(metadata).encode(),
        f"{dist_info}/WHEEL": (
            "Wheel-Version: 1.0\n"
            "Generator: poetry-core 2.3.1\n"
            "Root-Is-Purelib: false\n"
            f"Tag: {tag}\n"
        ).encode(),
    }
    if extra_member is not None:
        members[extra_member] = b"foreign payload"
    record = io.StringIO(newline="")
    writer = csv.writer(record, lineterminator="\n")
    for member, payload in sorted(members.items()):
        digest = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=").decode()
        writer.writerow((member, f"sha256={digest}", len(payload)))
    record_name = f"{dist_info}/RECORD"
    writer.writerow((record_name, "", ""))
    members[record_name] = record.getvalue().encode()
    with zipfile.ZipFile(path, "w") as archive:
        for member, payload in members.items():
            archive.writestr(member, payload)


def _diagnostic_wheel(
    members: list[tuple[str, bytes]],
    *,
    timestamp: tuple[int, int, int, int, int, int] = (2026, 8, 24, 1, 2, 4),
    mode: int = 0o100644,
    compression: int = zipfile.ZIP_STORED,
    comment: bytes = b"",
) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.comment = comment
        for name, payload in members:
            info = zipfile.ZipInfo(name, timestamp)
            info.compress_type = compression
            info.create_system = 3
            info.external_attr = mode << 16
            archive.writestr(info, payload)
    return output.getvalue()


LOCAL = b"PK\x03\x04"
CENTRAL = b"PK\x01\x02"
EOCD = b"PK\x05\x06"


def _nth_zip_record(payload: bytes, signature: bytes, index: int) -> int:
    cursor = 0
    for _occurrence in range(index + 1):
        cursor = payload.find(signature, cursor)
        assert cursor >= 0
        if _occurrence != index:
            cursor += len(signature)
    return cursor


def _central_record_end(payload: bytes, record: int) -> int:
    name_size, extra_size, comment_size = struct.unpack_from(
        "<3H", payload, record + 28
    )
    return record + 46 + name_size + extra_size + comment_size


def _wheel_with_inserted_central_record(payload: bytes, record: bytes) -> bytes:
    mutated = bytearray(payload)
    eocd = payload.rfind(EOCD)
    assert eocd >= 0
    mutated[eocd:eocd] = record
    updated_eocd = eocd + len(record)
    disk_count, total_count = struct.unpack_from("<HH", mutated, updated_eocd + 8)
    central_size = struct.unpack_from("<L", mutated, updated_eocd + 12)[0]
    struct.pack_into(
        "<HH", mutated, updated_eocd + 8, disk_count + 1, total_count + 1
    )
    struct.pack_into("<L", mutated, updated_eocd + 12, central_size + len(record))
    return bytes(mutated)


def _wheel_with_removed_central_record(payload: bytes, index: int) -> bytes:
    record = _nth_zip_record(payload, CENTRAL, index)
    record_end = _central_record_end(payload, record)
    removed_size = record_end - record
    mutated = bytearray(payload)
    del mutated[record:record_end]
    eocd = mutated.rfind(EOCD)
    assert eocd >= 0
    disk_count, total_count = struct.unpack_from("<HH", mutated, eocd + 8)
    central_size = struct.unpack_from("<L", mutated, eocd + 12)[0]
    struct.pack_into(
        "<HH", mutated, eocd + 8, disk_count - 1, total_count - 1
    )
    struct.pack_into("<L", mutated, eocd + 12, central_size - removed_size)
    return bytes(mutated)


def _wheel_with_duplicated_central_record(payload: bytes, index: int = 0) -> bytes:
    record = _nth_zip_record(payload, CENTRAL, index)
    return _wheel_with_inserted_central_record(
        payload, payload[record : _central_record_end(payload, record)]
    )


def _wheel_with_nth_record_field(
    payload: bytes,
    signature: bytes,
    index: int,
    offset: int,
    format: str,
    value: int,
) -> bytes:
    mutated = bytearray(payload)
    record = _nth_zip_record(payload, signature, index)
    struct.pack_into(format, mutated, record + offset, value)
    return bytes(mutated)


def _wheel_with_paired_record_field(
    payload: bytes,
    index: int,
    local_offset: int,
    central_offset: int,
    format: str,
    value: int,
) -> bytes:
    central = _nth_zip_record(payload, CENTRAL, index)
    local = struct.unpack_from("<L", payload, central + 42)[0]
    mutated = bytearray(payload)
    struct.pack_into(format, mutated, local + local_offset, value)
    struct.pack_into(format, mutated, central + central_offset, value)
    return bytes(mutated)


def _wheel_with_local_record_delta(
    payload: bytes,
    index: int,
    local_offset: int,
    central_offset: int,
    format: str,
    delta: int,
) -> bytes:
    central = _nth_zip_record(payload, CENTRAL, index)
    current = struct.unpack_from(format, payload, central + central_offset)[0]
    return _wheel_with_nth_record_field(
        payload, LOCAL, index, local_offset, format, current + delta
    )


def _wheel_with_extra_field(
    payload: bytes, signature: bytes, extra: bytes
) -> bytes:
    mutated = bytearray(payload)
    record = payload.find(signature)
    assert record >= 0
    if signature == LOCAL:
        fixed_size, name_offset, extra_offset = 30, 26, 28
    else:
        assert signature == CENTRAL
        fixed_size, name_offset, extra_offset = 46, 28, 30
    name_size, extra_size = struct.unpack_from(
        "<HH", payload, record + name_offset
    )
    insertion = record + fixed_size + name_size + extra_size
    mutated[insertion:insertion] = extra
    struct.pack_into("<H", mutated, record + extra_offset, extra_size + len(extra))
    eocd = mutated.rfind(EOCD)
    assert eocd >= 0
    eocd_field = 16 if signature == LOCAL else 12
    value = struct.unpack_from("<L", mutated, eocd + eocd_field)[0]
    struct.pack_into("<L", mutated, eocd + eocd_field, value + len(extra))
    return bytes(mutated)


def _wheel_with_data_descriptor(payload: bytes) -> bytes:
    central = _nth_zip_record(payload, CENTRAL, 0)
    local = struct.unpack_from("<L", payload, central + 42)[0]
    crc, compressed_size, uncompressed_size = struct.unpack_from(
        "<3L", payload, central + 16
    )
    local_name_size, local_extra_size = struct.unpack_from(
        "<HH", payload, local + 26
    )
    data_start = local + 30 + local_name_size + local_extra_size
    assert data_start + compressed_size == central
    flags = struct.unpack_from("<H", payload, central + 8)[0] | 0x0008
    flagged = _wheel_with_paired_record_field(payload, 0, 6, 8, "<H", flags)
    descriptor = struct.pack(
        "<4s3L", b"PK\x07\x08", crc, compressed_size, uncompressed_size
    )
    mutated = bytearray(flagged)
    mutated[central:central] = descriptor
    eocd = mutated.rfind(EOCD)
    central_offset = struct.unpack_from("<L", mutated, eocd + 16)[0]
    struct.pack_into("<L", mutated, eocd + 16, central_offset + len(descriptor))
    return bytes(mutated)


def _wheel_with_local_padding(
    payload: bytes, *, before_member: int, padding: bytes = b"gap"
) -> bytes:
    eocd = payload.rfind(EOCD)
    assert eocd >= 0
    member_count = struct.unpack_from("<H", payload, eocd + 10)[0]
    central_offset = struct.unpack_from("<L", payload, eocd + 16)[0]
    central_records = [
        _nth_zip_record(payload, CENTRAL, index) for index in range(member_count)
    ]
    local_offsets = [
        struct.unpack_from("<L", payload, record + 42)[0]
        for record in central_records
    ]
    insertion = 0 if before_member == 0 else local_offsets[before_member]
    mutated = bytearray(payload)
    mutated[insertion:insertion] = padding
    updated_central = central_offset + len(padding)
    cursor = updated_central
    for old_local in local_offsets:
        new_local = old_local + (len(padding) if old_local >= insertion else 0)
        struct.pack_into("<L", mutated, cursor + 42, new_local)
        cursor = _central_record_end(bytes(mutated), cursor)
    updated_eocd = mutated.rfind(EOCD)
    struct.pack_into("<L", mutated, updated_eocd + 16, updated_central)
    return bytes(mutated)


def _wheel_with_first_region_overlap(payload: bytes) -> bytes:
    first_central = _nth_zip_record(payload, CENTRAL, 0)
    second_central = _nth_zip_record(payload, CENTRAL, 1)
    first_local = struct.unpack_from("<L", payload, first_central + 42)[0]
    second_local = struct.unpack_from("<L", payload, second_central + 42)[0]
    name_size, extra_size = struct.unpack_from("<HH", payload, first_local + 26)
    data_start = first_local + 30 + name_size + extra_size
    return _wheel_with_paired_record_field(
        payload, 0, 18, 20, "<L", second_local - data_start + 1
    )


def _wheel_with_data_extending_into_central(payload: bytes) -> bytes:
    central = _nth_zip_record(payload, CENTRAL, 0)
    local = struct.unpack_from("<L", payload, central + 42)[0]
    name_size, extra_size = struct.unpack_from("<HH", payload, local + 26)
    data_start = local + 30 + name_size + extra_size
    return _wheel_with_paired_record_field(
        payload, 0, 18, 20, "<L", central - data_start + 1
    )


def _candidate_structural_preflight_for_test(payload: bytes) -> dict[str, int]:
    return builder._candidate_wheel_structural_preflight(payload)


def _candidate_raw_wheel_diagnostic_for_test(
    first_payload: bytes, second_payload: bytes
) -> dict[str, object]:
    return builder._candidate_raw_wheel_diagnostic(
        first_payload,
        second_payload,
        _candidate_structural_preflight_for_test(first_payload),
        _candidate_structural_preflight_for_test(second_payload),
    )


def _empty_candidate_preflight() -> dict[str, int]:
    return {
        "compressed_size": 0,
        "declared_uncompressed_size": 0,
        "invalid_member_size_count": 0,
        "member_count": 0,
        "streamed_expanded_bytes": 0,
    }


def _write_x4_authority_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    mutation: str | None = None,
) -> tuple[Path, str, dict[str, object], dict[str, object], dict[str, Path]]:
    build_root = tmp_path / "build-root"
    build_root.mkdir(mode=0o700)
    roots = {
        "candidate_build_root": build_root,
        "candidate_forensic_root": tmp_path / "forensic",
        "candidate_runtime_root": tmp_path / "runtime",
        "rollback_root": tmp_path / "rollback",
    }
    inputs = {
        "candidate": {
            "upstream_commit": "27a8e54e7ac3c57d6cbf8891f0283dfbaee97317"
        },
        "native_build_environment": {"construction": "EMPTY_THEN_SET_EXACT_VALUES"},
        "policy_hashes": {
            "cargo_registry_policy_sha256": "1" * 64,
            "engine_build_policy_sha256": "2" * 64,
            "input_cache_policy_sha256": "3" * 64,
            "release_provenance_policy_sha256": "4" * 64,
            "wheel_cache_policy_sha256": "5" * 64,
        },
        "source": {
            "artifact": {
                "sha256": "a141c913d9c00ef18ac78a416bddfeef85fa06ebd172d98fdd752ad2c5957441"
            }
        },
    }
    engine = {
        "candidate": {
            "upstream_commit": "27a8e54e7ac3c57d6cbf8891f0283dfbaee97317"
        },
        "external_cache_isolation": {
            "external_roots": {name: str(path) for name, path in roots.items()}
        }
    }
    complete = tmp_path / "task-4-receipt.json"
    complete.write_bytes(b"{}\n")
    complete.chmod(0o400)
    receipt_document: dict[str, object] = {
        "build_a_authorized": True,
        "candidate": {"head": "a" * 40, "tree": "b" * 40},
        "complete_authority_receipt": {
            "path": "task-4-receipt.json",
            "sha256": hashlib.sha256(b"{}\n").hexdigest(),
            "size": 3,
        },
        "checks": {
            "ambient_fallback_reachable": False,
            "build_parent": {
                "empty": True,
                "gid": os.getegid(),
                "mode": "0700",
                "owner": os.geteuid(),
            },
            "candidate_output_roots": {
                "artifact_root": "ABSENT",
                "closure_root": "ABSENT",
                "forensic_root": "ABSENT",
            },
            "host_authority_lane": {
                "environment": {"TEMP": "/tmp", "TMP": "/tmp", "TMPDIR": "/tmp"},
                "exit_code": 0,
                "reason": "HOST_TESTS_PASSED",
                "result": "PASS",
            },
            "network_capability": "DISABLED_BY_BUBBLEWRAP_UNSHARE_ALL",
            "release_provenance": {
                "exit_code": 0,
                "network": "DISABLED_BY_CONSTRUCTION",
                "peeled_commit": "27a8e54e7ac3c57d6cbf8891f0283dfbaee97317",
                "primary_sha256": "a141c913d9c00ef18ac78a416bddfeef85fa06ebd172d98fdd752ad2c5957441",
                "result": "PASS",
                "tag_object": "d3e1685e979925d7b0ffacd1b3f442547686e18f",
                "wheel_sha256": "8c438e95c275a13df0c0ddb7012c462708b5e99ff3612e36a1b7bd49ab39c216",
            },
            "rollback_authority": {
                "artifact_generation": "artifacts/fixture",
                "artifact_manifest_sha256": "b" * 64,
                "closure_sha256": "c" * 64,
                "generation": "runtime-fixture",
                "manifest_mode": "0400",
                "manifest_sha256": "d" * 64,
                "result": "PASS",
                "schema": 6,
            },
            "roots_disjoint": True,
            "toolchain_inputs": {
                "exit_code": 0,
                "result": "PASS",
                "sha256": "6" * 64,
            },
        },
        "identities": {
            "bubblewrap": {"version": "bubblewrap 0.9.0"},
            "cargo": {"version": "cargo 1.97.1"},
            "cpython": {"version": "CPython 3.12.3"},
            "llvm": {"version": "clang 22.1.3"},
            "rustc": {"version": "rustc 1.97.1"},
        },
        "policy_sha256": {
            "cargo_registry": "1" * 64,
            "engine_build": "2" * 64,
            "input_cache": "3" * 64,
            "release_provenance": "4" * 64,
            "wheel_cache": "5" * 64,
        },
        "recorded_at_utc": "2026-08-25T21:55:08Z",
        "review_round": 2,
        "schema": "p1-u04-x4-authority-preflight-v1",
        "verdict": "X4_READY_FOR_BUILD_A",
    }
    monkeypatch.setattr(
        builder,
        "_candidate_live_rollback_authority",
        lambda _rollback_root: copy.deepcopy(
            receipt_document["checks"]["rollback_authority"]
        ),
        raising=False,
    )
    expected_digest = "0" * 64 if mutation == "digest" else None
    if mutation == "schema":
        receipt_document["schema"] = "foreign"
    elif mutation == "verdict":
        receipt_document["verdict"] = "DEFERRED"
    elif mutation == "head":
        receipt_document["candidate"]["head"] = "c" * 40  # type: ignore[index]
    elif mutation == "tree":
        receipt_document["candidate"]["tree"] = "d" * 40  # type: ignore[index]
    elif mutation == "checks":
        receipt_document["checks"]["roots_disjoint"] = False  # type: ignore[index]
    elif mutation == "release_check":
        receipt_document["checks"]["release_provenance"]["tag_object"] = "e" * 40  # type: ignore[index]
    elif mutation == "synthetic_toolchain_policy":
        receipt_document["policy_sha256"]["toolchain_inputs"] = "6" * 64  # type: ignore[index]
    elif mutation == "network_capability":
        receipt_document["checks"]["network_capability"] = "AMBIENT"  # type: ignore[index]
    receipt = tmp_path / "x4-receipt.json"
    raw = (json.dumps(receipt_document, sort_keys=True, indent=2) + "\n").encode(
        "ascii"
    )
    receipt.write_bytes(raw)
    receipt.chmod(0o400)
    if mutation == "mode":
        receipt.chmod(0o600)
    elif mutation == "link":
        link = tmp_path / "x4-receipt-link.json"
        os.link(receipt, link)
    monkeypatch.setattr(builder, "_X4_COMPLETE_AUTHORITY_RECEIPT", complete, raising=False)
    monkeypatch.setattr(
        builder, "_X4_COMPLETE_AUTHORITY_RECEIPT_PATH", "task-4-receipt.json", raising=False
    )
    monkeypatch.setattr(
        builder,
        "_candidate_git_identity",
        lambda: {"head": "a" * 40, "tree": "b" * 40},
        raising=False,
    )
    monkeypatch.setattr(
        builder,
        "_candidate_external_identities",
        lambda *_args: receipt_document["identities"],
        raising=False,
    )
    monkeypatch.setattr(builder, "_verify_candidate_authority", lambda: (engine, inputs))
    real_sha256 = builder._sha256
    monkeypatch.setattr(
        builder,
        "_sha256",
        lambda path: "6" * 64
        if path == builder._CANDIDATE_TOOLCHAIN_INPUTS
        else real_sha256(path),
    )
    return receipt, expected_digest or hashlib.sha256(raw).hexdigest(), engine, inputs, roots


def _candidate_runtime_closure_loader_probe(
    checkout: Path, tool: Path, probe: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "\n".join(
                (
                    "import sys",
                    "from pathlib import Path",
                    "import scripts.build_nautilus_engine as builder",
                    "builder._ROOT = Path(sys.argv[1])",
                    "builder._CANDIDATE_RUNTIME_CLOSURE_TOOL = Path(sys.argv[2])",
                    probe,
                )
            ),
            str(checkout),
            str(tool),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_candidate_runtime_closure_loader_ignores_metadata_valid_bytecode(
    tmp_path: Path,
) -> None:
    checkout = tmp_path / "checkout"
    tool = checkout / "scripts/materialize_nautilus_runtime_closure.py"
    authority = checkout / "services"
    authority.mkdir(parents=True)
    tool.parent.mkdir()
    (authority / "__init__.py").write_bytes(b"")
    (authority / "fake_authority.py").write_bytes(b'PROVENANCE = "reviewed"\n')
    reviewed = b"from services.fake_authority import PROVENANCE\n"
    poisoned = b'PROVENANCE = "poisoned"\n'.ljust(len(reviewed), b" ")
    assert len(poisoned) == len(reviewed)
    tool.write_bytes(poisoned)
    timestamp = 2_000_000_000
    os.utime(tool, (timestamp, timestamp))
    cached = Path(
        py_compile.compile(
            str(tool),
            doraise=True,
            invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP,
        )
    )
    tool.write_bytes(reviewed)
    os.utime(tool, (timestamp, timestamp))
    assert struct.unpack("<LL", cached.read_bytes()[8:16]) == (
        timestamp,
        len(reviewed),
    )

    result = _candidate_runtime_closure_loader_probe(
        checkout,
        tool,
        "print(builder._load_candidate_runtime_closure_tool().PROVENANCE)",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "reviewed\n"


def test_candidate_runtime_closure_loader_rejects_preloaded_checkout_authority(
    tmp_path: Path,
) -> None:
    checkout = tmp_path / "checkout"
    tool = checkout / "scripts/materialize_nautilus_runtime_closure.py"
    tool.parent.mkdir(parents=True)
    tool.write_bytes(b"from services.fake_authority import PROVENANCE\n")
    probe = "\n".join(
        (
            "import types",
            'services = types.ModuleType("services")',
            "services.__path__ = []",
            'authority = types.ModuleType("services.fake_authority")',
            'authority.PROVENANCE = "preloaded"',
            'sys.modules["services"] = services',
            'sys.modules["services.fake_authority"] = authority',
            "try:",
            "    builder._load_candidate_runtime_closure_tool()",
            "except builder.VerificationError as exc:",
            '    print(f"rejected:{exc}")',
            "else:",
            '    print("accepted")',
        )
    )

    result = _candidate_runtime_closure_loader_probe(checkout, tool, probe)

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("rejected:")
    assert "preloaded" in result.stdout


def test_candidate_runtime_closure_loader_rejects_authority_outside_checkout(
    tmp_path: Path,
) -> None:
    checkout = tmp_path / "checkout"
    tool = checkout / "scripts/materialize_nautilus_runtime_closure.py"
    outside = tmp_path / "outside"
    (outside / "services").mkdir(parents=True)
    tool.parent.mkdir(parents=True)
    (outside / "services/__init__.py").write_bytes(b"")
    (outside / "services/fake_authority.py").write_bytes(
        b'PROVENANCE = "outside"\n'
    )
    tool.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(outside)!r})\n"
        "from services.fake_authority import PROVENANCE\n",
        encoding="ascii",
    )
    probe = "\n".join(
        (
            "try:",
            "    builder._load_candidate_runtime_closure_tool()",
            "except builder.VerificationError as exc:",
            '    print(f"rejected:{exc}")',
            "else:",
            '    print("accepted")',
        )
    )

    result = _candidate_runtime_closure_loader_probe(checkout, tool, probe)

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("rejected:")
    assert "provenance" in result.stdout


def test_canonical_x4_authority_receipt_with_checks_is_accepted(
    x4_posix_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    receipt, receipt_sha256, _engine, _inputs, _roots = _write_x4_authority_receipt(
        x4_posix_tmp_path, monkeypatch
    )

    validated = builder._validate_x4_authority_receipt(
        receipt, receipt_sha256, phase="A"
    )

    assert validated["schema"] == "p1-u04-x4-authority-preflight-v1"
    assert validated["checks"] == {
        "ambient_fallback_reachable": False,
        "build_parent": {
            "empty": True,
            "gid": os.getegid(),
            "mode": "0700",
            "owner": os.geteuid(),
        },
        "candidate_output_roots": {
            "artifact_root": "ABSENT",
            "closure_root": "ABSENT",
            "forensic_root": "ABSENT",
        },
        "host_authority_lane": {
            "environment": {"TEMP": "/tmp", "TMP": "/tmp", "TMPDIR": "/tmp"},
            "exit_code": 0,
            "reason": "HOST_TESTS_PASSED",
            "result": "PASS",
        },
        "network_capability": "DISABLED_BY_BUBBLEWRAP_UNSHARE_ALL",
        "release_provenance": {
            "exit_code": 0,
            "network": "DISABLED_BY_CONSTRUCTION",
            "peeled_commit": "27a8e54e7ac3c57d6cbf8891f0283dfbaee97317",
            "primary_sha256": "a141c913d9c00ef18ac78a416bddfeef85fa06ebd172d98fdd752ad2c5957441",
            "result": "PASS",
            "tag_object": "d3e1685e979925d7b0ffacd1b3f442547686e18f",
            "wheel_sha256": "8c438e95c275a13df0c0ddb7012c462708b5e99ff3612e36a1b7bd49ab39c216",
        },
        "rollback_authority": {
            "artifact_generation": "artifacts/fixture",
            "artifact_manifest_sha256": "b" * 64,
            "closure_sha256": "c" * 64,
            "generation": "runtime-fixture",
            "manifest_mode": "0400",
            "manifest_sha256": "d" * 64,
            "result": "PASS",
            "schema": 6,
        },
        "roots_disjoint": True,
        "toolchain_inputs": {
            "exit_code": 0,
            "result": "PASS",
            "sha256": "6" * 64,
        },
    }


@pytest.mark.parametrize(
    "mutation", ("synthetic_toolchain_policy", "network_capability")
)
def test_candidate_x4_receipt_rejects_noncanonical_policy_and_network_shape(
    x4_posix_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    receipt, digest, *_ = _write_x4_authority_receipt(
        x4_posix_tmp_path, monkeypatch, mutation=mutation
    )

    with pytest.raises(builder.VerificationError, match="X4 authority receipt"):
        builder._validate_x4_authority_receipt(receipt, digest, phase="A")


def test_candidate_git_identity_pins_fsmonitor_and_hooks_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict[str, str]]] = []
    outputs = iter(("", "a" * 40, "b" * 40))

    def fake_run(command: list[str], **kwargs):
        calls.append((command, kwargs["env"]))
        return subprocess.CompletedProcess(command, 0, next(outputs), "")

    monkeypatch.setattr(builder.subprocess, "run", fake_run)

    assert builder._candidate_git_identity() == {
        "head": "a" * 40,
        "tree": "b" * 40,
    }
    assert calls == [
        (
            [
                "/usr/bin/git",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.hooksPath=/dev/null",
                "-C",
                str(builder._ROOT),
                *arguments,
            ],
            {"LC_ALL": "C", "LANG": "C"},
        )
        for arguments in (
            ("status", "--porcelain=v1", "--untracked-files=no"),
            ("rev-parse", "HEAD"),
            ("rev-parse", "HEAD^{tree}"),
        )
    ]


def test_candidate_build_a_runs_one_build_and_never_publishes_final_artifacts(
    x4_posix_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tmp_path = x4_posix_tmp_path
    receipt, receipt_sha256, engine, inputs, roots = _write_x4_authority_receipt(
        tmp_path, monkeypatch
    )
    descriptor = os.open(tmp_path, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW)
    build_calls: list[str] = []

    def fake_build(*_args):
        build_calls.append("A")
        return (
            b"wheel-a",
            _empty_candidate_preflight(),
            {"wheel": {"filename": WHEEL_FILENAME, "sha256": hashlib.sha256(b"wheel-a").hexdigest(), "size": 7}},
            {"P1_U04_SOURCE_ST_DEV": "1", "P1_U04_SOURCE_ST_INO": "2"},
            descriptor,
        )

    monkeypatch.setattr(builder, "_materialize_candidate_inputs", lambda *_args: roots)
    monkeypatch.setattr(builder, "_build_candidate_once", fake_build)
    monkeypatch.setattr(
        builder,
        "_candidate_process_identity",
        lambda: {
            "boot_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "pid": 100,
            "start_time_ticks": 200,
        },
        raising=False,
    )

    result = builder.build_candidate_a(
        authority_receipt=receipt,
        authority_receipt_sha256=receipt_sha256,
    )

    assert build_calls == ["A"]
    assert result["kind"] == "P1_U04_BUILD_A"
    assert result["candidate"] == {"head": "a" * 40, "tree": "b" * 40}
    assert result["policy_sha256"] == {
        "cargo_registry": "1" * 64,
        "engine_build": "2" * 64,
        "input_cache": "3" * 64,
        "release_provenance": "4" * 64,
        "wheel_cache": "5" * 64,
    }
    assert result["authority_identities"] == {
        "bubblewrap": {"version": "bubblewrap 0.9.0"},
        "cargo": {"version": "cargo 1.97.1"},
        "cpython": {"version": "CPython 3.12.3"},
        "llvm": {"version": "clang 22.1.3"},
        "rustc": {"version": "rustc 1.97.1"},
    }
    assert (roots["candidate_build_root"] / "build-a").is_dir()
    assert not (roots["candidate_build_root"] / "build-b").exists()
    assert not (roots["candidate_build_root"] / "artifacts").exists()


@pytest.mark.parametrize("drifted_binding", ("candidate", "policy_sha256", "identities"))
def test_candidate_build_a_revalidates_exact_x4_bindings_before_publication(
    x4_posix_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drifted_binding: str,
) -> None:
    receipt, receipt_sha256, _engine, _inputs, roots = _write_x4_authority_receipt(
        x4_posix_tmp_path, monkeypatch
    )
    descriptor = os.open(x4_posix_tmp_path, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW)
    monkeypatch.setattr(builder, "_materialize_candidate_inputs", lambda *_args: roots)
    monkeypatch.setattr(
        builder,
        "_build_candidate_once",
        lambda *_args: (
            b"wheel-a",
            _empty_candidate_preflight(),
            {
                "wheel": {
                    "filename": WHEEL_FILENAME,
                    "sha256": hashlib.sha256(b"wheel-a").hexdigest(),
                    "size": 7,
                }
            },
            {"P1_U04_SOURCE_ST_DEV": "1", "P1_U04_SOURCE_ST_INO": "2"},
            descriptor,
        ),
    )
    monkeypatch.setattr(
        builder,
        "_candidate_process_identity",
        lambda: {
            "boot_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "pid": 100,
            "start_time_ticks": 200,
        },
    )
    real_validate = builder._validate_x4_authority_receipt
    validation_count = 0

    def validate_with_drift(*args, **kwargs):
        nonlocal validation_count
        validated = real_validate(*args, **kwargs)
        validation_count += 1
        if validation_count == 2:
            validated = copy.deepcopy(validated)
            if drifted_binding == "candidate":
                validated["candidate"]["head"] = "c" * 40
            elif drifted_binding == "policy_sha256":
                validated["policy_sha256"]["engine_build"] = "d" * 64
            else:
                validated["identities"]["cargo"]["version"] = "cargo drifted"
        return validated

    monkeypatch.setattr(builder, "_validate_x4_authority_receipt", validate_with_drift)

    with pytest.raises(builder.VerificationError, match="validated X4"):
        builder.build_candidate_a(
            authority_receipt=receipt,
            authority_receipt_sha256=receipt_sha256,
        )

    assert validation_count == 2
    assert not (roots["candidate_build_root"] / "build-a").exists()


def test_candidate_build_a_rejects_live_rollback_drift_before_publication(
    x4_posix_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt, receipt_sha256, _engine, _inputs, roots = _write_x4_authority_receipt(
        x4_posix_tmp_path, monkeypatch
    )
    recorded = copy.deepcopy(
        json.loads(receipt.read_bytes())["checks"]["rollback_authority"]
    )
    calls = 0

    def live_projection(_rollback_root: Path) -> dict[str, object]:
        nonlocal calls
        calls += 1
        observed = copy.deepcopy(recorded)
        if calls == 2:
            observed["closure_sha256"] = "9" * 64
        return observed

    monkeypatch.setattr(
        builder, "_candidate_live_rollback_authority", live_projection, raising=False
    )
    descriptor = os.open(
        x4_posix_tmp_path, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW
    )
    monkeypatch.setattr(builder, "_materialize_candidate_inputs", lambda *_args: roots)
    monkeypatch.setattr(
        builder,
        "_build_candidate_once",
        lambda *_args: (
            b"wheel-a",
            _empty_candidate_preflight(),
            {
                "wheel": {
                    "filename": WHEEL_FILENAME,
                    "sha256": hashlib.sha256(b"wheel-a").hexdigest(),
                    "size": 7,
                }
            },
            {"P1_U04_SOURCE_ST_DEV": "1", "P1_U04_SOURCE_ST_INO": "2"},
            descriptor,
        ),
    )
    monkeypatch.setattr(
        builder,
        "_candidate_process_identity",
        lambda: {
            "boot_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "pid": 100,
            "start_time_ticks": 200,
        },
    )
    with pytest.raises(builder.VerificationError, match="live rollback authority drifted"):
        builder.build_candidate_a(
            authority_receipt=receipt,
            authority_receipt_sha256=receipt_sha256,
        )
    assert calls == 2
    assert not (roots["candidate_build_root"] / "build-a").exists()


def test_candidate_build_b_requires_a_different_process_and_same_x4_receipt(
    x4_posix_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tmp_path = x4_posix_tmp_path
    receipt, receipt_sha256, _engine, inputs, roots = _write_x4_authority_receipt(
        tmp_path, monkeypatch
    )
    process_identity = {
        "boot_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "pid": 100,
        "start_time_ticks": 200,
    }
    builder._publish_candidate_build_result(
        roots,
        label="A",
        wheel_payload=b"wheel-a",
        artifact_core={"wheel": {"filename": WHEEL_FILENAME, "sha256": hashlib.sha256(b"wheel-a").hexdigest(), "size": 7}},
        source_identity={"P1_U04_SOURCE_ST_DEV": "1", "P1_U04_SOURCE_ST_INO": "2"},
        process_identity=process_identity,
        x4_authority=json.loads(receipt.read_bytes()),
        x4_receipt_sha256=receipt_sha256,
    )
    monkeypatch.setattr(
        builder, "_materialize_candidate_inputs", lambda *_args, **_kwargs: roots
    )
    monkeypatch.setattr(builder, "_candidate_process_identity", lambda: process_identity)

    with pytest.raises(builder.VerificationError, match="distinct process"):
        builder.build_candidate_b(
            authority_receipt=receipt,
            authority_receipt_sha256=receipt_sha256,
        )


def test_candidate_build_b_rejects_modified_build_a_and_leaves_final_absent(
    x4_posix_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tmp_path = x4_posix_tmp_path
    receipt, receipt_sha256, _engine, _inputs, roots = _write_x4_authority_receipt(
        tmp_path, monkeypatch
    )
    builder._publish_candidate_build_result(
        roots,
        label="A",
        wheel_payload=b"wheel-a",
        artifact_core={
            "wheel": {
                "filename": WHEEL_FILENAME,
                "sha256": hashlib.sha256(b"wheel-a").hexdigest(),
                "size": 7,
            }
        },
        source_identity={"P1_U04_SOURCE_ST_DEV": "1", "P1_U04_SOURCE_ST_INO": "2"},
        process_identity={
            "boot_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "pid": 100,
            "start_time_ticks": 200,
        },
        x4_authority=json.loads(receipt.read_bytes()),
        x4_receipt_sha256=receipt_sha256,
    )
    wheel = roots["candidate_build_root"] / "build-a" / WHEEL_FILENAME
    wheel.chmod(0o600)
    wheel.write_bytes(b"modified")
    wheel.chmod(0o400)
    monkeypatch.setattr(
        builder, "_materialize_candidate_inputs", lambda *_args, **_kwargs: roots
    )

    with pytest.raises(builder.VerificationError, match="Build A"):
        builder.build_candidate_b(
            authority_receipt=receipt,
            authority_receipt_sha256=receipt_sha256,
        )

    assert not (roots["candidate_build_root"] / "artifacts").exists()


def test_candidate_build_b_rejects_build_a_x4_binding_drift_before_native_build(
    x4_posix_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    receipt, receipt_sha256, _engine, _inputs, roots = _write_x4_authority_receipt(
        x4_posix_tmp_path, monkeypatch
    )
    builder._publish_candidate_build_result(
        roots,
        label="A",
        wheel_payload=b"wheel-a",
        artifact_core={
            "wheel": {
                "filename": WHEEL_FILENAME,
                "sha256": hashlib.sha256(b"wheel-a").hexdigest(),
                "size": 7,
            }
        },
        source_identity={"P1_U04_SOURCE_ST_DEV": "1", "P1_U04_SOURCE_ST_INO": "2"},
        process_identity={
            "boot_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "pid": 100,
            "start_time_ticks": 200,
        },
        x4_authority=json.loads(receipt.read_bytes()),
        x4_receipt_sha256=receipt_sha256,
    )
    build_a_receipt = (
        roots["candidate_build_root"] / "build-a" / "build-receipt.json"
    )
    build_a_receipt.chmod(0o600)
    drifted = json.loads(build_a_receipt.read_bytes())
    drifted["authority_identities"]["cargo"]["version"] = "cargo drifted"
    build_a_receipt.write_text(
        json.dumps(drifted, sort_keys=True, indent=2) + "\n", encoding="ascii"
    )
    build_a_receipt.chmod(0o400)
    monkeypatch.setattr(
        builder, "_materialize_candidate_inputs", lambda *_args, **_kwargs: roots
    )
    monkeypatch.setattr(
        builder,
        "_build_candidate_once",
        lambda *_args: pytest.fail("Build A X4 binding drift reached native build"),
    )

    with pytest.raises(builder.VerificationError, match="Build A X4"):
        builder.build_candidate_b(
            authority_receipt=receipt,
            authority_receipt_sha256=receipt_sha256,
        )

    assert not (roots["candidate_build_root"] / "artifacts").exists()


def test_candidate_build_b_runs_one_build_and_publishes_build_a_final_artifact(
    x4_posix_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tmp_path = x4_posix_tmp_path
    receipt, receipt_sha256, _engine, _inputs, roots = _write_x4_authority_receipt(
        tmp_path, monkeypatch
    )
    payload = b"wheel-a"
    core = {
        "wheel": {
            "filename": WHEEL_FILENAME,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size": len(payload),
        }
    }
    builder._publish_candidate_build_result(
        roots,
        label="A",
        wheel_payload=payload,
        artifact_core=core,
        source_identity={"P1_U04_SOURCE_ST_DEV": "1", "P1_U04_SOURCE_ST_INO": "2"},
        process_identity={
            "boot_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "pid": 100,
            "start_time_ticks": 200,
        },
        x4_authority=json.loads(receipt.read_bytes()),
        x4_receipt_sha256=receipt_sha256,
    )
    descriptor = os.open(tmp_path, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW)
    build_calls: list[str] = []
    monkeypatch.setattr(
        builder, "_materialize_candidate_inputs", lambda *_args, **_kwargs: roots
    )
    monkeypatch.setattr(
        builder,
        "_candidate_process_identity",
        lambda: {
            "boot_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "pid": 101,
            "start_time_ticks": 201,
        },
    )

    def fake_build(*_args):
        build_calls.append("B")
        return (
            payload,
            _empty_candidate_preflight(),
            core,
            {"P1_U04_SOURCE_ST_DEV": "1", "P1_U04_SOURCE_ST_INO": "3"},
            descriptor,
        )

    monkeypatch.setattr(builder, "_build_candidate_once", fake_build)

    manifest = builder.build_candidate_b(
        authority_receipt=receipt,
        authority_receipt_sha256=receipt_sha256,
    )

    assert build_calls == ["B"]
    build_a_receipt = (
        roots["candidate_build_root"] / "build-a" / "build-receipt.json"
    )
    build_b_receipt = (
        roots["candidate_build_root"] / "build-b" / "build-receipt.json"
    )
    assert manifest["reproducible_build"] == {
        "authoritative_manifest_equality": True,
        "build_a_receipt_sha256": hashlib.sha256(
            build_a_receipt.read_bytes()
        ).hexdigest(),
        "build_b_receipt_sha256": hashlib.sha256(
            build_b_receipt.read_bytes()
        ).hexdigest(),
        "build_count": 2,
        "fresh_physical_stages": True,
        "logical_stages_absent_after_build": True,
        "native_inventory_equality": True,
        "process_identities": [
            {
                "boot_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "pid": 100,
                "start_time_ticks": 200,
            },
            {
                "boot_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "pid": 101,
                "start_time_ticks": 201,
            },
        ],
        "raw_wheel_equality": True,
        "source_fd_identities": [
            {"P1_U04_SOURCE_ST_DEV": "1", "P1_U04_SOURCE_ST_INO": "2"},
            {"P1_U04_SOURCE_ST_DEV": "1", "P1_U04_SOURCE_ST_INO": "3"},
        ],
        "wheel_sha256": hashlib.sha256(payload).hexdigest(),
        "x4_authority_receipt_sha256": receipt_sha256,
    }
    assert (roots["candidate_build_root"] / "artifacts" / WHEEL_FILENAME).read_bytes() == payload


def test_candidate_build_b_final_receipt_digests_come_from_final_validated_loads(
    x4_posix_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    receipt, receipt_sha256, _engine, _inputs, roots = _write_x4_authority_receipt(
        x4_posix_tmp_path, monkeypatch
    )
    payload = b"wheel-a"
    core = {
        "wheel": {
            "filename": WHEEL_FILENAME,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size": len(payload),
        }
    }
    builder._publish_candidate_build_result(
        roots,
        label="A",
        wheel_payload=payload,
        artifact_core=core,
        source_identity={"P1_U04_SOURCE_ST_DEV": "1", "P1_U04_SOURCE_ST_INO": "2"},
        process_identity={
            "boot_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "pid": 100,
            "start_time_ticks": 200,
        },
        x4_authority=json.loads(receipt.read_bytes()),
        x4_receipt_sha256=receipt_sha256,
    )
    descriptor = os.open(
        x4_posix_tmp_path, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW
    )
    monkeypatch.setattr(
        builder, "_materialize_candidate_inputs", lambda *_args, **_kwargs: roots
    )
    monkeypatch.setattr(
        builder,
        "_candidate_process_identity",
        lambda: {
            "boot_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "pid": 101,
            "start_time_ticks": 201,
        },
    )
    monkeypatch.setattr(
        builder,
        "_build_candidate_once",
        lambda *_args: (
            payload,
            _empty_candidate_preflight(),
            core,
            {"P1_U04_SOURCE_ST_DEV": "1", "P1_U04_SOURCE_ST_INO": "3"},
            descriptor,
        ),
    )
    real_load = builder._load_candidate_build_result
    loads: list[tuple[str, str]] = []

    def record_load(roots, *, label):
        loaded = real_load(roots, label=label)
        loads.append((label, loaded[3]))
        return loaded

    monkeypatch.setattr(builder, "_load_candidate_build_result", record_load)

    manifest = builder.build_candidate_b(
        authority_receipt=receipt,
        authority_receipt_sha256=receipt_sha256,
    )

    assert [label for label, _digest in loads[-2:]] == ["A", "B"]
    assert manifest["reproducible_build"]["build_a_receipt_sha256"] == loads[-2][1]
    assert manifest["reproducible_build"]["build_b_receipt_sha256"] == loads[-1][1]


def test_candidate_build_b_final_barrier_rejects_build_a_drift(
    x4_posix_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    receipt, receipt_sha256, _engine, _inputs, roots = _write_x4_authority_receipt(
        x4_posix_tmp_path, monkeypatch
    )
    payload = b"wheel-a"
    core = {
        "wheel": {
            "filename": WHEEL_FILENAME,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size": len(payload),
        }
    }
    builder._publish_candidate_build_result(
        roots,
        label="A",
        wheel_payload=payload,
        artifact_core=core,
        source_identity={"P1_U04_SOURCE_ST_DEV": "1", "P1_U04_SOURCE_ST_INO": "2"},
        process_identity={
            "boot_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "pid": 100,
            "start_time_ticks": 200,
        },
        x4_authority=json.loads(receipt.read_bytes()),
        x4_receipt_sha256=receipt_sha256,
    )
    descriptor = os.open(
        x4_posix_tmp_path, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW
    )
    monkeypatch.setattr(
        builder, "_materialize_candidate_inputs", lambda *_args, **_kwargs: roots
    )
    monkeypatch.setattr(
        builder,
        "_candidate_process_identity",
        lambda: {
            "boot_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "pid": 101,
            "start_time_ticks": 201,
        },
    )
    monkeypatch.setattr(
        builder,
        "_build_candidate_once",
        lambda *_args: (
            payload,
            _empty_candidate_preflight(),
            core,
            {"P1_U04_SOURCE_ST_DEV": "1", "P1_U04_SOURCE_ST_INO": "3"},
            descriptor,
        ),
    )
    real_load = builder._load_candidate_build_result
    load_count = 0

    def drift_before_final_a(roots, *, label):
        nonlocal load_count
        load_count += 1
        if load_count == 4:
            assert label == "A"
            build_a_receipt = (
                roots["candidate_build_root"] / "build-a" / "build-receipt.json"
            )
            build_a_receipt.chmod(0o600)
            drifted = json.loads(build_a_receipt.read_bytes())
            drifted["kind"] = "P1_U04_BUILD_DRIFTED"
            build_a_receipt.write_text(
                json.dumps(drifted, sort_keys=True, indent=2) + "\n",
                encoding="ascii",
            )
            build_a_receipt.chmod(0o400)
        return real_load(roots, label=label)

    monkeypatch.setattr(builder, "_load_candidate_build_result", drift_before_final_a)

    with pytest.raises(builder.VerificationError):
        builder.build_candidate_b(
            authority_receipt=receipt,
            authority_receipt_sha256=receipt_sha256,
        )

    assert not (roots["candidate_build_root"] / "artifacts").exists()


@pytest.mark.parametrize("publication", ("build-b", "final"))
def test_candidate_build_b_revalidates_x4_before_each_publication(
    x4_posix_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    publication: str,
) -> None:
    receipt, receipt_sha256, _engine, _inputs, roots = _write_x4_authority_receipt(
        x4_posix_tmp_path, monkeypatch
    )
    payload = b"wheel-a"
    core = {
        "wheel": {
            "filename": WHEEL_FILENAME,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size": len(payload),
        }
    }
    builder._publish_candidate_build_result(
        roots,
        label="A",
        wheel_payload=payload,
        artifact_core=core,
        source_identity={"P1_U04_SOURCE_ST_DEV": "1", "P1_U04_SOURCE_ST_INO": "2"},
        process_identity={
            "boot_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "pid": 100,
            "start_time_ticks": 200,
        },
        x4_authority=json.loads(receipt.read_bytes()),
        x4_receipt_sha256=receipt_sha256,
    )
    descriptor = os.open(x4_posix_tmp_path, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW)
    monkeypatch.setattr(
        builder, "_materialize_candidate_inputs", lambda *_args, **_kwargs: roots
    )
    monkeypatch.setattr(
        builder,
        "_candidate_process_identity",
        lambda: {
            "boot_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "pid": 101,
            "start_time_ticks": 201,
        },
    )
    monkeypatch.setattr(
        builder,
        "_build_candidate_once",
        lambda *_args: (
            payload,
            _empty_candidate_preflight(),
            core,
            {"P1_U04_SOURCE_ST_DEV": "1", "P1_U04_SOURCE_ST_INO": "3"},
            descriptor,
        ),
    )
    real_validate = builder._validate_x4_authority_receipt
    phase_calls: list[str] = []

    def validate_with_drift(*args, **kwargs):
        validated = real_validate(*args, **kwargs)
        phase_calls.append(kwargs["phase"])
        should_drift = (
            publication == "build-b" and phase_calls == ["B", "B"]
        ) or (publication == "final" and kwargs["phase"] == "FINAL")
        if should_drift:
            validated = copy.deepcopy(validated)
            validated["candidate"]["head"] = "c" * 40
        return validated

    monkeypatch.setattr(builder, "_validate_x4_authority_receipt", validate_with_drift)

    with pytest.raises(builder.VerificationError, match="validated X4"):
        builder.build_candidate_b(
            authority_receipt=receipt,
            authority_receipt_sha256=receipt_sha256,
        )

    assert not (roots["candidate_build_root"] / "artifacts").exists()
    assert (roots["candidate_build_root"] / "build-b").exists() is (
        publication == "final"
    )


def test_candidate_build_b_reloads_sealed_build_b_before_final_publication(
    x4_posix_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    receipt, receipt_sha256, _engine, _inputs, roots = _write_x4_authority_receipt(
        x4_posix_tmp_path, monkeypatch
    )
    payload = b"wheel-a"
    core = {
        "wheel": {
            "filename": WHEEL_FILENAME,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size": len(payload),
        }
    }
    builder._publish_candidate_build_result(
        roots,
        label="A",
        wheel_payload=payload,
        artifact_core=core,
        source_identity={"P1_U04_SOURCE_ST_DEV": "1", "P1_U04_SOURCE_ST_INO": "2"},
        process_identity={
            "boot_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "pid": 100,
            "start_time_ticks": 200,
        },
        x4_authority=json.loads(receipt.read_bytes()),
        x4_receipt_sha256=receipt_sha256,
    )
    descriptor = os.open(x4_posix_tmp_path, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW)
    monkeypatch.setattr(
        builder, "_materialize_candidate_inputs", lambda *_args, **_kwargs: roots
    )
    monkeypatch.setattr(
        builder,
        "_candidate_process_identity",
        lambda: {
            "boot_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "pid": 101,
            "start_time_ticks": 201,
        },
    )
    monkeypatch.setattr(
        builder,
        "_build_candidate_once",
        lambda *_args: (
            payload,
            _empty_candidate_preflight(),
            core,
            {"P1_U04_SOURCE_ST_DEV": "1", "P1_U04_SOURCE_ST_INO": "3"},
            descriptor,
        ),
    )
    real_publish = builder._publish_candidate_build_result

    def publish_then_mutate_build_b(*args, **kwargs):
        published = real_publish(*args, **kwargs)
        if kwargs["label"] == "B":
            build_b_receipt = (
                roots["candidate_build_root"] / "build-b" / "build-receipt.json"
            )
            build_b_receipt.chmod(0o600)
            drifted = json.loads(build_b_receipt.read_bytes())
            drifted["kind"] = "P1_U04_BUILD_DRIFTED"
            build_b_receipt.write_text(
                json.dumps(drifted, sort_keys=True, indent=2) + "\n",
                encoding="ascii",
            )
            build_b_receipt.chmod(0o400)
        return published

    monkeypatch.setattr(
        builder, "_publish_candidate_build_result", publish_then_mutate_build_b
    )

    with pytest.raises(builder.VerificationError, match="Build B"):
        builder.build_candidate_b(
            authority_receipt=receipt,
            authority_receipt_sha256=receipt_sha256,
        )

    assert not (roots["candidate_build_root"] / "artifacts").exists()


@pytest.mark.parametrize(
    "mutation",
    (
        "digest",
        "schema",
        "verdict",
        "head",
        "tree",
        "checks",
        "release_check",
        "mode",
        "link",
    ),
)
def test_candidate_actions_reject_untrusted_x4_receipt(
    x4_posix_tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    tmp_path = x4_posix_tmp_path
    receipt, expected_sha256, _engine, _inputs, _roots = _write_x4_authority_receipt(
        tmp_path, monkeypatch, mutation=mutation
    )
    monkeypatch.setattr(
        builder,
        "_build_candidate_once",
        lambda *_args: pytest.fail("untrusted X4 receipt reached native build"),
    )

    with pytest.raises(builder.VerificationError, match="X4 authority receipt"):
        builder.build_candidate_a(
            authority_receipt=receipt,
            authority_receipt_sha256=expected_sha256,
        )


def test_candidate_split_cli_rejects_combined_missing_and_legacy_authority_before_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(builder, "build_candidate_a", lambda **_kwargs: calls.append("A"), raising=False)
    monkeypatch.setattr(builder, "build_candidate_b", lambda **_kwargs: calls.append("B"), raising=False)

    with pytest.raises(SystemExit):
        builder.main(["--build-candidate-a", "--build-candidate-b", "--offline"])
    assert builder.main(["--build-candidate-a", "--offline"]) == 2
    assert builder.main([
        "--build-candidate-a", "--offline", "--authority-receipt", "/tmp/r",
        "--authority-receipt-sha256", "0" * 64, "--policy", "/tmp/foreign",
    ]) == 2
    assert builder.main([
        "--build-candidate-a", "--authority-receipt", "/tmp/r",
        "--authority-receipt-sha256", "0" * 64,
    ]) == 2
    assert builder.main([
        "--build-candidate-a", "--offline", "--authority-receipt", "/tmp/r",
        "--authority-receipt-sha256", "0" * 64, "--sandbox", "/tmp/foreign",
    ]) == 2
    assert builder.main([
        "--build-candidate-a", "--offline", "--authority-receipt", "/tmp/r",
        "--authority-receipt-sha256", "0" * 64, "--retain-raw-wheel-pair",
    ]) == 2
    assert calls == []


@pytest.mark.parametrize("publication_failure", (False, True))
def test_candidate_build_closes_source_descriptor_before_intermediate_publication(
    x4_posix_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    publication_failure: bool,
) -> None:
    tmp_path = x4_posix_tmp_path
    receipt, receipt_sha256, _engine, _inputs, roots = _write_x4_authority_receipt(
        tmp_path, monkeypatch
    )
    descriptor = os.open(tmp_path, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW)

    def fake_build(*_args):
        return (
            b"wheel-a",
            _empty_candidate_preflight(),
            {
                "wheel": {
                    "filename": WHEEL_FILENAME,
                    "sha256": hashlib.sha256(b"wheel-a").hexdigest(),
                    "size": 7,
                }
            },
            {"P1_U04_SOURCE_ST_DEV": "1", "P1_U04_SOURCE_ST_INO": "2"},
            descriptor,
        )

    def fake_publish(*_args, **_kwargs):
        with pytest.raises(OSError) as captured:
            os.fstat(descriptor)
        assert captured.value.errno == errno.EBADF
        if publication_failure:
            raise builder.VerificationError("publication failed")
        return {"kind": "P1_U04_BUILD_A"}

    monkeypatch.setattr(builder, "_materialize_candidate_inputs", lambda *_args: roots)
    monkeypatch.setattr(builder, "_build_candidate_once", fake_build)
    monkeypatch.setattr(builder, "_publish_candidate_build_result", fake_publish)
    monkeypatch.setattr(
        builder,
        "_candidate_process_identity",
        lambda: {
            "boot_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "pid": 100,
            "start_time_ticks": 200,
        },
    )

    if publication_failure:
        with pytest.raises(builder.VerificationError, match="publication failed"):
            builder.build_candidate_a(
                authority_receipt=receipt,
                authority_receipt_sha256=receipt_sha256,
            )
    else:
        assert builder.build_candidate_a(
            authority_receipt=receipt,
            authority_receipt_sha256=receipt_sha256,
        ) == {"kind": "P1_U04_BUILD_A"}


@pytest.mark.parametrize("raw_wheel_equality", (False, True))
def test_candidate_forensic_split_build_retains_sealed_pair_and_never_final(
    x4_posix_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    raw_wheel_equality: bool,
) -> None:
    tmp_path = x4_posix_tmp_path
    receipt, receipt_sha256, _engine, _inputs, roots = _write_x4_authority_receipt(
        tmp_path, monkeypatch
    )
    first_payload = _diagnostic_wheel(
        [("nautilus_trader/module.py", b"first")]
    )
    second_payload = (
        first_payload
        if raw_wheel_equality
        else _diagnostic_wheel([("nautilus_trader/module.py", b"second")])
    )
    first_core = {
        "wheel": {
            "filename": WHEEL_FILENAME,
            "sha256": hashlib.sha256(first_payload).hexdigest(),
            "size": len(first_payload),
        }
    }
    first_process = {
        "boot_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "pid": 100,
        "start_time_ticks": 200,
    }
    builder._publish_candidate_build_result(
        roots,
        label="A",
        wheel_payload=first_payload,
        artifact_core=first_core,
        source_identity={"P1_U04_SOURCE_ST_DEV": "1", "P1_U04_SOURCE_ST_INO": "2"},
        process_identity=first_process,
        x4_authority=json.loads(receipt.read_bytes()),
        x4_receipt_sha256=receipt_sha256,
    )
    descriptor = os.open(tmp_path, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW)
    second_core = {
        "wheel": {
            "filename": WHEEL_FILENAME,
            "sha256": hashlib.sha256(second_payload).hexdigest(),
            "size": len(second_payload),
        }
    }
    monkeypatch.setattr(
        builder, "_materialize_candidate_inputs", lambda *_args, **_kwargs: roots
    )
    monkeypatch.setattr(
        builder,
        "_candidate_process_identity",
        lambda: {
            "boot_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "pid": 101,
            "start_time_ticks": 201,
        },
    )
    monkeypatch.setattr(
        builder,
        "_build_candidate_once",
        lambda *_args: (
            second_payload,
            _candidate_structural_preflight_for_test(second_payload),
            second_core,
            {"P1_U04_SOURCE_ST_DEV": "1", "P1_U04_SOURCE_ST_INO": "3"},
            descriptor,
        ),
    )

    with pytest.raises(builder.VerificationError):
        builder.build_candidate_b(
            authority_receipt=receipt,
            authority_receipt_sha256=receipt_sha256,
            retain_raw_wheel_pair=True,
        )

    forensic_root = roots["candidate_forensic_root"]
    assert {path.name for path in forensic_root.iterdir()} == {
        f"first-{WHEEL_FILENAME}",
        f"second-{WHEEL_FILENAME}",
        "forensic-manifest.json",
    }
    assert (roots["candidate_build_root"] / "build-a").is_dir()
    assert (roots["candidate_build_root"] / "build-b").is_dir()
    assert not (roots["candidate_build_root"] / "artifacts").exists()


def _wheel_with_declared_eocd_count(payload: bytes, count: int) -> bytes:
    mutated = bytearray(payload)
    eocd = payload.rfind(b"PK\x05\x06")
    assert eocd >= 0
    struct.pack_into("<HH", mutated, eocd + 8, count, count)
    return bytes(mutated)


def _wheel_with_record_field(
    payload: bytes,
    signature: bytes,
    offset: int,
    format: str,
    value: int,
) -> bytes:
    mutated = bytearray(payload)
    record = payload.find(signature)
    assert record >= 0
    struct.pack_into(format, mutated, record + offset, value)
    return bytes(mutated)


def _wheel_with_zip64_extra(payload: bytes, signature: bytes) -> bytes:
    return _wheel_with_extra_field(payload, signature, struct.pack("<HH", 0x0001, 0))


def _wheel_with_zip64_trailer(payload: bytes, signature: bytes) -> bytes:
    eocd = payload.rfind(b"PK\x05\x06")
    assert eocd >= 0
    if signature == b"PK\x06\x06":
        trailer = signature + struct.pack("<Q", 44) + b"\0" * 44
    else:
        assert signature == b"PK\x06\x07"
        trailer = signature + b"\0" * 16
    return payload[:eocd] + trailer + payload[eocd:]


def _bind_raw_wheel_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    first_payload: bytes,
    second_payload: bytes,
    published: list[bool],
    preflights: tuple[dict[str, int], dict[str, int]] | None = None,
    roots: dict[str, Path] | None = None,
) -> tuple[list[int], list[int]]:
    if preflights is None:
        preflights = (
            _candidate_structural_preflight_for_test(first_payload),
            _candidate_structural_preflight_for_test(second_payload),
        )
    descriptors = [
        os.open(tmp_path, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW)
        for _index in range(2)
    ]
    build_calls: list[int] = []

    def fake_build(*_args):
        index = len(build_calls)
        build_calls.append(index)
        identity = {
            "P1_U04_SOURCE_ST_DEV": "1",
            "P1_U04_SOURCE_ST_INO": str(index + 1),
        }
        payload = (first_payload, second_payload)[index]
        return (
            payload,
            preflights[index],
            {"wheel": {"sha256": "0" * 64}},
            identity,
            descriptors[index],
        )

    engine = (
        {
            "external_cache_isolation": {
                "external_roots": {
                    name: str(path) for name, path in (roots or {}).items()
                }
            }
        }
        if roots is not None
        else {}
    )
    monkeypatch.setattr(builder, "_verify_candidate_authority", lambda: (engine, {}))
    monkeypatch.setattr(
        builder, "_materialize_candidate_inputs", lambda *_args: roots or {}
    )
    monkeypatch.setattr(builder, "_build_candidate_once", fake_build)
    monkeypatch.setattr(
        builder,
        "_publish_candidate_artifacts",
        lambda *_args: published.append(True),
    )
    return descriptors, build_calls


def _write_source_archive(
    path: Path, members: list[tuple[str, bytes | None, int]]
) -> dict[str, object]:
    with tarfile.open(path, "w:gz") as archive:
        for name, payload, mode in members:
            member = tarfile.TarInfo(name)
            member.mode = mode
            if payload is None:
                member.type = tarfile.DIRTYPE
                archive.addfile(member)
            else:
                member.size = len(payload)
                archive.addfile(member, io.BytesIO(payload))
    path.chmod(0o400)
    return {
        "filename": path.name,
        "size": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "member_count": len(members),
        "entry_count": len(members),
        "top_level_root": "source-root",
    }


def _portable_candidate_policies(
    tmp_path: Path,
) -> tuple[
    dict[str, object],
    dict[str, object],
    dict[str, Path],
    Path,
    Path,
]:
    engine = json.loads(ENGINE_POLICY.read_text(encoding="ascii"))
    inputs = json.loads(
        builder._CANDIDATE_TOOLCHAIN_INPUTS.read_text(encoding="ascii")
    )
    old_roots = engine["external_cache_isolation"]["external_roots"]
    fixture_root = tmp_path / "portable-authority"
    roots = {name: fixture_root / name for name in old_roots}
    replacements = sorted(
        ((str(old_roots[name]), str(path)) for name, path in roots.items()),
        key=lambda item: len(item[0]),
        reverse=True,
    )

    def rebind(value: object) -> object:
        if isinstance(value, dict):
            return {key: rebind(item) for key, item in value.items()}
        if isinstance(value, list):
            return [rebind(item) for item in value]
        if isinstance(value, str):
            for old, new in replacements:
                if value == old or value.startswith(old + "/"):
                    return new + value[len(old) :]
        return value

    engine = rebind(engine)
    inputs = rebind(inputs)
    assert isinstance(engine, dict)
    assert isinstance(inputs, dict)
    policy_root = tmp_path / "portable-policy"
    policy_root.mkdir()
    engine_path = policy_root / "engine-build-policy.json"
    inputs_path = policy_root / "toolchain-inputs.json"
    _write_portable_candidate_policies(engine_path, inputs_path, engine, inputs)
    return engine, inputs, roots, engine_path, inputs_path


def _write_portable_candidate_policies(
    engine_path: Path,
    inputs_path: Path,
    engine: dict[str, object],
    inputs: dict[str, object],
) -> None:
    engine_path.write_text(
        json.dumps(engine, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="ascii",
    )
    inputs["policy_hashes"]["engine_build_policy_sha256"] = hashlib.sha256(
        engine_path.read_bytes()
    ).hexdigest()
    inputs_path.write_text(
        json.dumps(inputs, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="ascii",
    )


def _replace_source_archive_payload(
    path: Path, record: dict[str, object], payload: bytes
) -> None:
    path.chmod(0o600)
    path.write_bytes(payload)
    path.chmod(0o400)
    record["size"] = len(payload)
    record["sha256"] = hashlib.sha256(payload).hexdigest()


def _write_candidate_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[dict[str, object], dict[str, object], dict[str, Path], dict[str, object]]:
    engine, inputs, roots, engine_path, inputs_path = _portable_candidate_policies(
        tmp_path
    )
    roots["candidate_build_root"] = tmp_path
    for document in (engine, inputs):
        document["external_cache_isolation"]["external_roots"][
            "candidate_build_root"
        ] = str(tmp_path)
    source_root = roots["candidate_input_root"] / "source-inputs"
    source_root.mkdir(parents=True)
    source_record = inputs["source"]["artifact"]
    archive_path = source_root / "source-fixture.tar.gz"
    source_record.update(
        _write_source_archive(
            archive_path,
            [("source-root/fixture.py", b"FIXTURE = True\n", 0o400)],
        )
    )
    source_record["symlink_count"] = 0
    source_record["symlink_records"] = []
    _write_portable_candidate_policies(engine_path, inputs_path, engine, inputs)
    artifact_directory = tmp_path / "artifacts"
    artifact_directory.mkdir()
    wheel = artifact_directory / WHEEL_FILENAME
    _write_candidate_wheel(
        wheel,
        inputs,
        package_member="nautilus_trader/native.so",
        package_payload=_elf64(soname="native.so"),
    )
    source_tree = builder._extract_candidate_source(
        archive_path,
        tmp_path / "extracted-source",
        source_record,
    )
    runtime_wheels = inputs["runtime_wheels"]
    document = {
        "schema_version": 7,
        "manifest_kind": "NAUTILUS_V1_231_CANDIDATE_ARTIFACT",
        "activation_status": "CANDIDATE_ONLY_NOT_ACTIVATED",
        "engine": {
            "name": "nautilus_trader",
            "version": "1.231.0",
            "upstream_tag": engine["candidate"]["upstream_tag"],
            "upstream_commit": engine["candidate"]["upstream_commit"],
        },
        "python": {
            "identity": inputs["python"]["identity"],
            "abi": inputs["python"]["abi"],
            "executable_sha256": inputs["python"]["executable_sha256"],
            "stdlib_tree_sha256": inputs["python"]["stdlib_inventory"]["tree_sha256"],
        },
        "source": {
            **source_record,
            "verified_extracted_tree_sha256": source_tree,
        },
        "policy_hashes": inputs["policy_hashes"],
        "toolchain": {
            "rustc_identity": engine["rust"]["rustc_identity"],
            "cargo_identity": engine["rust"]["cargo_identity"],
            "llvm_version": engine["llvm_toolchain"]["version"],
            "command_router_authority": inputs["command_router"]["authority"],
        },
        "network": "DISABLED_BY_BUBBLEWRAP_UNSHARE_ALL",
        "wheel": {
            "filename": wheel.name,
            "size": wheel.stat().st_size,
            "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        },
        "native_libraries": builder._candidate_native_inventory(wheel),
        "runtime_wheels": [
            {
                key: record[key]
                for key in ("filename", "package", "version", "mode", "size", "sha256")
            }
            for record in runtime_wheels
        ],
    }
    x4_receipt_sha256 = "e" * 64
    candidate_identity = {"head": "a" * 40, "tree": "b" * 40}
    policy_sha256 = builder._candidate_policy_receipt(inputs)
    sanitized_environment_sha256 = hashlib.sha256(
        json.dumps(
            inputs["native_build_environment"],
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    ).hexdigest()
    authority_identities = {"fixture": {"sha256": "f" * 64}}
    process_identities = [
        {
            "boot_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "pid": 100,
            "start_time_ticks": 200,
        },
        {
            "boot_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "pid": 101,
            "start_time_ticks": 300,
        },
    ]
    source_identities = [
        {"P1_U04_SOURCE_ST_DEV": "1", "P1_U04_SOURCE_ST_INO": "2"},
        {"P1_U04_SOURCE_ST_DEV": "1", "P1_U04_SOURCE_ST_INO": "3"},
    ]
    wheel_payload = wheel.read_bytes()
    core_raw = (
        json.dumps(document, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    ).encode("ascii")
    build_receipt_sha256: dict[str, str] = {}
    for index, label in enumerate(("A", "B")):
        build_directory = tmp_path / f"build-{label.lower()}"
        build_directory.mkdir()
        build_wheel = build_directory / WHEEL_FILENAME
        build_core = build_directory / "artifact-core.json"
        build_receipt = build_directory / "build-receipt.json"
        build_wheel.write_bytes(wheel_payload)
        build_core.write_bytes(core_raw)
        receipt_document = {
            "artifact_core": {
                "filename": "artifact-core.json",
                "sha256": hashlib.sha256(core_raw).hexdigest(),
                "size": len(core_raw),
            },
            "authority_identities": authority_identities,
            "candidate": candidate_identity,
            "file_set": [WHEEL_FILENAME, "artifact-core.json", "build-receipt.json"],
            "kind": f"P1_U04_BUILD_{label}",
            "label": label,
            "policy_sha256": policy_sha256,
            "process_identity": process_identities[index],
            "sanitized_environment_sha256": sanitized_environment_sha256,
            "schema": "p1-u04-candidate-build-result-v1",
            "source_identity": source_identities[index],
            "wheel": document["wheel"],
            "x4_authority_receipt_sha256": x4_receipt_sha256,
        }
        build_receipt.write_bytes(
            (
                json.dumps(
                    receipt_document, ensure_ascii=True, sort_keys=True, indent=2
                )
                + "\n"
            ).encode("ascii")
        )
        build_receipt_sha256[label.lower()] = hashlib.sha256(
            build_receipt.read_bytes()
        ).hexdigest()
        for path in (build_wheel, build_core, build_receipt):
            path.chmod(0o400)
        build_directory.chmod(0o500)
    document["reproducible_build"] = {
        "build_a_receipt_sha256": build_receipt_sha256["a"],
        "build_b_receipt_sha256": build_receipt_sha256["b"],
        "build_count": 2,
        "fresh_physical_stages": True,
        "logical_stages_absent_after_build": True,
        "raw_wheel_equality": True,
        "native_inventory_equality": True,
        "authoritative_manifest_equality": True,
        "wheel_sha256": document["wheel"]["sha256"],
        "process_identities": process_identities,
        "source_fd_identities": source_identities,
        "x4_authority_receipt_sha256": x4_receipt_sha256,
    }
    manifest = artifact_directory / "artifact-manifest.json"
    manifest.write_text(
        json.dumps(document, sort_keys=True, indent=2) + "\n", encoding="ascii"
    )
    for path in (wheel, manifest):
        path.chmod(0o400)
    artifact_directory.chmod(0o500)
    monkeypatch.setattr(builder, "_candidate_git_identity", lambda: candidate_identity)
    monkeypatch.setattr(builder, "_verify_candidate_authority", lambda: (engine, inputs))
    monkeypatch.setattr(
        builder,
        "_candidate_external_identities",
        lambda *_args: authority_identities,
    )
    return engine, inputs, roots, document


def _replace_candidate_artifact_manifest(
    root: Path, document: dict[str, object]
) -> None:
    manifest = root / "artifacts/artifact-manifest.json"
    manifest.chmod(0o600)
    manifest.write_text(
        json.dumps(document, sort_keys=True, indent=2) + "\n", encoding="ascii"
    )
    manifest.chmod(0o400)


def _write_candidate_closure_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[
    Path,
    Path,
    dict[str, object],
    dict[str, object],
    dict[str, object],
    str,
]:
    base_runtime = tmp_path / "rollback/runtime-closure-v3"
    base_files = {
        "files/usr/lib/libbase.so": ("/usr/lib/libbase.so", b"base"),
        "files/engine/wheels/active.whl": ("/engine/wheels/active.whl", b"active"),
    }
    base_records = []
    for relative, (target, payload) in base_files.items():
        path = base_runtime / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        path.chmod(0o400)
        base_records.append(
            {
                "mode": "0400",
                "path": relative,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size": len(payload),
                "target": target,
            }
        )
    base_records.sort(key=lambda record: record["path"])
    base_manifest = {
        "schema_version": 1,
        "engine_name": "nautilus_trader",
        "engine_version": "1.227.0",
        "python_identity": "CPython 3.12.3",
        "source_commit": "2" * 40,
        "entrypoint": "/usr/bin/python3.12",
        "argv_prefix": ["-I", "-S", "/engine/launcher/nautilus_backtest.py"],
        "result_validator_id": "nautilus-backtest-result-v1",
        "timeout_seconds": 120,
        "artifact_manifest_sha256": "3" * 64,
        "files": base_records,
    }
    base_raw = materializer._canonical_json_bytes(base_manifest) + b"\n"
    (base_runtime / "closure-manifest.json").write_bytes(base_raw)
    (base_runtime / "closure-manifest.json").chmod(0o400)
    for directory in sorted(
        (path for path in base_runtime.rglob("*") if path.is_dir()), reverse=True
    ):
        directory.chmod(0o500)
    base_runtime.chmod(0o500)
    base_policy = {
        "profile": "execution-simulation",
        "profile_manifest_schema_version": 6,
        "semantic_profile": "nautilus-execution-simulation-v2",
        "result_validator_id": "nautilus-backtest-simulation-result-v1",
        "dependency_import_policy": "native-guarded-stdlib-first-sealed-wheel-path-v1",
        "source_commit": "4" * 40,
        "engine_name": "nautilus_trader",
        "engine_version": "1.227.0",
        "engine_upstream_commit": "2" * 40,
        "python_identity": "CPython 3.12.3",
        "artifact_manifest_sha256": "3" * 64,
        "base_runtime_manifest_sha256": hashlib.sha256(base_raw).hexdigest(),
        "base_file_count": len(base_records),
        "base_file_inventory_sha256": hashlib.sha256(
            materializer._canonical_json_bytes(base_records)
        ).hexdigest(),
        "argv_prefix": [
            "/usr/bin/python3.12",
            "-I",
            "-S",
            "/engine/launcher/nautilus_backtest.py",
            "--profile",
            "execution-simulation",
        ],
    }
    runtime_wheel = {
        "filename": "runtime.whl",
        "package": "runtime",
        "version": "1",
        "mode": "0400",
        "size": 7,
        "sha256": hashlib.sha256(b"runtime").hexdigest(),
    }
    engine_wheel = {
        "filename": WHEEL_FILENAME,
        "size": 6,
        "sha256": hashlib.sha256(b"engine").hexdigest(),
    }
    artifact = {
        "engine": {
            "name": "nautilus_trader",
            "version": "1.231.0",
            "upstream_tag": "v1.231.0",
            "upstream_commit": "5" * 40,
        },
        "python": {
            "identity": "CPython 3.12.3",
            "abi": "cp312",
            "executable_sha256": "6" * 64,
            "stdlib_tree_sha256": "7" * 64,
        },
        "source": {
            "filename": "source.tar.gz",
            "sha256": "8" * 64,
            "verified_extracted_tree_sha256": "9" * 64,
        },
        "policy_hashes": {"engine_build_policy_sha256": "a" * 64},
        "toolchain": {
            "rustc_identity": "rustc exact",
            "cargo_identity": "cargo exact",
            "llvm_version": "22.1.3",
            "command_router_authority": "EXACT_SEALED_COMMAND_ROUTER_V1",
        },
        "network": "DISABLED_BY_BUBBLEWRAP_UNSHARE_ALL",
        "runtime_wheels": [runtime_wheel],
        "wheel": engine_wheel,
    }
    inputs = {
        "policy_hashes": artifact["policy_hashes"],
        "python": {
            "identity": artifact["python"]["identity"],
            "abi": artifact["python"]["abi"],
            "executable_sha256": artifact["python"]["executable_sha256"],
            "stdlib_inventory": {
                "tree_sha256": artifact["python"]["stdlib_tree_sha256"]
            },
        },
        "source": {
            "artifact": {
                "filename": artifact["source"]["filename"],
                "sha256": artifact["source"]["sha256"],
            }
        },
        "runtime_wheels": [runtime_wheel],
    }
    root = tmp_path / "closure"
    output_records = [
        next(record for record in base_records if not record["target"].startswith("/engine/"))
    ]
    for record, payload in ((runtime_wheel, b"runtime"), (engine_wheel, b"engine")):
        relative = f"files/engine/wheels/{record['filename']}"
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        path.chmod(0o400)
        output_records.append(
            {
                "mode": "0400",
                "path": relative,
                "sha256": record["sha256"],
                "size": record["size"],
                "target": f"/engine/wheels/{record['filename']}",
            }
        )
    base_payload = root / "files/usr/lib/libbase.so"
    base_payload.parent.mkdir(parents=True, exist_ok=True)
    base_payload.write_bytes(b"base")
    base_payload.chmod(0o400)
    output_records.sort(key=lambda record: record["path"])
    loader = {
        "path": "/lib64/ld-linux-x86-64.so.2",
        "mode": "0400",
        "size": 1,
        "sha256": "b" * 64,
        "native_record_count": 0,
        "resolution": "ELF_LOADER_SEARCH_ORDER_V2",
    }
    artifact_sha256 = hashlib.sha256(b"artifact").hexdigest()
    selected_authority = {
        "generation": "runtime-closure-v12-r12-simulation",
        "artifact_generation": "artifacts/fixture",
        "policy": base_policy,
        "policy_sha256": "c" * 64,
        "manifest": {"schema_version": 6, "files": base_records},
        "manifest_sha256": "d" * 64,
        "manifest_size": 1,
        "manifest_mode": "0400",
        "artifact_manifest_sha256": base_policy["artifact_manifest_sha256"],
        "closure_sha256": "e" * 64,
        "non_engine_file_count": 1,
        "non_engine_file_inventory_sha256": "f" * 64,
        "attestation": {"manifest_schema_version": 6},
    }
    monkeypatch.setattr(
        materializer,
        "_selected_base_authority",
        lambda *_args, **_kwargs: selected_authority,
    )
    manifest = materializer._candidate_manifest(
        artifact=artifact,
        artifact_sha256=artifact_sha256,
        base_runtime=base_runtime,
        base_policy=base_policy,
        base_manifest=base_manifest,
        base_records=base_records,
        output_records=output_records,
        native_inventory=[],
        loader_assumptions=loader,
        qualification_sha256=hashlib.sha256(
            materializer._CANDIDATE_IMPORT_SCRIPT.encode("ascii")
        ).hexdigest(),
    )
    manifest_path = root / "closure-manifest.json"
    manifest_path.write_bytes(materializer._canonical_json_bytes(manifest) + b"\n")
    manifest_path.chmod(0o400)
    for directory in sorted(
        (path for path in root.rglob("*") if path.is_dir()), reverse=True
    ):
        directory.chmod(0o500)
    root.chmod(0o500)

    class CandidateBuilder:
        _CANDIDATE_WHEEL_FILENAME = WHEEL_FILENAME

        @staticmethod
        def _candidate_json(path: Path):
            return json.loads(path.read_text(encoding="ascii"))

        @staticmethod
        def _verify_candidate_wheel_archive(_path: Path) -> None:
            return None

    monkeypatch.setattr(materializer, "_candidate_builder_tool", CandidateBuilder)
    monkeypatch.setattr(
        materializer,
        "_candidate_native_inventory",
        lambda *_args: ([], loader),
    )
    return root, base_runtime, base_policy, artifact, inputs, artifact_sha256


def _write_selected_base_authority_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[
    Path,
    dict[str, object],
    dict[str, object],
    list[dict[str, object]],
    dict[str, object],
    CompleteEngineClosureAttestation,
    list[tuple[object, str]],
]:
    rollback = tmp_path / "rollback"
    historical_records = [
        {
            "mode": "0400",
            "path": "files/engine/wheels/active.whl",
            "sha256": hashlib.sha256(b"active").hexdigest(),
            "size": 6,
            "target": "/engine/wheels/active.whl",
        },
        {
            "mode": "0400",
            "path": "files/usr/lib/libbase.so",
            "sha256": hashlib.sha256(b"base").hexdigest(),
            "size": 4,
            "target": "/usr/lib/libbase.so",
        },
    ]
    historical_manifest = {
        "schema_version": 1,
        "engine_name": "nautilus_trader",
        "engine_version": "1.227.0",
        "python_identity": "CPython 3.12.3",
        "source_commit": "2" * 40,
        "entrypoint": "/usr/bin/python3.12",
        "argv_prefix": ["-I", "-S", "/engine/launcher/nautilus_backtest.py"],
        "result_validator_id": "nautilus-backtest-result-v1",
        "timeout_seconds": 120,
        "artifact_manifest_sha256": "0" * 64,
        "files": historical_records,
    }
    historical_raw = materializer._canonical_json_bytes(historical_manifest) + b"\n"

    artifact = rollback / "artifacts/nautilus-1.227.0-cp312-rust-bound-input-ff2e7753974c"
    artifact.mkdir(parents=True)
    wheel = artifact / "active.whl"
    wheel.write_bytes(b"active")
    artifact_document = {
        "engine_name": "nautilus_trader",
        "engine_version": "1.227.0",
        "python_identity": "CPython 3.12.3",
        "upstream_commit": "2" * 40,
        "wheel": {
            "filename": wheel.name,
            "sha256": hashlib.sha256(b"active").hexdigest(),
            "size": 6,
        },
    }
    artifact_raw = (
        json.dumps(artifact_document, sort_keys=True, indent=2) + "\n"
    ).encode("ascii")
    (artifact / "artifact-manifest.json").write_bytes(artifact_raw)
    for path in artifact.iterdir():
        path.chmod(0o400)
    artifact.chmod(0o500)

    guard = {
        "binary_sha256": hashlib.sha256(b"guard").hexdigest(),
        "binary_size": 5,
        "cargo_identity": "cargo 1.95.0 (f2d3ce0bd 2026-03-21)",
        "cargo_lock": "engines/nautilus/native_entry_guard/Cargo.lock",
        "cargo_lock_sha256": "1" * 64,
        "cargo_manifest": "engines/nautilus/native_entry_guard/Cargo.toml",
        "cargo_manifest_sha256": "2" * 64,
        "llvm_toolchain_policy_sha256": "3" * 64,
        "mode": "0500",
        "rust_toolchain_policy_sha256": "4" * 64,
        "rustc_identity": "rustc 1.95.0 (59807616e 2026-04-14)",
        "source": "engines/nautilus/native_entry_guard/src/main.rs",
        "source_sha256": "5" * 64,
        "target": "/engine/bin/nautilus-entry-guard",
        "target_triple": "x86_64-unknown-linux-gnu",
    }
    policy = {
        "argv_prefix": [
            "/usr/bin/python3.12",
            "-I",
            "-S",
            "/engine/launcher/nautilus_backtest.py",
            "--profile",
            "execution-simulation",
        ],
        "artifact_manifest_sha256": hashlib.sha256(artifact_raw).hexdigest(),
        "base_file_count": len(historical_records),
        "base_file_inventory_sha256": hashlib.sha256(
            materializer._canonical_json_bytes(historical_records)
        ).hexdigest(),
        "base_runtime_manifest_sha256": hashlib.sha256(historical_raw).hexdigest(),
        "dependency_import_policy": "native-guarded-stdlib-first-sealed-wheel-path-v1",
        "engine_name": "nautilus_trader",
        "engine_upstream_commit": "2" * 40,
        "engine_version": "1.227.0",
        "engine_wheel_mode": "0400",
        "engine_wheel_target": "/engine/wheels/active.whl",
        "entrypoint": "/engine/bin/nautilus-entry-guard",
        "launcher_inventory": [
            {
                "mode": "0400",
                "sha256": "6" * 64,
                "source": "engines/nautilus/launcher/nautilus_backtest.py",
                "target": "/engine/launcher/nautilus_backtest.py",
            },
            {
                "mode": "0400",
                "sha256": "7" * 64,
                "source": "engines/nautilus/launcher/target_portfolio_strategy.py",
                "target": "/engine/launcher/target_portfolio_strategy.py",
            },
        ],
        "native_entry_guard": guard,
        "profile": "execution-simulation",
        "profile_manifest_schema_version": 6,
        "python_identity": "CPython 3.12.3",
        "result_validator_id": "nautilus-backtest-simulation-result-v1",
        "schema_version": 1,
        "semantic_profile": "nautilus-execution-simulation-v2",
        "source_commit": "4" * 40,
        "timeout_seconds": 120,
    }
    assert set(policy) == materializer._POLICY_FIELDS
    policy_path = tmp_path / "runtime-closure-policy.json"
    policy_raw = materializer._canonical_json_bytes(policy) + b"\n"
    policy_path.write_bytes(policy_raw)

    selected = rollback / "runtime-closure-v12-r12-simulation"
    selected_records = [
        json.loads(json.dumps(historical_records[0])),
        json.loads(json.dumps(historical_records[1])),
        {
            "mode": "0500",
            "path": "files/engine/bin/nautilus-entry-guard",
            "sha256": guard["binary_sha256"],
            "size": 5,
            "target": "/engine/bin/nautilus-entry-guard",
        },
    ]
    payloads = {
        "files/engine/wheels/active.whl": b"active",
        "files/usr/lib/libbase.so": b"base",
        "files/engine/bin/nautilus-entry-guard": b"guard",
    }
    for record in selected_records:
        path = selected / str(record["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payloads[str(record["path"])])
        path.chmod(int(str(record["mode"]), 8))
    selected_manifest = {
        "argv_prefix": policy["argv_prefix"],
        "artifact_manifest_sha256": policy["artifact_manifest_sha256"],
        "dependency_import_policy": policy["dependency_import_policy"],
        "engine_name": policy["engine_name"],
        "engine_upstream_commit": policy["engine_upstream_commit"],
        "engine_version": policy["engine_version"],
        "entrypoint": policy["entrypoint"],
        "files": selected_records,
        "native_entry_guard": guard,
        "profile": policy["profile"],
        "python_identity": policy["python_identity"],
        "result_validator_id": policy["result_validator_id"],
        "schema_version": 6,
        "semantic_profile": policy["semantic_profile"],
        "source_commit": policy["source_commit"],
        "timeout_seconds": policy["timeout_seconds"],
    }
    selected_raw = (
        json.dumps(selected_manifest, sort_keys=True, indent=2) + "\n"
    ).encode("ascii")
    selected_manifest_path = selected / "closure-manifest.json"
    selected_manifest_path.write_bytes(selected_raw)
    selected_manifest_path.chmod(0o400)
    for directory in sorted(
        (path for path in selected.rglob("*") if path.is_dir()), reverse=True
    ):
        directory.chmod(0o500)
    selected.chmod(0o500)

    sandbox = tmp_path / "bwrap"
    sandbox.write_bytes(b"sandbox")
    sandbox.chmod(0o500)
    mounts = tuple(
        ReadOnlyClosureMount(
            source=selected / str(record["path"]),
            target=PurePosixPath(str(record["target"])),
            identity=(
                (selected / str(record["path"])).stat().st_dev,
                (selected / str(record["path"])).stat().st_ino,
            ),
            size=int(record["size"]),
            mode=int(str(record["mode"]), 8),
            sha256=str(record["sha256"]),
        )
        for record in selected_records
    )
    sidecar_stat = selected_manifest_path.stat()
    native_guard = NativeEntryGuardAttestation(
        target=PurePosixPath(str(guard["target"])),
        guarded_executable=PurePosixPath("/usr/bin/python3.12"),
        binary_sha256=str(guard["binary_sha256"]),
        binary_size=int(guard["binary_size"]),
        mode=0o500,
        source=str(guard["source"]),
        source_sha256=str(guard["source_sha256"]),
        cargo_manifest=str(guard["cargo_manifest"]),
        cargo_manifest_sha256=str(guard["cargo_manifest_sha256"]),
        cargo_lock=str(guard["cargo_lock"]),
        cargo_lock_sha256=str(guard["cargo_lock_sha256"]),
        cargo_identity=str(guard["cargo_identity"]),
        rustc_identity=str(guard["rustc_identity"]),
        rust_toolchain_policy_sha256=str(guard["rust_toolchain_policy_sha256"]),
        llvm_toolchain_policy_sha256=str(guard["llvm_toolchain_policy_sha256"]),
        target_triple=str(guard["target_triple"]),
    )
    sandbox_stat = sandbox.stat()
    attestation = CompleteEngineClosureAttestation(
        manifest_schema_version=6,
        profile="execution-simulation",
        source_commit=str(policy["source_commit"]),
        closure_sha256="8" * 64,
        mounts=mounts,
        entrypoint=PurePosixPath(str(policy["entrypoint"])),
        argv_prefix=tuple(str(value) for value in policy["argv_prefix"]),
        timeout_seconds=120,
        result_validator_id=str(policy["result_validator_id"]),
        sandbox=OsSandboxProof(
            executable=sandbox,
            identity=(sandbox_stat.st_dev, sandbox_stat.st_ino),
            executable_sha256=hashlib.sha256(b"sandbox").hexdigest(),
            profile_sha256="9" * 64,
            version="bubblewrap fixture",
            capabilities=("--perms", "--ro-bind-data"),
        ),
        semantic_profile=str(policy["semantic_profile"]),
        closure_manifest=ReadOnlyClosureMount(
            source=selected_manifest_path,
            target=PurePosixPath("/run/trading-agent/closure-manifest.json"),
            identity=(sidecar_stat.st_dev, sidecar_stat.st_ino),
            size=len(selected_raw),
            mode=0o400,
            sha256=hashlib.sha256(selected_raw).hexdigest(),
        ),
        native_entry_guard=native_guard,
        dependency_import_policy=str(policy["dependency_import_policy"]),
    )
    calls: list[tuple[object, str]] = []

    def attest(config, *, expected_profile: str):
        calls.append((config, expected_profile))
        return attestation

    monkeypatch.setattr(materializer, "_CANDIDATE_BASE_POLICY", policy_path)
    monkeypatch.setattr(materializer, "_CANDIDATE_SANDBOX", sandbox)
    monkeypatch.setattr(
        materializer,
        "_SELECTED_POLICY_SHA256",
        hashlib.sha256(policy_raw).hexdigest(),
        raising=False,
    )
    monkeypatch.setattr(
        materializer,
        "_SELECTED_MANIFEST_SHA256",
        hashlib.sha256(selected_raw).hexdigest(),
        raising=False,
    )
    monkeypatch.setattr(
        materializer,
        "_SELECTED_ARTIFACT_MANIFEST_SHA256",
        hashlib.sha256(artifact_raw).hexdigest(),
        raising=False,
    )
    monkeypatch.setattr(
        materializer, "_SELECTED_CLOSURE_SHA256", "8" * 64, raising=False
    )
    monkeypatch.setattr(materializer, "attest_nautilus_backtest_closure", attest)
    return (
        rollback,
        policy,
        historical_manifest,
        historical_records,
        selected_manifest,
        attestation,
        calls,
    )


def test_candidate_bwrap_handoff_binds_only_the_verified_physical_stage_fd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    physical_stage = tmp_path / "physical-stage"
    source = physical_stage / "source"
    source.mkdir(parents=True)
    source.chmod(0o700)
    physical_stage.chmod(0o700)
    logical_stage = _logical_stage()
    assert not logical_stage.exists()
    observed: dict[str, object] = {}

    def fake_run(command, **kwargs):
        observed["command"] = command
        observed["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(builder.subprocess, "run", fake_run)
    monkeypatch.setattr(builder, "_validate_sandbox", lambda _sandbox: "bubblewrap fixture")
    _mock_candidate_native_snapshot(monkeypatch)

    source_identity = builder._candidate_sandbox_run(
        physical_stage=physical_stage,
        logical_stage=logical_stage,
        action="policy-probe",
    )

    command = observed["command"]
    kwargs = observed["kwargs"]
    assert isinstance(command, list)
    assert command.count("--bind-fd") == 1
    bind_index = command.index("--bind-fd")
    assert command[bind_index + 2] == str(logical_stage)
    assert kwargs["pass_fds"] == (int(command[bind_index + 1]),)
    assert source_identity == {
        "P1_U04_SOURCE_ST_DEV": str(os.stat(source).st_dev),
        "P1_U04_SOURCE_ST_INO": str(os.stat(source).st_ino),
    }
    assert ["--ro-bind", "/", "/"] not in [
        command[index : index + 3] for index in range(len(command) - 2)
    ]
    triples = [command[index : index + 3] for index in range(len(command) - 2)]
    pairs = [command[index : index + 2] for index in range(len(command) - 1)]
    assert ["--ro-bind", "/usr/lib64", "/usr/lib64"] not in triples
    assert ["--dir", "/lib64"] in pairs
    assert [
        "--symlink",
        "../usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2",
        "/lib64/ld-linux-x86-64.so.2",
    ] in triples
    engine = builder._candidate_json(ENGINE_POLICY)
    assert str(builder._candidate_roots(engine)["rollback_root"]) not in command
    assert "/usr/bin/gcc" not in command
    assert command.count("--clearenv") == 1
    assert "dict(os.environ)" in command[-1]
    assert "sysconfig.get_config_var('EXT_SUFFIX')" in command[-1]
    source_fd = os.open(source, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        initial, _effective = builder._candidate_environment(
            engine, logical_stage, source_fd
        )
    finally:
        os.close(source_fd)
    entry_environment = {
        command[index + 1]: command[index + 2]
        for index, value in enumerate(command)
        if value == "--setenv"
    }
    entry_environment["PWD"] = str(logical_stage / "source")
    assert entry_environment == initial


def test_candidate_handoff_rejects_foreign_logical_stage_and_source_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    physical_stage = tmp_path / "physical-stage"
    physical_stage.mkdir(mode=0o700)
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (physical_stage / "source").symlink_to(foreign, target_is_directory=True)
    monkeypatch.setattr(builder, "_validate_sandbox", lambda _sandbox: "bubblewrap fixture")

    with pytest.raises(builder.VerificationError, match="offline candidate sandbox"):
        builder._candidate_sandbox_run(
            physical_stage=physical_stage,
            logical_stage=_logical_stage(),
            action="policy-probe",
        )

    (physical_stage / "source").unlink()
    (physical_stage / "source").mkdir()
    (physical_stage / "source").chmod(0o700)
    with pytest.raises(builder.VerificationError, match="U03 candidate build environment"):
        builder._candidate_sandbox_run(
            physical_stage=physical_stage,
            logical_stage=tmp_path / "stage-0123456789abcdef",
            action="policy-probe",
        )


def test_candidate_rust_materializer_rejects_every_payload_collision(
    tmp_path: Path,
) -> None:
    output = tmp_path / "bin/rustc"
    output.parent.mkdir()
    output.write_bytes(b"reviewed-first-component")

    with pytest.raises(builder.VerificationError, match="payload collision"):
        builder._write_candidate_rust_payload(
            output,
            io.BytesIO(b"even-identical-or-different-must-fail"),
            0o755,
        )


def test_candidate_rust_verifier_rejects_same_size_archive_payload_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    input_root = tmp_path / "inputs"
    rust_inputs = input_root / "rust-inputs"
    rust_inputs.mkdir(parents=True)
    components = []
    payloads = {
        "cargo": {"bin/cargo": b"cargo", "share/cargo.txt": b"cargo-data"},
        "rust-std": {"lib/rustlib/stdlib": b"reviewed"},
        "rustc": {"bin/rustc": b"rustc"},
    }
    for name, files in payloads.items():
        component = "rust-std-x86_64-unknown-linux-gnu" if name == "rust-std" else name
        archive_path = rust_inputs / f"{name}.tar.xz"
        with tarfile.open(archive_path, "w:xz") as archive:
            for relative, payload in files.items():
                member = tarfile.TarInfo(f"rust-fixture/{component}/{relative}")
                member.mode = 0o755 if relative.startswith("bin/") else 0o644
                member.size = len(payload)
                archive.addfile(member, io.BytesIO(payload))
        archive_path.chmod(0o400)
        components.append(
            {
                "filename": archive_path.name,
                "mode": "0400",
                "name": name,
                "sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
                "size": archive_path.stat().st_size,
            }
        )
    inputs = {
        "external_cache_isolation": {
            "external_roots": {"candidate_input_root": str(input_root)}
        },
        "rust": {"components": components},
        "command_router": {
            "entries": [
                {
                    "name": "cargo",
                    "type": "file",
                    "exec_target": {
                        "size": len(payloads["cargo"]["bin/cargo"]),
                        "sha256": hashlib.sha256(
                            payloads["cargo"]["bin/cargo"]
                        ).hexdigest(),
                    },
                }
            ]
        },
    }
    engine = {
        "rust": {"cargo_identity": "cargo fixture", "rustc_identity": "rustc fixture"}
    }
    real_candidate_json = builder._candidate_json
    monkeypatch.setattr(
        builder,
        "_candidate_json",
        lambda path: engine if path == builder._CANDIDATE_ENGINE_POLICY else real_candidate_json(path),
    )
    monkeypatch.setattr(
        builder,
        "_run_identity",
        lambda command, _label: f"{Path(command[0]).name} fixture",
    )
    destination = tmp_path / "rust"
    builder._materialize_candidate_rust(inputs, destination)
    drifted = destination / "lib/rustlib/stdlib"
    drifted.chmod(0o600)
    drifted.write_bytes(b"substitu")
    drifted.chmod(0o400)

    with pytest.raises(builder.VerificationError, match="Rust materialized"):
        builder._verify_candidate_rust(inputs, destination)


def test_candidate_reused_archives_bind_the_real_u03_0400_records() -> None:
    inputs = builder._candidate_json(builder._CANDIDATE_TOOLCHAIN_INPUTS)
    cargo = builder._candidate_json(builder._CANDIDATE_CARGO_POLICY)

    assert {record["mode"] for record in inputs["rust"]["components"]} == {"0400"}
    assert {record["mode"] for record in cargo["packages"]} == {"0400"}


def test_candidate_vendor_verifier_rejects_coordinated_payload_checksum_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    input_root = tmp_path / "inputs"
    registry = input_root / "cargo-registry"
    registry.mkdir(parents=True)
    archive_path = registry / "fixture-1.0.0.crate"
    original = b"reviewed"
    with tarfile.open(archive_path, "w:gz") as archive:
        member = tarfile.TarInfo("fixture-1.0.0/src/lib.rs")
        member.mode = 0o644
        member.size = len(original)
        archive.addfile(member, io.BytesIO(original))
    archive_path.chmod(0o400)
    record = {
        "checksum": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
        "filename": archive_path.name,
        "mode": "0400",
        "name": "fixture",
        "sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
        "size": archive_path.stat().st_size,
        "version": "1.0.0",
    }
    cargo_policy = tmp_path / "cargo-policy.json"
    cargo_policy.write_text(
        json.dumps({"package_count": 1, "packages": [record]}), encoding="ascii"
    )
    monkeypatch.setattr(builder, "_CANDIDATE_CARGO_POLICY", cargo_policy)
    inputs = {
        "external_cache_isolation": {
            "external_roots": {"candidate_input_root": str(input_root)}
        }
    }
    destination = tmp_path / "vendor"
    builder._materialize_candidate_vendor(inputs, destination)
    package = destination / "fixture-1.0.0"
    payload = package / "src/lib.rs"
    replacement = b"drifted!"
    payload.chmod(0o600)
    payload.write_bytes(replacement)
    payload.chmod(0o400)
    checksum = package / ".cargo-checksum.json"
    checksum.chmod(0o600)
    checksum.write_text(
        json.dumps(
            {
                "files": {"src/lib.rs": hashlib.sha256(replacement).hexdigest()},
                "package": record["checksum"],
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="ascii",
    )
    checksum.chmod(0o400)

    with pytest.raises(builder.VerificationError, match="vendor materialized"):
        builder._verify_candidate_vendor(inputs, destination)


def test_candidate_archive_directory_authority_includes_empty_and_implicit_parents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    input_root = tmp_path / "inputs"
    rust_inputs = input_root / "rust-inputs"
    registry = input_root / "cargo-registry"
    rust_inputs.mkdir(parents=True)
    registry.mkdir()
    components = []
    for name, member_name in (
        ("cargo", "bin/cargo"),
        ("rust-std", "lib/rustlib/stdlib"),
        ("rustc", "bin/rustc"),
    ):
        component = (
            "rust-std-x86_64-unknown-linux-gnu" if name == "rust-std" else name
        )
        archive_path = rust_inputs / f"{name}.tar.xz"
        with tarfile.open(archive_path, "w:xz") as archive:
            if name == "cargo":
                empty = tarfile.TarInfo(f"rust-fixture/{component}/share/empty")
                empty.type = tarfile.DIRTYPE
                empty.mode = 0o755
                archive.addfile(empty)
            payload = name.encode("ascii")
            member = tarfile.TarInfo(f"rust-fixture/{component}/{member_name}")
            member.mode = 0o755 if member_name.startswith("bin/") else 0o644
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
        archive_path.chmod(0o400)
        components.append(
            {
                "filename": archive_path.name,
                "mode": "0400",
                "name": name,
                "sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
                "size": archive_path.stat().st_size,
            }
        )
    crate = registry / "fixture-1.0.0.crate"
    with tarfile.open(crate, "w:gz") as archive:
        empty = tarfile.TarInfo("fixture-1.0.0/reviewed/empty")
        empty.type = tarfile.DIRTYPE
        empty.mode = 0o755
        archive.addfile(empty)
        payload = b"reviewed"
        member = tarfile.TarInfo("fixture-1.0.0/src/lib.rs")
        member.mode = 0o644
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))
    crate.chmod(0o400)
    crate_record = {
        "checksum": hashlib.sha256(crate.read_bytes()).hexdigest(),
        "filename": crate.name,
        "mode": "0400",
        "name": "fixture",
        "sha256": hashlib.sha256(crate.read_bytes()).hexdigest(),
        "size": crate.stat().st_size,
        "version": "1.0.0",
    }
    cargo_policy = tmp_path / "cargo-policy.json"
    cargo_policy.write_text(
        json.dumps({"package_count": 1, "packages": [crate_record]}),
        encoding="ascii",
    )
    monkeypatch.setattr(builder, "_CANDIDATE_CARGO_POLICY", cargo_policy)
    inputs = {
        "external_cache_isolation": {
            "external_roots": {"candidate_input_root": str(input_root)}
        },
        "rust": {"components": components},
    }

    rust_directories, _rust_files = builder._candidate_rust_expected_files(inputs)
    vendor_directories, _vendor_files = builder._candidate_vendor_expected_files(
        inputs
    )

    assert rust_directories == {
        "bin",
        "lib",
        "lib/rustlib",
        "share",
        "share/empty",
    }
    assert vendor_directories == {
        "fixture-1.0.0",
        "fixture-1.0.0/reviewed",
        "fixture-1.0.0/reviewed/empty",
        "fixture-1.0.0/src",
    }


@pytest.mark.parametrize("drift", ("extra", "missing", "symlink", "mode"))
def test_candidate_materialized_directory_authority_rejects_topology_and_metadata(
    tmp_path: Path, drift: str
) -> None:
    root = tmp_path / "sealed"
    empty = root / "reviewed/empty"
    empty.mkdir(parents=True)
    for path in (empty, empty.parent, root):
        path.chmod(0o500)
    expected = {"reviewed", "reviewed/empty"}
    verifier = getattr(builder, "_verify_candidate_materialized_directories", None)
    assert callable(verifier), "exact materialized directory verifier is missing"
    verifier(root, expected, "candidate fixture")

    root.chmod(0o700)
    if drift == "extra":
        (root / "extra").mkdir(mode=0o500)
    elif drift == "missing":
        empty.parent.chmod(0o700)
        empty.rmdir()
        empty.parent.chmod(0o500)
    elif drift == "symlink":
        empty.parent.chmod(0o700)
        empty.rmdir()
        target = tmp_path / "foreign"
        target.mkdir()
        empty.symlink_to(target, target_is_directory=True)
        empty.parent.chmod(0o500)
    else:
        empty.chmod(0o700)
    root.chmod(0o500)

    with pytest.raises(builder.VerificationError, match="directory authority"):
        verifier(root, expected, "candidate fixture")


def test_candidate_sandbox_rejects_source_replacement_between_actions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    physical_stage = tmp_path / "physical-stage"
    source = physical_stage / "source"
    source.mkdir(parents=True)
    source.chmod(0o700)
    physical_stage.chmod(0o700)
    identity_reader = getattr(builder, "_candidate_source_identity", None)
    assert callable(identity_reader), "candidate source identity reader is missing"
    expected_identity = identity_reader(source)

    def successful_action(command, **_kwargs):
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(builder.subprocess, "run", successful_action)
    monkeypatch.setattr(builder, "_validate_sandbox", lambda _sandbox: "bubblewrap fixture")
    _mock_candidate_native_snapshot(monkeypatch)

    builder._candidate_sandbox_run(
        physical_stage=physical_stage,
        logical_stage=_logical_stage(),
        action="venv",
        expected_source_identity=expected_identity,
    )
    source.rename(physical_stage / "replaced-source")
    source.mkdir(mode=0o700)

    with pytest.raises(builder.VerificationError, match="source directory identity drifted"):
        builder._candidate_sandbox_run(
            physical_stage=physical_stage,
            logical_stage=_logical_stage(),
            action="install",
            expected_source_identity=expected_identity,
        )


def test_candidate_sandbox_rechecks_source_identity_after_every_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    physical_stage = tmp_path / "physical-stage"
    source = physical_stage / "source"
    source.mkdir(parents=True)
    source.chmod(0o700)
    physical_stage.chmod(0o700)
    expected_identity = builder._candidate_source_identity(source)

    def replace_source(command, **_kwargs):
        source.rename(physical_stage / "replaced-source")
        source.mkdir(mode=0o700)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(builder.subprocess, "run", replace_source)
    monkeypatch.setattr(builder, "_validate_sandbox", lambda _sandbox: "bubblewrap fixture")
    _mock_candidate_native_snapshot(monkeypatch)

    with pytest.raises(builder.VerificationError, match="source directory identity drifted"):
        builder._candidate_sandbox_run(
            physical_stage=physical_stage,
            logical_stage=_logical_stage(),
            action="venv",
            expected_source_identity=expected_identity,
        )


def test_candidate_build_keeps_pip_out_of_the_exact_cargo_environment() -> None:
    inputs = builder._candidate_json(builder._CANDIDATE_TOOLCHAIN_INPUTS)
    logical_stage = _logical_stage()

    native = builder._candidate_command("native", logical_stage, inputs)
    package = builder._candidate_command("package", logical_stage, inputs)

    assert native == (
        str(logical_stage / "venv/bin/python"),
        "-I",
        str(logical_stage / "source/build.py"),
    )
    assert package[:4] == (
        str(logical_stage / "venv/bin/python"),
        "-I",
        "-S",
        "-c",
    )
    assert "pip" not in package
    assert "e705689fdd110147a19c8b3a895c1e0f646ae6757e958e09f9d9e4707652447e" in package[4]
    assert "e004bcb876c6f773848b65112b64f97f3b31a445ee34afe8f89468f49b2ae679" in package[4]
    assert "get_vcs = no_vcs" in package[4]
    assert "hook_calls != 1" in package[4]
    with pytest.raises(builder.VerificationError, match="action is not exact"):
        builder._candidate_command("wheel", logical_stage, inputs)


def test_candidate_logical_stage_token_is_stable_and_policy_conforming() -> None:
    expected = "stage-0000000000000000"

    assert builder._candidate_stage_token() == expected
    assert builder._candidate_stage_token() == expected
    assert len(expected) == len("stage-") + 16
    assert set(expected.removeprefix("stage-")) <= set("0123456789abcdef")


def test_candidate_package_preamble_accepts_poetry_vendored_import_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _engine, inputs, _roots, _engine_path, _inputs_path = (
        _portable_candidate_policies(tmp_path)
    )
    logical_stage = tmp_path / "stage-0123456789abcdef"
    site = logical_stage / "venv/lib/python3.12/site-packages"
    modules = {
        "poetry/__init__.py": b"",
        "poetry/core/__init__.py": (
            b"import sys\nfrom pathlib import Path\n"
            b"sys.path.insert(0, str(Path(__file__).parent / '_vendor'))\n"
        ),
        "poetry/core/_vendor/packaging/__init__.py": b"",
        "poetry/core/factory.py": b"class Factory:\n    pass\n",
        "poetry/core/masonry/__init__.py": b"",
        "poetry/core/masonry/builders/__init__.py": b"",
        "poetry/core/masonry/builders/builder.py": b"",
        "poetry/core/masonry/builders/wheel.py": b"class WheelBuilder:\n    pass\n",
        "poetry/core/masonry/metadata.py": b"",
        "poetry/core/vcs/__init__.py": b"",
    }
    wheel = tmp_path / "poetry-fixture.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for relative, raw in modules.items():
            archive.writestr(relative, raw)
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(site)
    tracked = {
        relative: hashlib.sha256(raw).hexdigest()
        for relative, raw in modules.items()
        if relative
        in {
            "poetry/core/__init__.py",
            "poetry/core/_vendor/packaging/__init__.py",
            "poetry/core/factory.py",
            "poetry/core/masonry/builders/builder.py",
            "poetry/core/masonry/builders/wheel.py",
            "poetry/core/masonry/metadata.py",
            "poetry/core/vcs/__init__.py",
        }
    }
    monkeypatch.setattr(builder, "_CANDIDATE_POETRY_MODULES", tracked)

    package = builder._candidate_command("package", logical_stage, inputs)
    preamble, separator, _remainder = package[4].partition("vcs_calls=0\n")
    assert separator
    original_path = list(sys.path)
    original_modules = set(sys.modules)
    try:
        sys.path[:] = inputs["python"]["admitted_sys_path"]
        exec(compile(preamble, "candidate-package-preamble", "exec"), {})
    finally:
        sys.path[:] = original_path
        for name in set(sys.modules) - original_modules:
            if name == "packaging" or name.startswith(("packaging.", "poetry")):
                sys.modules.pop(name, None)


def test_candidate_binds_exact_upstream_build_and_pyproject_symbols(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, inputs, _roots, engine_path, inputs_path = _portable_candidate_policies(
        tmp_path
    )
    source = tmp_path / "source"
    source.mkdir()
    build_py = b"import subprocess\nsubprocess.run(['cargo', 'build', '--lib'])\n"
    pyproject_raw = b"""[project]
name = "nautilus_trader"
version = "1.231.0"
requires-python = ">=3.12,<3.15"

[build-system]
requires = ["setuptools>=83", "poetry-core==2.3.1", "numpy>=1.26.4", "cython==3.2.9"]
build-backend = "poetry.core.masonry.api"

[tool.poetry.build]
script = "build.py"
generate-setup-file = false

[[tool.poetry.include]]
path = "crates/*"
format = "sdist"
[[tool.poetry.include]]
path = "Cargo.lock"
format = "sdist"
[[tool.poetry.include]]
path = "Cargo.toml"
format = "sdist"
[[tool.poetry.include]]
path = ".cargo/*"
format = "sdist"
[[tool.poetry.include]]
path = "nautilus_trader/**/*.so"
format = "wheel"
[[tool.poetry.include]]
path = "nautilus_trader/**/*.pyd"
format = "wheel"
[[tool.poetry.include]]
path = "nautilus_trader/py.typed"
format = "sdist"
[[tool.poetry.include]]
path = "nautilus_trader/py.typed"
format = "wheel"
[[tool.poetry.include]]
path = "nautilus_trader/**/*.pyi"
format = "sdist"
[[tool.poetry.include]]
path = "nautilus_trader/**/*.pyi"
format = "wheel"
"""
    (source / "build.py").write_bytes(build_py)
    (source / "pyproject.toml").write_bytes(pyproject_raw)
    identities = {
        name: (len(raw), hashlib.sha256(raw).hexdigest())
        for name, raw in (("build.py", build_py), ("pyproject.toml", pyproject_raw))
    }
    inputs["source"]["build_inputs"] = [
        {"path": name, "size": size, "sha256": digest}
        for name, (size, digest) in identities.items()
    ]
    engine["native_build_environment"]["sealed_source_trace"]["build.py"] = (
        builder._load_candidate_generator()._trace_build_script(build_py)
    )
    _write_portable_candidate_policies(engine_path, inputs_path, engine, inputs)
    monkeypatch.setattr(builder, "_CANDIDATE_SOURCE_INPUTS", identities)

    verifier = getattr(builder, "_verify_candidate_source_contract", None)
    assert callable(verifier), "candidate source/build contract verifier is missing"
    verifier(source, engine, inputs)

    pyproject = source / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8").replace(
            'script = "build.py"', 'script = "foreign.py"'
        ),
        encoding="utf-8",
    )
    with pytest.raises(builder.VerificationError, match="source build authority"):
        verifier(source, engine, inputs)


def test_candidate_source_archive_stream_inventory_matches_extracted_tree_digest(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "source.tar.gz"
    record = _write_source_archive(
        archive,
        [
            ("source-root/alpha.txt", b"alpha", 0o640),
            ("source-root/nested/beta.txt", b"beta", 0o600),
        ],
    )
    inventory_reader = getattr(builder, "_candidate_source_archive_inventory", None)
    assert callable(inventory_reader), "read-only source archive inventory is missing"

    inventory, digest = inventory_reader(archive, record)

    assert inventory == {
        "alpha.txt": (0o640, 5, hashlib.sha256(b"alpha").hexdigest()),
        "nested/beta.txt": (0o600, 4, hashlib.sha256(b"beta").hexdigest()),
    }
    assert builder._extract_candidate_source(
        archive, tmp_path / "extracted", record
    ) == digest


def test_candidate_source_extraction_rejects_path_replacement_after_raw_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "source.tar.gz"
    record = _write_source_archive(
        archive, [("source-root/payload", b"payload", 0o600)]
    )
    original = archive.read_bytes()
    replacement_payload = bytearray(original)
    replacement_payload[4] ^= 1
    replacement = tmp_path / "replacement.tar.gz"
    replacement.write_bytes(replacement_payload)
    replacement.chmod(0o400)
    assert hashlib.sha256(replacement_payload).hexdigest() != record["sha256"]
    assert gzip.decompress(replacement_payload) == gzip.decompress(original)
    real_tarfile_open = tarfile.open
    replaced = False

    def replace_before_tar_parse(*args, **kwargs):
        nonlocal replaced
        if not replaced:
            os.replace(replacement, archive)
            replaced = True
        return real_tarfile_open(*args, **kwargs)

    monkeypatch.setattr(builder.tarfile, "open", replace_before_tar_parse)

    with pytest.raises(builder.VerificationError):
        builder._extract_candidate_source(archive, tmp_path / "extracted", record)
    assert replaced


def test_candidate_source_archive_rejects_missing_gzip_trailer(tmp_path: Path) -> None:
    archive = tmp_path / "source.tar.gz"
    record = _write_source_archive(
        archive, [("source-root/payload", b"payload", 0o600)]
    )
    _replace_source_archive_payload(archive, record, archive.read_bytes()[:-8])

    with pytest.raises(builder.VerificationError):
        builder._candidate_source_archive_inventory(archive, record)


def test_candidate_source_archive_rejects_missing_tar_end_blocks(tmp_path: Path) -> None:
    archive = tmp_path / "source.tar.gz"
    record = _write_source_archive(
        archive, [("source-root/payload", b"payload", 0o600)]
    )
    with_only_one_end_block = gzip.compress(
        gzip.decompress(archive.read_bytes())[:1536], mtime=0
    )
    _replace_source_archive_payload(archive, record, with_only_one_end_block)

    with pytest.raises(builder.VerificationError):
        builder._candidate_source_archive_inventory(archive, record)


def test_candidate_source_archive_rejects_hidden_second_tar(tmp_path: Path) -> None:
    archive = tmp_path / "source.tar.gz"
    record = _write_source_archive(
        archive, [("source-root/payload", b"payload", 0o600)]
    )
    hidden = tmp_path / "hidden.tar.gz"
    _write_source_archive(hidden, [("source-root/hidden", b"hidden", 0o600)])
    first = gzip.decompress(archive.read_bytes())
    hidden_tar = gzip.decompress(hidden.read_bytes())
    _replace_source_archive_payload(
        archive, record, gzip.compress(first[:2048] + hidden_tar, mtime=0)
    )

    with pytest.raises(builder.VerificationError):
        builder._candidate_source_archive_inventory(archive, record)


@pytest.mark.parametrize(
    ("members", "message"),
    (
        (
            [
                ("source-root/duplicate", b"first", 0o600),
                ("source-root/duplicate", b"second", 0o600),
            ],
            "collision",
        ),
        (
            [
                ("source-root/ancestor", b"file", 0o600),
                ("source-root/ancestor/child", b"child", 0o600),
            ],
            "collision",
        ),
        ([("source-root/../escape", b"escape", 0o600)], "unsafe"),
        ([("source-root/directory", None, 0o700)], "regular-file"),
    ),
    ids=("duplicate", "ancestor", "unsafe", "non-regular"),
)
def test_candidate_source_archive_stream_inventory_rejects_unsafe_shape(
    tmp_path: Path,
    members: list[tuple[str, bytes | None, int]],
    message: str,
) -> None:
    archive = tmp_path / "source.tar.gz"
    record = _write_source_archive(archive, members)
    inventory_reader = getattr(builder, "_candidate_source_archive_inventory", None)
    assert callable(inventory_reader), "read-only source archive inventory is missing"

    with pytest.raises(builder.VerificationError, match=message):
        inventory_reader(archive, record)


@pytest.mark.parametrize(
    "members",
    (
        [("source-root\\ambiguous", b"payload", 0o600)],
        [("source-root/C:/drive", b"payload", 0o600)],
        [("source-root//double", b"payload", 0o600)],
        [("source-root/./dot", b"payload", 0o600)],
        [
            ("source-root/caf\N{LATIN SMALL LETTER E WITH ACUTE}", b"one", 0o600),
            ("source-root/cafe\N{COMBINING ACUTE ACCENT}", b"two", 0o600),
        ],
        [
            ("source-root/CaseFold", b"one", 0o600),
            ("source-root/casefold", b"two", 0o600),
        ],
        [
            ("source-root/ancestor", b"one", 0o600),
            ("source-root/ancestor/child", b"two", 0o600),
        ],
    ),
    ids=(
        "backslash",
        "windows-drive-component",
        "double-slash",
        "dot-component",
        "nfc-collision",
        "case-fold-collision",
        "component-ancestor-collision",
    ),
)
def test_candidate_source_archive_matches_u02_path_rejections(
    tmp_path: Path, members: list[tuple[str, bytes | None, int]]
) -> None:
    archive = tmp_path / "source.tar.gz"
    record = _write_source_archive(archive, members)

    with pytest.raises(provenance.VerificationError, match="archive"):
        provenance._scan_source_archive(archive, "source-root")
    with pytest.raises(builder.VerificationError, match="unsafe|collision"):
        builder._candidate_source_archive_inventory(archive, record)


@pytest.mark.parametrize(
    ("field", "message"),
    (
        ("size", "identity"),
        ("sha256", "identity"),
        ("mode", "identity"),
        ("member_count", "member count"),
        ("entry_count", "entry count"),
    ),
)
def test_candidate_source_archive_stream_inventory_rejects_authority_drift(
    tmp_path: Path, field: str, message: str
) -> None:
    archive = tmp_path / "source.tar.gz"
    record = _write_source_archive(
        archive, [("source-root/payload", b"payload", 0o600)]
    )
    if field == "mode":
        archive.chmod(0o600)
    elif field == "sha256":
        record[field] = "0" * 64
    else:
        record[field] = int(record[field]) + 1
    inventory_reader = getattr(builder, "_candidate_source_archive_inventory", None)
    assert callable(inventory_reader), "read-only source archive inventory is missing"

    with pytest.raises(builder.VerificationError, match=message):
        inventory_reader(archive, record)


def test_candidate_native_outputs_equal_every_sealed_pyx_and_pyo3(tmp_path: Path) -> None:
    ext_suffix = getattr(builder, "_CANDIDATE_EXT_SUFFIX", None)
    verifier = getattr(builder, "_verify_candidate_native_outputs", None)
    assert ext_suffix == ".cpython-312-x86_64-linux-gnu.so"
    assert callable(verifier), "candidate native output verifier is missing"
    source = tmp_path / "source"
    package = source / "nautilus_trader"
    build_package = source / "build/lib.linux-x86_64-cpython-312/nautilus_trader"
    for relative in (Path("alpha.pyx"), Path("nested/beta.pyx")):
        (package / relative).parent.mkdir(parents=True, exist_ok=True)
        (package / relative).write_text("# sealed source\n", encoding="ascii")
        output = relative.with_suffix(ext_suffix)
        (package / output).write_bytes(b"source-native")
        (build_package / output).parent.mkdir(parents=True, exist_ok=True)
        (build_package / output).write_bytes(b"build-native")
    pyo3 = package / "core" / f"nautilus_pyo3{ext_suffix}"
    pyo3.parent.mkdir()
    pyo3.write_bytes(b"pyo3-native")

    inventory = verifier(source)

    assert set(inventory) == {
        f"nautilus_trader/alpha{ext_suffix}",
        f"nautilus_trader/nested/beta{ext_suffix}",
        f"nautilus_trader/core/nautilus_pyo3{ext_suffix}",
    }
    (package / f"alpha{ext_suffix}").unlink()
    with pytest.raises(builder.VerificationError, match="native output set"):
        verifier(source)


def test_candidate_native_enters_upstream_build_with_initial_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    physical_stage = tmp_path / "physical-stage"
    source = physical_stage / "source"
    source.mkdir(parents=True)
    source.chmod(0o700)
    physical_stage.chmod(0o700)
    observed: dict[str, object] = {}

    def fake_run(command, **kwargs):
        observed["command"] = command
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(builder.subprocess, "run", fake_run)
    monkeypatch.setattr(builder, "_validate_sandbox", lambda _sandbox: "bubblewrap fixture")
    _mock_candidate_native_snapshot(monkeypatch)

    builder._candidate_sandbox_run(
        physical_stage=physical_stage,
        logical_stage=_logical_stage(),
        action="native",
        expected_source_identity=builder._candidate_source_identity(source),
    )

    command = observed["command"]
    assert isinstance(command, list)
    environment = {
        command[index + 1]: command[index + 2]
        for index, value in enumerate(command)
        if value == "--setenv"
    }
    base_rustflags = (
        "-Dwarnings -Aclippy::drop_non_drop -C link-arg=-fuse-ld=lld "
        "-C link-arg=-Wl,--gc-sections -C link-arg=-Wl,--as-needed "
        "-C link-arg=-Wl,-z,relro -C link-arg=-Wl,-z,now "
        "-C relocation-model=pic"
    )
    effective_rustflags = base_rustflags + " -C link-arg=-s"

    assert environment["RUSTFLAGS"] + " -C link-arg=-s" == effective_rustflags
    assert environment["RUSTFLAGS"] == base_rustflags
    assert not {"CC", "CXX", "LDSHARED", "CFLAGS"} & environment.keys()
    assert environment["LDFLAGS"] == (
        "-fuse-ld=lld -Wl,--gc-sections -Wl,--as-needed "
        "-Wl,-z,relro -Wl,-z,now"
    )


def test_candidate_initial_environment_transforms_to_exact_u03_cargo_boundary(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    source_fd = os.open(source, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        initial, effective = builder._candidate_environment(
            builder._candidate_json(ENGINE_POLICY), _logical_stage(), source_fd
        )
    finally:
        os.close(source_fd)

    cargo = dict(initial)
    cargo.update(CC="clang", CXX="clang++", LDSHARED="clang -shared")
    cargo["RUSTFLAGS"] += " -C link-arg=-s"

    assert cargo == effective
    assert "CFLAGS" not in cargo
    assert cargo["LDFLAGS"] == (
        "-fuse-ld=lld -Wl,--gc-sections -Wl,--as-needed "
        "-Wl,-z,relro -Wl,-z,now"
    )


def test_candidate_cython_linker_authority_is_exact_and_full_relro(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    source_fd = os.open(source, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        initial, effective = builder._candidate_environment(
            builder._candidate_json(ENGINE_POLICY), _logical_stage(), source_fd
        )
    finally:
        os.close(source_fd)

    expected = (
        "-fuse-ld=lld -Wl,--gc-sections -Wl,--as-needed "
        "-Wl,-z,relro -Wl,-z,now"
    )
    assert initial["LDFLAGS"] == expected
    assert effective["LDFLAGS"] == expected
    policy = builder._candidate_json(ENGINE_POLICY)["native_build_environment"]
    assert "LDFLAGS" not in policy["prohibited_source_environment"]


@pytest.mark.parametrize(
    ("action", "contract_key"),
    (("venv", "initial_environment"), ("install", "effective_environment"), ("package", "effective_environment")),
)
def test_candidate_non_native_actions_use_the_reviewed_u03_environment(
    action: str,
    contract_key: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    physical_stage = tmp_path / "physical-stage"
    source = physical_stage / "source"
    source.mkdir(parents=True)
    source.chmod(0o700)
    physical_stage.chmod(0o700)
    observed: dict[str, object] = {}

    def fake_run(command, **_kwargs):
        observed["command"] = command
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(builder.subprocess, "run", fake_run)
    monkeypatch.setattr(builder, "_validate_sandbox", lambda _sandbox: "bubblewrap fixture")
    _mock_candidate_native_snapshot(monkeypatch)
    builder._candidate_sandbox_run(
        physical_stage=physical_stage,
        logical_stage=_logical_stage(),
        action=action,
        expected_source_identity=builder._candidate_source_identity(source),
    )

    command = observed["command"]
    assert isinstance(command, list)
    entry_environment = {
        command[index + 1]: command[index + 2]
        for index, value in enumerate(command)
        if value == "--setenv"
    }
    entry_environment["PWD"] = str(_logical_stage() / "source")
    source_fd = os.open(source, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        initial, effective = builder._candidate_environment(
            builder._candidate_json(ENGINE_POLICY), _logical_stage(), source_fd
        )
    finally:
        os.close(source_fd)
    contract = {"initial_environment": initial, "effective_environment": effective}
    assert entry_environment == contract[contract_key]


def test_candidate_wheel_archive_identity_metadata_wheel_and_record_are_exact(
    tmp_path: Path,
) -> None:
    inputs = builder._candidate_json(builder._CANDIDATE_TOOLCHAIN_INPUTS)
    wheel = tmp_path / WHEEL_FILENAME
    _write_candidate_wheel(wheel, inputs)
    builder._verify_candidate_wheel_archive(wheel)

    wrong_tag = tmp_path / "nautilus_trader-1.231.0-cp312-cp312-linux_x86_64.whl"
    _write_candidate_wheel(wrong_tag, inputs)
    with pytest.raises(builder.VerificationError, match="wheel identity"):
        builder._verify_candidate_wheel_archive(wrong_tag)

    _write_candidate_wheel(wheel, inputs, tag="cp312-abi3-manylinux_2_35_x86_64")
    with pytest.raises(builder.VerificationError, match="WHEEL metadata"):
        builder._verify_candidate_wheel_archive(wheel)

    _write_candidate_wheel(wheel, inputs, name="foreign")
    with pytest.raises(builder.VerificationError, match="METADATA identity"):
        builder._verify_candidate_wheel_archive(wheel)

    _write_candidate_wheel(wheel, inputs, dist_info="foreign-1.231.0.dist-info")
    with pytest.raises(builder.VerificationError, match="metadata root"):
        builder._verify_candidate_wheel_archive(wheel)


def test_candidate_metadata_authority_is_exactly_eleven_direct_and_three_transitive() -> None:
    engine = builder._candidate_json(ENGINE_POLICY)
    inputs = builder._candidate_json(builder._CANDIDATE_TOOLCHAIN_INPUTS)
    policy = builder._candidate_json(builder._CANDIDATE_WHEEL_POLICY)
    metadata_only_actual = [
        'betfair-parser (==0.19.1) ; extra == "betfair"',
        "click (>=8.4.1,<9.0.0)",
        'defusedxml (>=0.7.1,<1.0.0) ; extra == "ib"',
        'docker (>=7.2.0,<8.0.0) ; extra == "docker"',
        "fsspec (==2026.2.0)",
        'kaleido (>=1.3.0,<2.0.0) ; extra == "visualization"',
        "msgspec (>=0.21.1,<1.0.0)",
        'nautilus-ibapi (==10.45.1) ; extra == "ib"',
        "numpy (>=1.26.4)",
        "pandas (>=2.3.3,<4.0.0)",
        'plotly (>=6.9.0,<7.0.0) ; extra == "visualization"',
        "portion (>=2.6.1)",
        'protobuf (==5.29.6) ; extra == "ib"',
        'py-clob-client-v2 (>=1.0.2,<2.0.0) ; extra == "polymarket"',
        "pyarrow (>=25.0.0)",
        "pytz (>=2026.2)",
        'simplejson (==3.20.2) ; extra == "visualization"',
        "tqdm (>=4.68.4,<5.0.0)",
        "tzdata (>=2026.3)",
        'uvloop (==0.22.1) ; sys_platform != "win32"',
    ]

    assert metadata_only_actual == sorted(metadata_only_actual)

    assert engine["candidate_wheel_metadata"] == {
        "authority": "EXACT_ORDERED_REQUIRES_DIST_V1",
        "requires_dist": metadata_only_actual,
        "source_correspondence": {
            "generator": "poetry-core 2.3.1",
            "generator_module_path": "poetry/core/masonry/metadata.py",
            "generator_module_sha256": (
                "27e852770d523f8e0d3bf3847a9a662f2c67725d2506dd83d6ef5bb67d9945a0"
            ),
            "project_path": "pyproject.toml",
            "project_sha256": (
                "5dbc4591408bd65f7b35c2274348a7a02ff7b034a15f46d5f8628d3c8fbafa36"
            ),
            "upstream_commit": "27a8e54e7ac3c57d6cbf8891f0283dfbaee97317",
        },
    }

    assert policy["runtime_direct"] == [
        "click",
        "fsspec",
        "msgspec",
        "numpy",
        "pandas",
        "portion",
        "pyarrow",
        "pytz",
        "tqdm",
        "tzdata",
        "uvloop",
    ]
    assert policy["runtime_transitive"] == [
        "python-dateutil",
        "six",
        "sortedcontainers",
    ]
    assert builder._candidate_runtime_direct_versions(inputs) == {
        "click": "8.4.2",
        "fsspec": "2026.2.0",
        "msgspec": "0.21.1",
        "numpy": "2.5.1",
        "pandas": "3.0.5",
        "portion": "2.6.2",
        "pyarrow": "25.0.0",
        "pytz": "2026.3.post1",
        "tqdm": "4.70.0",
        "tzdata": "2026.3",
        "uvloop": "0.22.1",
    }


@pytest.mark.parametrize(
    "mutate",
    (
        lambda requirements: requirements.pop(),
        lambda requirements: requirements.append(
            'foreign (>=1.0) ; extra == "dev"'
        ),
        lambda requirements: requirements.__setitem__(
            slice(11, 13), reversed(requirements[11:13])
        ),
        lambda requirements: requirements.__setitem__(
            0, 'click (>=8.4.1,<9.0.0) ; sys_platform != \'win32\''
        ),
    ),
    ids=("removed", "added", "reordered", "active-marker-mutated"),
)
def test_candidate_metadata_requires_dist_sequence_is_exact(
    tmp_path: Path,
    mutate: Callable[[list[str]], object],
) -> None:
    inputs = builder._candidate_json(builder._CANDIDATE_TOOLCHAIN_INPUTS)
    requirements = list(_candidate_requires_dist())
    mutate(requirements)
    wheel = tmp_path / WHEEL_FILENAME
    _write_candidate_wheel(wheel, inputs, requirements=tuple(requirements))

    with pytest.raises(builder.VerificationError, match="Requires-Dist"):
        builder._verify_candidate_wheel_archive(wheel)


@pytest.mark.parametrize(
    "mutate",
    (
        lambda policy, _inputs: policy.__setitem__("runtime_direct", "click"),
        lambda policy, _inputs: policy["runtime_direct"].append("click"),
        lambda policy, _inputs: policy["runtime_transitive"].append("click"),
        lambda policy, _inputs: policy["runtime_direct"].append("foreign"),
        lambda policy, _inputs: policy["runtime_direct"].pop(),
        lambda _policy, inputs: inputs["runtime_wheels"].append(
            dict(inputs["runtime_wheels"][0])
        ),
    ),
)
def test_candidate_metadata_authority_rejects_invalid_dependency_partition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutate: Callable[[dict[str, object], dict[str, object]], None],
) -> None:
    inputs = json.loads(builder._CANDIDATE_TOOLCHAIN_INPUTS.read_text(encoding="ascii"))
    policy = json.loads(builder._CANDIDATE_WHEEL_POLICY.read_text(encoding="ascii"))
    mutate(policy, inputs)
    path = tmp_path / "wheel-cache-policy.json"
    path.write_text(json.dumps(policy, sort_keys=True) + "\n", encoding="ascii")
    inputs["policy_hashes"]["wheel_cache_policy_sha256"] = hashlib.sha256(
        path.read_bytes()
    ).hexdigest()
    monkeypatch.setattr(builder, "_CANDIDATE_WHEEL_POLICY", path)

    with pytest.raises(builder.VerificationError, match="runtime wheel dependency authority"):
        builder._candidate_runtime_direct_versions(inputs)


def test_candidate_metadata_authority_is_hash_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = builder._candidate_json(builder._CANDIDATE_TOOLCHAIN_INPUTS)
    path = tmp_path / "wheel-cache-policy.json"
    path.write_bytes(builder._CANDIDATE_WHEEL_POLICY.read_bytes() + b"\n")
    monkeypatch.setattr(builder, "_CANDIDATE_WHEEL_POLICY", path)

    with pytest.raises(builder.VerificationError, match="wheel cache policy authority drifted"):
        builder._candidate_runtime_direct_versions(inputs)


@pytest.mark.parametrize(
    ("requirement", "message"),
    (
        ("foreign (>=1,,<2) ; extra == 'dev'", "metadata authority"),
        (
            "foreign (>=1) ; extra == 'dev' trailing",
            "dependency is invalid",
        ),
    ),
)
def test_candidate_metadata_rejects_malformed_inactive_requirement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    requirement: str,
    message: str,
) -> None:
    inputs = builder._candidate_json(builder._CANDIDATE_TOOLCHAIN_INPUTS)
    requirements = list(_candidate_requires_dist())
    requirements[-1] = requirement
    _bind_candidate_requires_dist(tmp_path, monkeypatch, requirements)
    wheel = tmp_path / WHEEL_FILENAME
    _write_candidate_wheel(
        wheel,
        inputs,
        requirements=tuple(requirements),
    )

    with pytest.raises(builder.VerificationError, match=message):
        builder._verify_candidate_wheel_archive(wheel)


@pytest.mark.parametrize(
    ("requirement", "message"),
    (
        ("click[bad extra] (>=8.4.1,<9.0.0)", "metadata authority"),
        (
            'click (>=8.4.1,<9.0.0) ; sys_platform != "win32\'',
            "dependency is invalid",
        ),
    ),
    ids=("invalid-extra", "mismatched-marker-quotes"),
)
def test_candidate_metadata_rejects_malformed_requirement_grammar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    requirement: str,
    message: str,
) -> None:
    inputs = builder._candidate_json(builder._CANDIDATE_TOOLCHAIN_INPUTS)
    requirements = list(_candidate_requires_dist())
    requirements[0] = requirement
    _bind_candidate_requires_dist(tmp_path, monkeypatch, requirements)
    wheel = tmp_path / WHEEL_FILENAME
    _write_candidate_wheel(wheel, inputs, requirements=tuple(requirements))

    with pytest.raises(builder.VerificationError, match=message):
        builder._verify_candidate_wheel_archive(wheel)


@pytest.mark.parametrize(
    "requirement",
    (
        'simplejson (>=1evil) ; extra == "visualization"',
        'simplejson (>=) ; extra == "visualization"',
        'simplejson (==*) ; extra == "visualization"',
        'simplejson (> =8.4.1) ; extra == "visualization"',
    ),
    ids=("invalid-version", "missing-operand", "wildcard", "token-whitespace"),
)
def test_candidate_metadata_rejects_invalid_inactive_specifier_operands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    requirement: str,
) -> None:
    inputs = builder._candidate_json(builder._CANDIDATE_TOOLCHAIN_INPUTS)
    requirements = list(_candidate_requires_dist())
    requirements[-1] = requirement
    _bind_candidate_requires_dist(tmp_path, monkeypatch, requirements)
    wheel = tmp_path / WHEEL_FILENAME
    _write_candidate_wheel(wheel, inputs, requirements=tuple(requirements))

    with pytest.raises(builder.VerificationError, match="metadata authority"):
        builder._verify_candidate_wheel_archive(wheel)


def test_candidate_metadata_rejects_transitive_runtime_wheel_as_direct(
    tmp_path: Path,
) -> None:
    inputs = builder._candidate_json(builder._CANDIDATE_TOOLCHAIN_INPUTS)
    wheel = tmp_path / WHEEL_FILENAME
    _write_candidate_wheel(
        wheel,
        inputs,
        requirements=(*_candidate_requires_dist(), "python-dateutil (>=2.8.2)"),
    )

    with pytest.raises(builder.VerificationError, match="Requires-Dist"):
        builder._verify_candidate_wheel_archive(wheel)


@pytest.mark.parametrize(
    ("package_member", "extra_member"),
    (
        ("nautilus_trader/__init__.py", "foreign/__init__.py"),
        ("nautilus_trader", None),
    ),
)
def test_candidate_wheel_rejects_foreign_top_level_payload_with_valid_record(
    tmp_path: Path, package_member: str, extra_member: str | None,
) -> None:
    inputs = builder._candidate_json(builder._CANDIDATE_TOOLCHAIN_INPUTS)
    wheel = tmp_path / WHEEL_FILENAME
    _write_candidate_wheel(
        wheel,
        inputs,
        extra_member=extra_member,
        package_member=package_member,
    )

    with pytest.raises(builder.VerificationError, match="payload namespace"):
        builder._verify_candidate_wheel_archive(wheel)


def test_candidate_artifact_validator_recomputes_and_binds_every_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, inputs, roots, document = _write_candidate_artifact(tmp_path, monkeypatch)
    validator = getattr(materializer, "_validate_candidate_artifact", None)
    assert callable(validator), "shared exact candidate artifact validator is missing"

    wheel, observed, _raw = validator(builder, engine, inputs, roots)
    assert wheel.name == WHEEL_FILENAME
    assert observed == document

    def missing_top(value: dict[str, object]) -> None:
        value.pop("network")

    def extra_top(value: dict[str, object]) -> None:
        value["foreign"] = True

    def extra_nested(value: dict[str, object]) -> None:
        value["python"]["foreign"] = True

    def engine_drift(value: dict[str, object]) -> None:
        value["engine"]["upstream_commit"] = "0" * 40

    def python_drift(value: dict[str, object]) -> None:
        value["python"]["executable_sha256"] = "0" * 64

    def source_drift(value: dict[str, object]) -> None:
        value["source"]["verified_extracted_tree_sha256"] = "0" * 64

    def policy_drift(value: dict[str, object]) -> None:
        value["policy_hashes"]["engine_build_policy_sha256"] = "0" * 64

    def toolchain_drift(value: dict[str, object]) -> None:
        value["toolchain"]["command_router_authority"] = "FOREIGN"

    def wheel_drift(value: dict[str, object]) -> None:
        value["wheel"]["sha256"] = "0" * 64

    def native_drift(value: dict[str, object]) -> None:
        value["native_libraries"][0]["size"] += 1

    def runtime_wheel_drift(value: dict[str, object]) -> None:
        value["runtime_wheels"][0]["version"] = "0"

    def reproducibility_drift(value: dict[str, object]) -> None:
        value["reproducible_build"]["raw_wheel_equality"] = False

    def source_fd_drift(value: dict[str, object]) -> None:
        value["reproducible_build"]["source_fd_identities"][0][
            "P1_U04_SOURCE_ST_DEV"
        ] = "01"

    def source_fd_reuse(value: dict[str, object]) -> None:
        value["reproducible_build"]["source_fd_identities"][1] = value[
            "reproducible_build"
        ]["source_fd_identities"][0].copy()

    def process_identity_drift(value: dict[str, object]) -> None:
        value["reproducible_build"]["process_identities"][0]["boot_id"] = "foreign"

    def process_identity_reuse(value: dict[str, object]) -> None:
        value["reproducible_build"]["process_identities"][1] = value[
            "reproducible_build"
        ]["process_identities"][0].copy()

    for mutate in (
        missing_top,
        extra_top,
        extra_nested,
        engine_drift,
        python_drift,
        source_drift,
        policy_drift,
        toolchain_drift,
        wheel_drift,
        native_drift,
        runtime_wheel_drift,
        reproducibility_drift,
        source_fd_drift,
        source_fd_reuse,
        process_identity_drift,
        process_identity_reuse,
    ):
        drifted = json.loads(json.dumps(document))
        mutate(drifted)
        _replace_candidate_artifact_manifest(tmp_path, drifted)
        with pytest.raises(
            materializer.RuntimeClosureMaterializationError,
            match="candidate artifact",
        ):
            validator(builder, engine, inputs, roots)


@pytest.mark.parametrize("drift", ("contents", "mode"))
def test_candidate_artifact_validator_binds_exact_build_and_x4_receipt_digests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drift: str
) -> None:
    engine, inputs, roots, _document = _write_candidate_artifact(tmp_path, monkeypatch)
    validator = materializer._validate_candidate_artifact

    validator(builder, engine, inputs, roots)

    build_b_receipt = tmp_path / "build-b" / "build-receipt.json"
    if drift == "contents":
        build_b_receipt.chmod(0o600)
        drifted = json.loads(build_b_receipt.read_bytes())
        drifted["x4_authority_receipt_sha256"] = "f" * 64
        build_b_receipt.write_text(
            json.dumps(drifted, sort_keys=True, indent=2) + "\n", encoding="ascii"
        )
        build_b_receipt.chmod(0o400)
    else:
        build_b_receipt.chmod(0o500)

    with pytest.raises(
        materializer.RuntimeClosureMaterializationError,
        match="reproducibility authority",
    ):
        validator(builder, engine, inputs, roots)


@pytest.mark.parametrize("missing", (WHEEL_FILENAME, "artifact-core.json"))
def test_candidate_artifact_validator_rejects_incomplete_sealed_build_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    missing: str,
) -> None:
    engine, inputs, roots, _document = _write_candidate_artifact(
        tmp_path, monkeypatch
    )
    target = tmp_path / "build-b" / missing
    target.parent.chmod(0o700)
    target.chmod(0o600)
    target.unlink()
    target.parent.chmod(0o500)

    with pytest.raises(
        materializer.RuntimeClosureMaterializationError,
        match="candidate artifact reproducibility authority drifted",
    ):
        materializer._validate_candidate_artifact(builder, engine, inputs, roots)


def test_candidate_artifact_validator_rejects_minimal_build_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, inputs, roots, document = _write_candidate_artifact(
        tmp_path, monkeypatch
    )
    build_b_receipt = tmp_path / "build-b" / "build-receipt.json"
    build_b_receipt.chmod(0o600)
    build_b_receipt.write_text(
        json.dumps(
            {"x4_authority_receipt_sha256": "e" * 64},
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="ascii",
    )
    build_b_receipt.chmod(0o400)
    document["reproducible_build"]["build_b_receipt_sha256"] = hashlib.sha256(
        build_b_receipt.read_bytes()
    ).hexdigest()
    _replace_candidate_artifact_manifest(tmp_path, document)

    with pytest.raises(
        materializer.RuntimeClosureMaterializationError,
        match="candidate artifact reproducibility authority drifted",
    ):
        materializer._validate_candidate_artifact(builder, engine, inputs, roots)


def test_candidate_artifact_validator_rejects_shared_forged_authority_identities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, inputs, roots, document = _write_candidate_artifact(
        tmp_path, monkeypatch
    )
    forged = {"fixture": {"sha256": "0" * 64}}
    for label in ("a", "b"):
        build_receipt = tmp_path / f"build-{label}" / "build-receipt.json"
        build_receipt.chmod(0o600)
        receipt_document = json.loads(build_receipt.read_bytes())
        receipt_document["authority_identities"] = forged
        build_receipt.write_text(
            json.dumps(receipt_document, sort_keys=True, indent=2) + "\n",
            encoding="ascii",
        )
        build_receipt.chmod(0o400)
        document["reproducible_build"][
            f"build_{label}_receipt_sha256"
        ] = hashlib.sha256(build_receipt.read_bytes()).hexdigest()
    _replace_candidate_artifact_manifest(tmp_path, document)

    with pytest.raises(
        materializer.RuntimeClosureMaterializationError,
        match="candidate artifact reproducibility authority drifted",
    ):
        materializer._validate_candidate_artifact(builder, engine, inputs, roots)


@pytest.mark.parametrize(
    "mutation",
    (
        "extra_file",
        "wrong_label",
        "receipt_digest",
        "wheel_digest",
        "core_digest",
        "reused_process_identity",
        "reused_source_identity",
        "candidate_drift",
        "policy_drift",
        "authority_drift",
        "sanitized_environment_drift",
        "final_core_mismatch",
    ),
)
def test_candidate_artifact_validator_rejects_sealed_build_record_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    engine, inputs, roots, document = _write_candidate_artifact(
        tmp_path, monkeypatch
    )
    build_b_directory = tmp_path / "build-b"
    build_b_receipt = build_b_directory / "build-receipt.json"
    receipt_document = json.loads(build_b_receipt.read_bytes())
    receipt_changed = False

    if mutation == "extra_file":
        build_b_directory.chmod(0o700)
        extra = build_b_directory / "foreign"
        extra.write_bytes(b"foreign")
        extra.chmod(0o400)
        build_b_directory.chmod(0o500)
    elif mutation == "receipt_digest":
        document["reproducible_build"]["build_b_receipt_sha256"] = "0" * 64
    elif mutation == "wrong_label":
        receipt_document["label"] = "A"
        receipt_changed = True
    elif mutation == "wheel_digest":
        receipt_document["wheel"]["sha256"] = "0" * 64
        receipt_changed = True
    elif mutation == "core_digest":
        receipt_document["artifact_core"]["sha256"] = "0" * 64
        receipt_changed = True
    elif mutation == "reused_process_identity":
        receipt_document["process_identity"] = document["reproducible_build"][
            "process_identities"
        ][0]
        receipt_changed = True
    elif mutation == "reused_source_identity":
        receipt_document["source_identity"] = document["reproducible_build"][
            "source_fd_identities"
        ][0]
        receipt_changed = True
    elif mutation == "candidate_drift":
        receipt_document["candidate"]["head"] = "0" * 40
        receipt_changed = True
    elif mutation == "policy_drift":
        receipt_document["policy_sha256"]["engine_build"] = "0" * 64
        receipt_changed = True
    elif mutation == "authority_drift":
        receipt_document["authority_identities"]["fixture"]["sha256"] = "0" * 64
        receipt_changed = True
    elif mutation == "sanitized_environment_drift":
        receipt_document["sanitized_environment_sha256"] = "0" * 64
        receipt_changed = True
    else:
        assert mutation == "final_core_mismatch"
        build_b_core = build_b_directory / "artifact-core.json"
        build_b_core.chmod(0o600)
        drifted_core = json.loads(build_b_core.read_bytes())
        drifted_core["activation_status"] = "FOREIGN"
        build_b_core.write_text(
            json.dumps(drifted_core, sort_keys=True, indent=2) + "\n",
            encoding="ascii",
        )
        build_b_core.chmod(0o400)
        core_raw = build_b_core.read_bytes()
        receipt_document["artifact_core"] = {
            "filename": "artifact-core.json",
            "sha256": hashlib.sha256(core_raw).hexdigest(),
            "size": len(core_raw),
        }
        receipt_changed = True

    if receipt_changed:
        build_b_receipt.chmod(0o600)
        build_b_receipt.write_text(
            json.dumps(receipt_document, sort_keys=True, indent=2) + "\n",
            encoding="ascii",
        )
        build_b_receipt.chmod(0o400)
        document["reproducible_build"]["build_b_receipt_sha256"] = hashlib.sha256(
            build_b_receipt.read_bytes()
        ).hexdigest()
    _replace_candidate_artifact_manifest(tmp_path, document)

    with pytest.raises(
        materializer.RuntimeClosureMaterializationError,
        match="candidate artifact reproducibility authority drifted",
    ):
        materializer._validate_candidate_artifact(builder, engine, inputs, roots)


def test_candidate_materialization_and_attestation_share_artifact_validator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = {"candidate": {}}
    inputs: dict[str, object] = {}
    roots = {"candidate_runtime_root": tmp_path / "runtime"}
    calls: list[str] = []

    def stop_after_validation(*_args):
        calls.append("validated")
        raise materializer.RuntimeClosureMaterializationError("validation stop")

    monkeypatch.setattr(
        materializer,
        "_candidate_authority",
        lambda: (builder, engine, inputs, roots),
    )
    monkeypatch.setattr(
        materializer, "_validate_candidate_artifact", stop_after_validation
    )
    with pytest.raises(
        materializer.RuntimeClosureMaterializationError, match="validation stop"
    ):
        materializer.materialize_candidate_runtime_closure()

    class CandidateBuilder:
        @staticmethod
        def _verify_candidate_authority():
            return engine, inputs

        @staticmethod
        def _candidate_roots(_engine):
            return roots

    monkeypatch.setattr(materializer, "_candidate_builder_tool", CandidateBuilder)
    with pytest.raises(
        materializer.RuntimeClosureMaterializationError, match="validation stop"
    ):
        materializer.attest_candidate_runtime_closure()

    assert calls == ["validated", "validated"]


def test_candidate_closure_attestation_cross_binds_exact_authorities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, base_runtime, base_policy, artifact, inputs, artifact_sha256 = (
        _write_candidate_closure_fixture(tmp_path, monkeypatch)
    )
    manifest = materializer._attest_candidate_closure(
        root,
        artifact=artifact,
        artifact_sha256=artifact_sha256,
        inputs=inputs,
        base_runtime=base_runtime,
        base_policy=base_policy,
    )

    assert set(manifest) == materializer._CANDIDATE_CLOSURE_FIELDS
    assert manifest["engine"] == artifact["engine"]
    assert manifest["source"] == artifact["source"]
    assert manifest["python"] == artifact["python"]
    assert manifest["policy_hashes"] == artifact["policy_hashes"]
    assert manifest["qualification"] == {
        "argv": ["/usr/bin/python3.12", "-I", "-S"],
        "script_sha256": hashlib.sha256(
            materializer._CANDIDATE_IMPORT_SCRIPT.encode("ascii")
        ).hexdigest(),
        "status": "PASS",
    }
    assert manifest["base_runtime"]["selected_authority"]["policy"][
        "profile_manifest_schema_version"
    ] == 6
    assert manifest["base_runtime"]["historical_manifest"]["schema_version"] == 1

    manifest_path = root / "closure-manifest.json"
    original = json.loads(manifest_path.read_text(encoding="ascii"))

    def missing_top(value: dict[str, object]) -> None:
        value.pop("source")

    def extra_top(value: dict[str, object]) -> None:
        value["foreign"] = True

    def drift_engine(value: dict[str, object]) -> None:
        value["engine"]["version"] = "1.230.0"

    def drift_source(value: dict[str, object]) -> None:
        value["source"]["sha256"] = "0" * 64

    def drift_python(value: dict[str, object]) -> None:
        value["python"]["abi"] = "cp313"

    def drift_policy(value: dict[str, object]) -> None:
        value["policy_hashes"]["engine_build_policy_sha256"] = "0" * 64

    def drift_qualification(value: dict[str, object]) -> None:
        value["qualification"]["status"] = "SKIPPED"

    def drift_base(value: dict[str, object]) -> None:
        value["base_runtime"]["selected_authority"]["policy"][
            "profile_manifest_schema_version"
        ] = 1

    for mutate in (
        missing_top,
        extra_top,
        drift_engine,
        drift_source,
        drift_python,
        drift_policy,
        drift_qualification,
        drift_base,
    ):
        drifted = json.loads(json.dumps(original))
        mutate(drifted)
        manifest_path.chmod(0o600)
        manifest_path.write_bytes(materializer._canonical_json_bytes(drifted) + b"\n")
        manifest_path.chmod(0o400)
        with pytest.raises(
            materializer.RuntimeClosureMaterializationError,
            match="candidate closure",
        ):
            materializer._attest_candidate_closure(
                root,
                artifact=artifact,
                artifact_sha256=artifact_sha256,
                inputs=inputs,
                base_runtime=base_runtime,
                base_policy=base_policy,
            )


def test_candidate_base_binding_requires_physical_selected_schema6_attestation() -> None:
    resolver = getattr(materializer, "_selected_base_authority", None)
    assert callable(resolver), "selected schema-6 authority resolver is missing"


def test_selected_schema6_authority_binds_complete_policy_manifest_and_attestation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        rollback,
        policy,
        historical_manifest,
        historical_records,
        selected_manifest,
        attestation,
        calls,
    ) = _write_selected_base_authority_fixture(tmp_path, monkeypatch)

    selected = materializer._selected_base_authority(
        rollback,
        base_policy=policy,
        historical_manifest=historical_manifest,
        historical_records=historical_records,
    )

    assert set(selected) == {
        "artifact_generation",
        "artifact_manifest_sha256",
        "attestation",
        "closure_sha256",
        "generation",
        "manifest",
        "manifest_mode",
        "manifest_sha256",
        "manifest_size",
        "non_engine_file_count",
        "non_engine_file_inventory_sha256",
        "policy",
        "policy_sha256",
    }
    assert selected["generation"] == "runtime-closure-v12-r12-simulation"
    assert selected["artifact_generation"] == (
        "artifacts/nautilus-1.227.0-cp312-rust-bound-input-ff2e7753974c"
    )
    assert selected["policy"] == policy
    assert set(selected["policy"]) == materializer._POLICY_FIELDS
    assert selected["manifest"] == selected_manifest
    assert selected["closure_sha256"] == attestation.closure_sha256
    assert set(selected["attestation"]) == {
        field.name for field in fields(CompleteEngineClosureAttestation)
    }
    assert set(selected["attestation"]["mounts"][0]) == {
        field.name for field in fields(ReadOnlyClosureMount)
    }
    assert set(selected["attestation"]["sandbox"]) == {
        field.name for field in fields(OsSandboxProof)
    }
    assert set(selected["attestation"]["native_entry_guard"]) == {
        field.name for field in fields(NativeEntryGuardAttestation)
    }
    assert calls[0][0] == materializer.NautilusClosureConfig(
        runtime_root=rollback / "runtime-closure-v12-r12-simulation",
        artifact_directory=rollback
        / "artifacts/nautilus-1.227.0-cp312-rust-bound-input-ff2e7753974c",
        sandbox_executable=materializer._CANDIDATE_SANDBOX,
    )
    assert calls[0][1] == "execution-simulation"


def test_selected_schema6_authority_rejects_symlinked_rollback_ancestor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "authority"
    alias.symlink_to(real, target_is_directory=True)
    rollback, policy, historical_manifest, historical_records, *_rest = (
        _write_selected_base_authority_fixture(alias, monkeypatch)
    )

    with pytest.raises(
        materializer.RuntimeClosureMaterializationError,
        match="symlinked ancestor",
    ):
        materializer._selected_base_authority(
            rollback,
            base_policy=policy,
            historical_manifest=historical_manifest,
            historical_records=historical_records,
        )


@pytest.mark.parametrize("drift", ("root", "generation"))
def test_selected_schema6_authority_rejects_selected_root_or_generation_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drift: str
) -> None:
    rollback, policy, historical_manifest, historical_records, *_rest = (
        _write_selected_base_authority_fixture(tmp_path, monkeypatch)
    )
    if drift == "root":
        selected_root = rollback / "runtime-closure-v12-r12-simulation"
        selected_root.chmod(0o700)
    else:
        monkeypatch.setattr(
            materializer,
            "_SELECTED_RUNTIME_GENERATION",
            "runtime-closure-unreviewed",
        )

    with pytest.raises(
        materializer.RuntimeClosureMaterializationError,
        match="selected schema-6 runtime",
    ):
        materializer._selected_base_authority(
            rollback,
            base_policy=policy,
            historical_manifest=historical_manifest,
            historical_records=historical_records,
        )


@pytest.mark.parametrize(
    "field",
    (
        "timeout_seconds",
        "argv_prefix",
        "entrypoint",
        "launcher_inventory",
        "native_entry_guard",
        "schema_version",
    ),
)
def test_selected_schema6_authority_rejects_omitted_policy_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    rollback, policy, historical_manifest, historical_records, *_rest = (
        _write_selected_base_authority_fixture(tmp_path, monkeypatch)
    )
    policy_path = materializer._CANDIDATE_BASE_POLICY
    drifted = json.loads(policy_path.read_text(encoding="ascii"))
    drifted.pop(field)
    policy_path.write_bytes(materializer._canonical_json_bytes(drifted) + b"\n")

    with pytest.raises(
        materializer.RuntimeClosureMaterializationError,
        match="selected schema-6 policy",
    ):
        materializer._selected_base_authority(
            rollback,
            base_policy=policy,
            historical_manifest=historical_manifest,
            historical_records=historical_records,
        )


@pytest.mark.parametrize("drift", ("bytes", "mode", "expected-hash"))
def test_selected_schema6_authority_rejects_manifest_identity_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drift: str
) -> None:
    rollback, policy, historical_manifest, historical_records, selected, *_rest = (
        _write_selected_base_authority_fixture(tmp_path, monkeypatch)
    )
    manifest_path = (
        rollback / "runtime-closure-v12-r12-simulation/closure-manifest.json"
    )
    if drift == "bytes":
        manifest_path.chmod(0o600)
        manifest_path.write_bytes(materializer._canonical_json_bytes(selected) + b"\n")
        manifest_path.chmod(0o400)
    elif drift == "mode":
        manifest_path.chmod(0o500)
    else:
        monkeypatch.setattr(materializer, "_SELECTED_MANIFEST_SHA256", "0" * 64)

    with pytest.raises(
        materializer.RuntimeClosureMaterializationError,
        match="selected schema-6 manifest",
    ):
        materializer._selected_base_authority(
            rollback,
            base_policy=policy,
            historical_manifest=historical_manifest,
            historical_records=historical_records,
        )


def test_selected_schema6_authority_rejects_artifact_hash_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rollback, policy, historical_manifest, historical_records, *_rest = (
        _write_selected_base_authority_fixture(tmp_path, monkeypatch)
    )
    monkeypatch.setattr(
        materializer, "_SELECTED_ARTIFACT_MANIFEST_SHA256", "0" * 64
    )

    with pytest.raises(
        materializer.RuntimeClosureMaterializationError,
        match="selected schema-6 artifact",
    ):
        materializer._selected_base_authority(
            rollback,
            base_policy=policy,
            historical_manifest=historical_manifest,
            historical_records=historical_records,
        )


@pytest.mark.parametrize("drift", ("result", "closure", "mount"))
def test_selected_schema6_authority_rejects_attestor_result_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drift: str
) -> None:
    (
        rollback,
        policy,
        historical_manifest,
        historical_records,
        _selected,
        attestation,
        _calls,
    ) = _write_selected_base_authority_fixture(tmp_path, monkeypatch)
    if drift == "result":
        drifted = replace(attestation, timeout_seconds=attestation.timeout_seconds + 1)
    elif drift == "closure":
        drifted = replace(attestation, closure_sha256="0" * 64)
    else:
        mount = replace(attestation.mounts[0], target=PurePosixPath("/foreign"))
        drifted = replace(attestation, mounts=(mount, *attestation.mounts[1:]))
    monkeypatch.setattr(
        materializer,
        "attest_nautilus_backtest_closure",
        lambda *_args, **_kwargs: drifted,
    )

    with pytest.raises(
        materializer.RuntimeClosureMaterializationError,
        match="selected schema-6 attestation",
    ):
        materializer._selected_base_authority(
            rollback,
            base_policy=policy,
            historical_manifest=historical_manifest,
            historical_records=historical_records,
        )


@pytest.mark.parametrize("authority", ("rollback", "runtime", "artifact"))
def test_selected_schema6_authority_rejects_directory_identity_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, authority: str
) -> None:
    (
        rollback,
        policy,
        historical_manifest,
        historical_records,
        _selected,
        attestation,
        _calls,
    ) = _write_selected_base_authority_fixture(tmp_path, monkeypatch)
    paths = {
        "rollback": rollback,
        "runtime": rollback / "runtime-closure-v12-r12-simulation",
        "artifact": rollback
        / "artifacts/nautilus-1.227.0-cp312-rust-bound-input-ff2e7753974c",
    }

    def relocate(_config, *, expected_profile: str):
        assert expected_profile == "execution-simulation"
        source = paths[authority]
        moved = source.with_name(source.name + "-moved")
        source.rename(moved)
        source.symlink_to(moved, target_is_directory=True)
        return attestation

    monkeypatch.setattr(
        materializer, "attest_nautilus_backtest_closure", relocate
    )

    with pytest.raises(
        materializer.RuntimeClosureMaterializationError,
        match="symlinked ancestor|identity changed",
    ):
        materializer._selected_base_authority(
            rollback,
            base_policy=policy,
            historical_manifest=historical_manifest,
            historical_records=historical_records,
        )


@pytest.mark.parametrize("drift", ("path", "mode", "extra", "missing"))
def test_selected_schema6_authority_rejects_non_engine_projection_divergence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drift: str
) -> None:
    rollback, policy, historical_manifest, historical_records, selected, *_rest = (
        _write_selected_base_authority_fixture(tmp_path, monkeypatch)
    )
    manifest_path = (
        rollback / "runtime-closure-v12-r12-simulation/closure-manifest.json"
    )
    records = selected["files"]
    assert isinstance(records, list)
    non_engine = next(
        record for record in records if record["target"] == "/usr/lib/libbase.so"
    )
    if drift == "path":
        non_engine["path"] = "files/usr/lib/libdrift.so"
    elif drift == "mode":
        non_engine["mode"] = "0500"
    elif drift == "extra":
        records.append(
            {
                "mode": "0400",
                "path": "files/usr/lib/libextra.so",
                "sha256": "0" * 64,
                "size": 1,
                "target": "/usr/lib/libextra.so",
            }
        )
    else:
        records.remove(non_engine)
    manifest_path.chmod(0o600)
    manifest_path.write_bytes(materializer._canonical_json_bytes(selected) + b"\n")
    manifest_path.chmod(0o400)
    monkeypatch.setattr(
        materializer,
        "_SELECTED_MANIFEST_SHA256",
        hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
    )

    with pytest.raises(materializer.RuntimeClosureMaterializationError):
        materializer._selected_base_authority(
            rollback,
            base_policy=policy,
            historical_manifest=historical_manifest,
            historical_records=historical_records,
        )


def test_candidate_artifact_manifest_failure_precedes_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    roots = {"candidate_build_root": tmp_path}
    core = {"wheel": {"filename": "candidate.whl"}}
    receipt = {"build_count": 2}
    monkeypatch.setattr(builder, "_candidate_json", lambda _path: {})
    renamed = False
    rename = builder._rename_noreplace

    def observe_rename(source: Path, destination: Path, **kwargs) -> None:
        nonlocal renamed
        renamed = True
        rename(source, destination, **kwargs)

    monkeypatch.setattr(builder, "_rename_noreplace", observe_rename)

    with pytest.raises(builder.VerificationError, match="manifest drifted"):
        builder._publish_candidate_artifacts(
            roots,
            b"candidate-wheel",
            core,
            receipt,
        )

    assert not renamed
    assert not (tmp_path / "artifacts").exists()


def test_candidate_artifact_cleanup_hook_cannot_delete_swapped_competitor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_root = tmp_path / "build"
    build_root.mkdir(mode=0o700)
    roots = {"candidate_build_root": build_root}
    core = {"wheel": {"filename": "candidate.whl"}}
    receipt = {"build_count": 2}
    manifest = {**core, "reproducible_build": receipt}
    destination = build_root / "artifacts"
    real_thaw = builder._thaw_tree
    swapped = False

    def staged_manifest(path: Path) -> dict[str, object]:
        return {} if path.parent == destination else manifest

    def swap_at_thaw(path: Path) -> None:
        nonlocal swapped
        if path == destination:
            swapped = True
            destination.rename(build_root / "task-owned-moved")
            destination.mkdir(mode=0o700)
            (destination / "competitor").write_bytes(b"competitor")
        real_thaw(path)

    monkeypatch.setattr(builder, "_candidate_json", staged_manifest)
    monkeypatch.setattr(builder, "_thaw_tree", swap_at_thaw)
    error: BaseException | None = None
    try:
        result = builder._publish_candidate_artifacts(
            roots, b"wheel", core, receipt
        )
    except BaseException as exc:  # exact R5 cleanup race evidence
        error = exc
        result = None

    assert not (swapped and not destination.exists()), (
        "post-publication thaw deleted a swapped competitor"
    )
    assert error is None
    assert result == destination
    assert (destination / "candidate.whl").read_bytes() == b"wheel"


def test_candidate_artifact_staging_identity_drift_fails_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    core = {"wheel": {"filename": "candidate.whl"}}
    receipt = {"build_count": 2}
    manifest = {**core, "reproducible_build": receipt}
    moved = tmp_path / "moved-staging"

    def swap_staging(path: Path) -> dict[str, object]:
        stage = path.parent
        if stage.name.startswith(".artifacts-"):
            stage.rename(moved)
            stage.mkdir(mode=0o700)
            (stage / "competitor").write_bytes(b"competitor")
            stage.chmod(0o500)
        return manifest

    monkeypatch.setattr(builder, "_candidate_json", swap_staging)

    with pytest.raises(builder.VerificationError, match="staging identity"):
        builder._publish_candidate_artifacts(
            {"candidate_build_root": tmp_path}, b"wheel", core, receipt
        )

    assert not (tmp_path / "artifacts").exists()
    assert (next(tmp_path.glob(".artifacts-*")) / "competitor").read_bytes() == (
        b"competitor"
    )
    assert (moved / "candidate.whl").read_bytes() == b"wheel"


def test_candidate_artifact_final_boundary_rejects_substituted_staging_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    core = {"wheel": {"filename": "candidate.whl"}}
    receipt = {"build_count": 2}
    rename = builder._rename_noreplace
    moved: Path | None = None
    competitor: Path | None = None

    def substitute_then_rename(source: Path, destination: Path, **kwargs) -> None:
        nonlocal moved, competitor
        moved = source.with_name(source.name + "-validated")
        competitor = source
        source.rename(moved)
        source.mkdir(mode=0o700)
        (source / "competitor").write_bytes(b"competitor")
        source.chmod(0o500)
        rename(source, destination, **kwargs)

    monkeypatch.setattr(builder, "_rename_noreplace", substitute_then_rename)

    with pytest.raises(builder.VerificationError, match="staging identity"):
        builder._publish_candidate_artifacts(
            {"candidate_build_root": tmp_path}, b"wheel", core, receipt
        )

    assert not (tmp_path / "artifacts").exists()
    assert competitor is not None
    assert (competitor / "competitor").read_bytes() == b"competitor"
    assert moved is not None
    assert (moved / "candidate.whl").read_bytes() == b"wheel"


class _Renameat2LookupSwap:
    argtypes: object = None
    restype: object = None

    def __call__(
        self,
        source_parent_fd: int,
        source_name: bytes,
        destination_parent_fd: int,
        destination_name: bytes,
        _flags: int,
    ) -> int:
        moved_name = source_name + b"-validated"
        os.rename(
            source_name,
            moved_name,
            src_dir_fd=source_parent_fd,
            dst_dir_fd=source_parent_fd,
        )
        os.mkdir(source_name, mode=0o700, dir_fd=source_parent_fd)
        forged = os.open(
            source_name + b"/forged",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o400,
            dir_fd=source_parent_fd,
        )
        os.write(forged, b"forged")
        os.close(forged)
        os.chmod(source_name, 0o500, dir_fd=source_parent_fd)
        os.rename(
            source_name,
            destination_name,
            src_dir_fd=source_parent_fd,
            dst_dir_fd=destination_parent_fd,
        )
        return 0


class _LibcWithRenameLookupSwap:
    def __init__(self) -> None:
        self.renameat2 = _Renameat2LookupSwap()


class _Renameat2Adversary:
    argtypes: object = None
    restype: object = None

    def __init__(self, *, repopulate_source: bool = False) -> None:
        self.calls = 0
        self.repopulate_source = repopulate_source
        self.source_name: bytes | None = None

    def __call__(
        self,
        source_parent_fd: int,
        source_name: bytes,
        destination_parent_fd: int,
        destination_name: bytes,
        _flags: int,
    ) -> int:
        self.calls += 1
        self.source_name = source_name
        os.rename(
            source_name,
            destination_name,
            src_dir_fd=source_parent_fd,
            dst_dir_fd=destination_parent_fd,
        )
        os.chmod(destination_name, 0o700, dir_fd=destination_parent_fd)
        if self.repopulate_source:
            os.mkdir(source_name, mode=0o700, dir_fd=source_parent_fd)
            marker = os.open(
                source_name + b"/repopulated",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o400,
                dir_fd=source_parent_fd,
            )
            os.write(marker, b"repopulated")
            os.close(marker)
        return 0


class _LibcWithRenameAdversary:
    def __init__(self, adversary: _Renameat2Adversary) -> None:
        self.renameat2 = adversary


def _fail_destination_confirmation(
    module: object,
    monkeypatch: pytest.MonkeyPatch,
    destination: Path,
) -> None:
    module_os = getattr(module, "os")
    real_stat = module_os.stat

    def fail(path, *args, **kwargs):
        if (
            path == destination.name
            and kwargs.get("dir_fd") is not None
            and kwargs.get("follow_symlinks") is False
        ):
            raise OSError(errno.EIO, "adversarial post-rename confirmation failure")
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(module_os, "stat", fail)


def test_candidate_artifact_kernel_lookup_substitution_cannot_return_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        builder.ctypes,
        "CDLL",
        lambda *_args, **_kwargs: _LibcWithRenameLookupSwap(),
    )

    with pytest.raises(builder.VerificationError, match="published artifact identity"):
        builder._publish_candidate_artifacts(
            {"candidate_build_root": tmp_path},
            b"wheel",
            {"wheel": {"filename": "candidate.whl"}},
            {"build_count": 2},
        )

    destination = tmp_path / "artifacts"
    assert (destination / "forged").read_bytes() == b"forged"


def test_candidate_artifact_same_inode_chmod_at_rename_cannot_return_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adversary = _Renameat2Adversary()
    monkeypatch.setattr(
        builder.ctypes,
        "CDLL",
        lambda *_args, **_kwargs: _LibcWithRenameAdversary(adversary),
    )

    with pytest.raises(builder.VerificationError, match="published artifact identity"):
        builder._publish_candidate_artifacts(
            {"candidate_build_root": tmp_path},
            b"wheel",
            {"wheel": {"filename": "candidate.whl"}},
            {"build_count": 2},
        )

    assert adversary.calls == 1
    assert (tmp_path / "artifacts").stat().st_mode & 0o777 == 0o700


def test_candidate_artifact_confirmation_failure_retains_repopulated_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adversary = _Renameat2Adversary(repopulate_source=True)
    destination = tmp_path / "artifacts"
    monkeypatch.setattr(
        builder.ctypes,
        "CDLL",
        lambda *_args, **_kwargs: _LibcWithRenameAdversary(adversary),
    )
    _fail_destination_confirmation(builder, monkeypatch, destination)

    with pytest.raises(builder.VerificationError, match="published artifact identity"):
        builder._publish_candidate_artifacts(
            {"candidate_build_root": tmp_path},
            b"wheel",
            {"wheel": {"filename": "candidate.whl"}},
            {"build_count": 2},
        )

    assert adversary.calls == 1
    assert destination.exists()
    assert adversary.source_name is not None
    source = tmp_path / os.fsdecode(adversary.source_name)
    assert (source / "repopulated").read_bytes() == b"repopulated"


def test_candidate_artifact_publication_boundary_follows_complete_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    core = {"wheel": {"filename": "candidate.whl"}}
    receipt = {"build_count": 2}
    manifest = {**core, "reproducible_build": receipt}
    events: list[str] = []
    publish = builder._rename_noreplace

    def validate(_path: Path) -> dict[str, object]:
        events.append("manifest")
        return manifest

    def rename(source: Path, destination: Path, **kwargs) -> None:
        events.append("rename")
        publish(source, destination, **kwargs)

    monkeypatch.setattr(builder, "_candidate_json", validate)
    monkeypatch.setattr(builder, "_rename_noreplace", rename)

    assert builder._publish_candidate_artifacts(
        {"candidate_build_root": tmp_path}, b"wheel", core, receipt
    ) == (tmp_path / "artifacts")

    assert events == ["manifest", "rename"]


def test_candidate_artifact_publication_preserves_preexisting_competitor(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "artifacts"
    destination.mkdir()
    (destination / "competitor").write_bytes(b"competitor")

    with pytest.raises(builder.VerificationError, match="not absent"):
        builder._publish_candidate_artifacts(
            {"candidate_build_root": tmp_path},
            b"wheel",
            {"wheel": {"filename": "candidate.whl"}},
            {},
        )

    assert (destination / "competitor").read_bytes() == b"competitor"


def test_candidate_artifact_successful_publication_remains_sealed(
    tmp_path: Path,
) -> None:
    destination = builder._publish_candidate_artifacts(
        {"candidate_build_root": tmp_path},
        b"wheel",
        {"wheel": {"filename": "candidate.whl"}},
        {},
    )

    assert destination == tmp_path / "artifacts"
    assert destination.stat().st_mode & 0o777 == 0o500


def _prepare_candidate_runtime_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Callable[[Path], dict[str, object]], Callable[[Path], str]]:
    _root, base_runtime, base_policy, artifact, inputs, _artifact_sha256 = (
        _write_candidate_closure_fixture(tmp_path, monkeypatch)
    )
    destination = tmp_path / "published/candidate-runtime"
    destination.parent.mkdir(mode=0o700)
    input_root = tmp_path / "candidate-input"
    runtime_wheel = input_root / "wheels/runtime.whl"
    runtime_wheel.parent.mkdir(parents=True)
    runtime_wheel.write_bytes(b"runtime")
    runtime_wheel.chmod(0o400)
    engine_wheel = tmp_path / WHEEL_FILENAME
    engine_wheel.write_bytes(b"engine")
    engine_wheel.chmod(0o400)
    roots = {
        "candidate_input_root": input_root,
        "candidate_runtime_root": destination,
        "rollback_root": base_runtime.parent,
    }
    candidate_builder = materializer._candidate_builder_tool()
    monkeypatch.setattr(
        materializer,
        "_candidate_authority",
        lambda: (candidate_builder, {}, inputs, roots),
    )
    monkeypatch.setattr(
        materializer,
        "_validate_candidate_artifact",
        lambda *_args: (engine_wheel, artifact, b"artifact"),
    )
    monkeypatch.setattr(materializer, "_load_policy", lambda _path: base_policy)
    attest = lambda root, **_kwargs: {"root": str(root)}
    qualify = lambda _root: hashlib.sha256(
        materializer._CANDIDATE_IMPORT_SCRIPT.encode("ascii")
    ).hexdigest()
    monkeypatch.setattr(materializer, "_attest_candidate_closure", attest)
    monkeypatch.setattr(materializer, "_qualify_candidate_import", qualify)
    return destination, attest, qualify


@pytest.mark.parametrize("failure", ("attestation", "import"))
def test_candidate_runtime_validation_failure_precedes_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    destination, attest, qualify = _prepare_candidate_runtime_publication(
        tmp_path, monkeypatch
    )
    renamed = False
    rename = materializer._renameat2_noreplace

    def observe_rename(*args, **kwargs) -> None:
        nonlocal renamed
        renamed = True
        rename(*args, **kwargs)

    monkeypatch.setattr(materializer, "_renameat2_noreplace", observe_rename)
    if failure == "attestation":
        monkeypatch.setattr(
            materializer,
            "_attest_candidate_closure",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                materializer.RuntimeClosureMaterializationError(
                    "staging attestation failure"
                )
            ),
        )
    else:
        monkeypatch.setattr(
            materializer,
            "_qualify_candidate_import",
            lambda _root: (_ for _ in ()).throw(
                materializer.RuntimeClosureMaterializationError(
                    "staging import failure"
                )
            ),
        )

    with pytest.raises(materializer.RuntimeClosureMaterializationError):
        materializer.materialize_candidate_runtime_closure()

    assert not renamed
    assert not destination.exists()


def test_candidate_runtime_cleanup_hook_cannot_delete_swapped_competitor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination, attest, _qualify = _prepare_candidate_runtime_publication(
        tmp_path, monkeypatch
    )
    real_unseal = materializer._unseal_and_remove
    competitor_deleted = False

    def swap_and_fail(root: Path, **kwargs):
        if root != destination:
            return attest(root, **kwargs)
        raise materializer.RuntimeClosureMaterializationError(
            "post-publish attestation failure"
        )

    def swap_at_unseal(path: Path) -> None:
        nonlocal competitor_deleted
        if path == destination:
            destination.rename(destination.parent / "task-owned-moved")
        destination.mkdir(mode=0o700)
        (destination / "competitor").write_bytes(b"competitor")
        real_unseal(path)
        competitor_deleted = not destination.exists()

    monkeypatch.setattr(materializer, "_attest_candidate_closure", swap_and_fail)
    monkeypatch.setattr(materializer, "_unseal_and_remove", swap_at_unseal)
    error: BaseException | None = None
    try:
        result = materializer.materialize_candidate_runtime_closure()
    except BaseException as exc:  # exact R5 cleanup race evidence
        error = exc
        result = None

    assert not competitor_deleted, "post-publication removal deleted a swapped competitor"
    assert error is None
    assert result == destination
    assert destination.exists()


def test_candidate_runtime_staging_identity_drift_fails_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination, attest, _qualify = _prepare_candidate_runtime_publication(
        tmp_path, monkeypatch
    )
    moved: Path | None = None
    competitor: Path | None = None

    def swap_staging(root: Path, **kwargs):
        nonlocal moved, competitor
        result = attest(root, **kwargs)
        if root != destination:
            moved = root.with_name(root.name + "-moved")
            competitor = root
            root.rename(moved)
            root.mkdir(mode=0o700)
            (root / "competitor").write_bytes(b"competitor")
            root.chmod(0o500)
        return result

    monkeypatch.setattr(materializer, "_attest_candidate_closure", swap_staging)

    with pytest.raises(
        materializer.RuntimeClosureMaterializationError,
        match="staging identity",
    ):
        materializer.materialize_candidate_runtime_closure()

    assert not destination.exists()
    assert competitor is not None
    assert (competitor / "competitor").read_bytes() == b"competitor"
    assert moved is not None
    assert (moved / "closure-manifest.json").is_file()


def test_candidate_runtime_final_boundary_rejects_substituted_staging_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination, _attest, _qualify = _prepare_candidate_runtime_publication(
        tmp_path, monkeypatch
    )
    publish = materializer._publish_noreplace
    moved: Path | None = None
    competitor: Path | None = None

    def substitute_then_publish(staging: Path, destination: Path, **kwargs):
        nonlocal moved, competitor
        moved = staging.with_name(staging.name + "-validated")
        competitor = staging
        staging.rename(moved)
        staging.mkdir(mode=0o700)
        (staging / "competitor").write_bytes(b"competitor")
        staging.chmod(0o500)
        return publish(staging, destination, **kwargs)

    monkeypatch.setattr(materializer, "_publish_noreplace", substitute_then_publish)

    with pytest.raises(
        materializer.RuntimeClosureMaterializationError,
        match="staging identity",
    ):
        materializer.materialize_candidate_runtime_closure()

    assert not destination.exists()
    assert competitor is not None
    assert (competitor / "competitor").read_bytes() == b"competitor"
    assert moved is not None
    assert (moved / "closure-manifest.json").is_file()


def test_candidate_runtime_kernel_lookup_substitution_cannot_return_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination, _attest, _qualify = _prepare_candidate_runtime_publication(
        tmp_path, monkeypatch
    )
    monkeypatch.setattr(
        materializer.ctypes,
        "CDLL",
        lambda *_args, **_kwargs: _LibcWithRenameLookupSwap(),
    )

    with pytest.raises(
        materializer.RuntimeClosureMaterializationError,
        match="published closure identity",
    ):
        materializer.materialize_candidate_runtime_closure()

    assert (destination / "forged").read_bytes() == b"forged"


def test_candidate_runtime_same_inode_chmod_at_rename_cannot_return_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination, _attest, _qualify = _prepare_candidate_runtime_publication(
        tmp_path, monkeypatch
    )
    adversary = _Renameat2Adversary()
    monkeypatch.setattr(
        materializer.ctypes,
        "CDLL",
        lambda *_args, **_kwargs: _LibcWithRenameAdversary(adversary),
    )

    with pytest.raises(
        materializer.RuntimeClosureMaterializationError,
        match="published closure identity",
    ):
        materializer.materialize_candidate_runtime_closure()

    assert adversary.calls == 1
    assert destination.stat().st_mode & 0o777 == 0o700


def test_candidate_runtime_confirmation_failure_retains_repopulated_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination, _attest, _qualify = _prepare_candidate_runtime_publication(
        tmp_path, monkeypatch
    )
    adversary = _Renameat2Adversary(repopulate_source=True)
    monkeypatch.setattr(
        materializer.ctypes,
        "CDLL",
        lambda *_args, **_kwargs: _LibcWithRenameAdversary(adversary),
    )
    _fail_destination_confirmation(materializer, monkeypatch, destination)

    with pytest.raises(
        materializer.RuntimeClosureMaterializationError,
        match="published closure identity",
    ):
        materializer.materialize_candidate_runtime_closure()

    assert adversary.calls == 1
    assert destination.exists()
    assert adversary.source_name is not None
    source = destination.parent / os.fsdecode(adversary.source_name)
    assert (source / "repopulated").read_bytes() == b"repopulated"


def test_candidate_runtime_descriptor_close_failure_keeps_completed_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination, _attest, _qualify = _prepare_candidate_runtime_publication(
        tmp_path, monkeypatch
    )
    close = materializer.os.close
    parent = destination.parent.stat()
    parent_identity = (parent.st_dev, parent.st_ino)

    def close_then_fail(descriptor: int) -> None:
        observed = materializer.os.fstat(descriptor)
        close(descriptor)
        if (observed.st_dev, observed.st_ino) == parent_identity:
            raise OSError("read-only parent descriptor close failure")

    monkeypatch.setattr(materializer.os, "close", close_then_fail)

    assert materializer.materialize_candidate_runtime_closure() == destination
    assert destination.exists()


def test_candidate_runtime_publication_preserves_preexisting_competitor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination, _attest, _qualify = _prepare_candidate_runtime_publication(
        tmp_path, monkeypatch
    )
    destination.mkdir(mode=0o700)
    (destination / "competitor").write_bytes(b"competitor")

    with pytest.raises(
        materializer.RuntimeClosureMaterializationError,
        match="already exists",
    ):
        materializer.materialize_candidate_runtime_closure()

    assert (destination / "competitor").read_bytes() == b"competitor"


def test_candidate_runtime_successful_publication_remains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination, _attest, _qualify = _prepare_candidate_runtime_publication(
        tmp_path, monkeypatch
    )

    assert materializer.materialize_candidate_runtime_closure() == destination
    assert destination.stat().st_mode & 0o777 == 0o500


@pytest.mark.parametrize(
    "drift",
    ("base-byte", "base-manifest", "extra-directory", "directory-mode", "file-mode"),
)
def test_candidate_closure_attestation_revalidates_base_and_exact_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drift: str
) -> None:
    root, base_runtime, base_policy, artifact, inputs, artifact_sha256 = (
        _write_candidate_closure_fixture(tmp_path, monkeypatch)
    )
    if drift == "base-byte":
        path = base_runtime / "files/usr/lib/libbase.so"
        path.chmod(0o600)
        path.write_bytes(b"drift")
        path.chmod(0o400)
    elif drift == "base-manifest":
        path = base_runtime / "closure-manifest.json"
        path.chmod(0o600)
        path.write_bytes(b"{}\n")
        path.chmod(0o400)
    elif drift == "extra-directory":
        (root / "files").chmod(0o700)
        extra = root / "files/empty"
        extra.mkdir()
        extra.chmod(0o500)
        (root / "files").chmod(0o500)
    elif drift == "directory-mode":
        (root / "files/usr").chmod(0o700)
    else:
        (root / f"files/engine/wheels/{WHEEL_FILENAME}").chmod(0o500)

    with pytest.raises(materializer.RuntimeClosureMaterializationError):
        materializer._attest_candidate_closure(
            root,
            artifact=artifact,
            artifact_sha256=artifact_sha256,
            inputs=inputs,
            base_runtime=base_runtime,
            base_policy=base_policy,
        )


def test_candidate_selects_one_and_only_one_direct_wheel(tmp_path: Path) -> None:
    selector = getattr(builder, "_candidate_single_wheel", None)
    assert callable(selector), "candidate single-wheel selector is missing"
    wheel = tmp_path / WHEEL_FILENAME
    wheel.write_bytes(b"wheel")
    assert selector(tmp_path) == wheel
    (tmp_path / "foreign.txt").write_text("foreign", encoding="ascii")
    with pytest.raises(builder.VerificationError, match="exactly one wheel"):
        selector(tmp_path)


def test_candidate_never_packages_after_native_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = {"source": {"artifact": {"filename": "source.tar.gz"}}}
    roots = {
        "candidate_build_root": tmp_path / "build-root",
        "candidate_input_root": tmp_path / "input-root",
    }
    actions: list[str] = []
    source_descriptors: list[int] = []

    def fake_extract(_archive, destination, _record):
        destination.mkdir(mode=0o700)
        return "0" * 64

    def fake_run(*, action, **_kwargs):
        actions.append(action)
        if action == "native":
            raise builder.VerificationError("native failed")
        return {"P1_U04_SOURCE_ST_DEV": "1", "P1_U04_SOURCE_ST_INO": "1"}

    monkeypatch.setattr(builder, "_extract_candidate_source", fake_extract)
    monkeypatch.setattr(builder, "_verify_candidate_source_contract", lambda *_args: None)
    monkeypatch.setattr(builder, "_candidate_sandbox_run", fake_run)
    real_open = os.open

    def tracking_open(path, flags, *args, **kwargs):
        descriptor = real_open(path, flags, *args, **kwargs)
        if (
            flags & os.O_PATH
            and isinstance(path, (str, os.PathLike))
            and Path(path).name == "source"
        ):
            source_descriptors.append(descriptor)
        return descriptor

    monkeypatch.setattr(builder.os, "open", tracking_open)

    with pytest.raises(builder.VerificationError, match="native failed"):
        builder._build_candidate_once({}, inputs, roots)

    assert actions == ["venv", "install", "native"]
    assert len(source_descriptors) == 1
    with pytest.raises(OSError) as captured:
        os.fstat(source_descriptors[0])
    assert captured.value.errno == errno.EBADF


def test_candidate_forensic_staging_substitution_before_identity_preserves_competitor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / "forensic-parent"
    parent.mkdir(mode=0o700)
    destination = parent / "evidence"
    stage_suffix = "4" * 16
    stage = parent / f".{destination.name}-{stage_suffix}"
    task_owned_orphan = parent / "task-owned-orphan"
    marker = stage / "competitor-marker"
    before_root: os.stat_result | None = None
    before_marker: os.stat_result | None = None
    before_names: set[str] | None = None

    def substitute_then_fail(candidate_stage: Path) -> None:
        nonlocal before_root, before_marker, before_names
        assert candidate_stage == stage
        candidate_stage.rename(task_owned_orphan)
        candidate_stage.mkdir(mode=0o700)
        marker.write_bytes(b"competitor-owned bytes")
        marker.chmod(0o400)
        candidate_stage.chmod(0o500)
        before_root = candidate_stage.lstat()
        before_marker = marker.lstat()
        before_names = {path.name for path in candidate_stage.iterdir()}
        raise builder.VerificationError("injected forensic seal failure")

    monkeypatch.setattr(builder.secrets, "token_hex", lambda _size: stage_suffix)
    monkeypatch.setattr(builder, "_seal_candidate_tree", substitute_then_fail)
    parent_stat = parent.lstat()

    with pytest.raises(
        builder.VerificationError, match="injected forensic seal failure"
    ):
        builder._retain_candidate_raw_wheel_pair(
            destination,
            (parent_stat.st_dev, parent_stat.st_ino),
            b"first payload",
            b"second payload",
            (
                {"P1_U04_SOURCE_ST_DEV": "1", "P1_U04_SOURCE_ST_INO": "11"},
                {"P1_U04_SOURCE_ST_DEV": "1", "P1_U04_SOURCE_ST_INO": "12"},
            ),
        )

    assert before_root is not None
    assert before_marker is not None
    assert before_names == {marker.name}
    assert stage.exists(), "unsafe cleanup deleted the substituted competitor"
    after_root = stage.lstat()
    after_marker = marker.lstat()
    assert (after_root.st_dev, after_root.st_ino, after_root.st_mode) == (
        before_root.st_dev,
        before_root.st_ino,
        before_root.st_mode,
    )
    assert (after_marker.st_dev, after_marker.st_ino, after_marker.st_mode) == (
        before_marker.st_dev,
        before_marker.st_ino,
        before_marker.st_mode,
    )
    assert {path.name for path in stage.iterdir()} == before_names
    assert marker.read_bytes() == b"competitor-owned bytes"
    assert task_owned_orphan.is_dir()
    assert {path.name for path in task_owned_orphan.iterdir()} == {
        f"first-{WHEEL_FILENAME}",
        f"second-{WHEEL_FILENAME}",
        "forensic-manifest.json",
    }
    assert not destination.exists()


def test_candidate_raw_wheel_diagnostic_is_deterministic_and_bounded() -> None:
    count = builder._CANDIDATE_RAW_WHEEL_DIAGNOSTIC_LIMIT + 3
    first = _diagnostic_wheel(
        [(f"nautilus_trader/module_{index:03}.py", b"first") for index in range(count)]
    )
    second = _diagnostic_wheel(
        [(f"nautilus_trader/module_{index:03}.py", b"second") for index in range(count)]
    )

    diagnostic = _candidate_raw_wheel_diagnostic_for_test(first, second)

    assert diagnostic == _candidate_raw_wheel_diagnostic_for_test(first, second)
    differences = diagnostic["member_differences"]
    assert differences["total"] == count
    assert differences["emitted"] == builder._CANDIDATE_RAW_WHEEL_DIAGNOSTIC_LIMIT
    assert differences["omitted"] == 3
    assert len(differences["sha256"]) == 64
    assert diagnostic["resources"]["retained_detail_count"] == (
        builder._CANDIDATE_RAW_WHEEL_DIAGNOSTIC_LIMIT
    )
    assert (
        diagnostic["member_differences"]["emitted"]
        + diagnostic["ordered_members"]["differences"]["emitted"]
        <= builder._CANDIDATE_RAW_WHEEL_DIAGNOSTIC_LIMIT
    )
    assert len(json.dumps(diagnostic, sort_keys=True, separators=(",", ":"))) < 100_000


def test_candidate_raw_wheel_read_rejects_prefixed_container_before_loading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inner = _diagnostic_wheel([("nautilus_trader/module.py", b"payload")])
    container = b"MZ" + b"self-extracting-prefix" * 64 + inner
    wheel = tmp_path / WHEEL_FILENAME
    wheel.write_bytes(container)
    monkeypatch.setattr(
        builder,
        "_CANDIDATE_RAW_WHEEL_BYTE_LIMIT",
        len(container) - 1,
    )

    with pytest.raises(builder.VerificationError, match="raw wheel.*size"):
        builder._candidate_read_raw_wheel(wheel)


def test_candidate_build_applies_raw_wheel_cap_before_artifact_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = {"source": {"artifact": {"filename": "source.tar.gz"}}}
    roots = {
        "candidate_build_root": tmp_path / "build-root",
        "candidate_input_root": tmp_path / "input-root",
    }
    actions: list[str] = []

    def fake_extract(_archive, destination, _record):
        destination.mkdir(mode=0o700)
        return "0" * 64

    def fake_run(*, physical_stage, action, **_kwargs):
        if action == "package":
            (physical_stage / "dist" / WHEEL_FILENAME).write_bytes(b"oversized")
        return {"P1_U04_SOURCE_ST_DEV": "1", "P1_U04_SOURCE_ST_INO": "1"}

    def reject_raw_wheel(_wheel):
        actions.append("bounded-read")
        raise builder.VerificationError("candidate raw wheel exceeds bounded size")

    monkeypatch.setattr(builder, "_extract_candidate_source", fake_extract)
    monkeypatch.setattr(builder, "_verify_candidate_source_contract", lambda *_args: None)
    monkeypatch.setattr(builder, "_candidate_sandbox_run", fake_run)
    monkeypatch.setattr(builder, "_verify_candidate_native_outputs", lambda _source: {})
    monkeypatch.setattr(builder, "_candidate_read_raw_wheel", reject_raw_wheel)
    monkeypatch.setattr(
        builder,
        "_candidate_artifact_core",
        lambda *_args: actions.append("artifact-core"),
    )

    with pytest.raises(builder.VerificationError, match="raw wheel.*size"):
        builder._build_candidate_once({}, inputs, roots)

    assert actions == ["bounded-read"]


def test_candidate_build_structural_preflight_precedes_every_zipfile_consumer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = {"source": {"artifact": {"filename": "source.tar.gz"}}}
    roots = {
        "candidate_build_root": tmp_path / "build-root",
        "candidate_input_root": tmp_path / "input-root",
    }
    payload = _diagnostic_wheel(
        [("nautilus_trader/module.py", b"candidate payload")]
    )
    central = _nth_zip_record(payload, CENTRAL, 0)
    crc = struct.unpack_from("<L", payload, central + 16)[0]
    payload = _wheel_with_nth_record_field(
        payload, LOCAL, 0, 14, "<L", crc ^ 1
    )
    artifact_calls: list[bool] = []
    zipfile_calls: list[bool] = []
    artifact_core = builder._candidate_artifact_core

    def fake_extract(_archive, destination, _record):
        destination.mkdir(mode=0o700)
        return "0" * 64

    def fake_run(*, physical_stage, action, **_kwargs):
        if action == "package":
            (physical_stage / "dist" / WHEEL_FILENAME).write_bytes(payload)
        return {"P1_U04_SOURCE_ST_DEV": "1", "P1_U04_SOURCE_ST_INO": "1"}

    def observe_artifact_core(*args):
        artifact_calls.append(True)
        return artifact_core(*args)

    def forbid_zipfile(*_args, **_kwargs):
        zipfile_calls.append(True)
        raise AssertionError("ZipFile ran before candidate structural preflight")

    monkeypatch.setattr(builder, "_extract_candidate_source", fake_extract)
    monkeypatch.setattr(builder, "_verify_candidate_source_contract", lambda *_args: None)
    monkeypatch.setattr(builder, "_candidate_sandbox_run", fake_run)
    monkeypatch.setattr(builder, "_verify_candidate_native_outputs", lambda _source: {})
    monkeypatch.setattr(builder, "_candidate_artifact_core", observe_artifact_core)
    monkeypatch.setattr(builder.zipfile, "ZipFile", forbid_zipfile)

    with pytest.raises(builder.VerificationError):
        builder._build_candidate_once({}, inputs, roots)

    assert artifact_calls == []
    assert zipfile_calls == []


def test_candidate_wheel_structural_preflight_rejects_eocd_count_before_zipfile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    count = builder._CANDIDATE_RAW_WHEEL_MEMBER_LIMIT + 1
    payload = _wheel_with_declared_eocd_count(
        _diagnostic_wheel([("nautilus_trader/module.py", b"first")]),
        count,
    )
    zipfile_calls: list[bool] = []

    def forbid_zipfile(*_args, **_kwargs):
        zipfile_calls.append(True)
        raise AssertionError("ZipFile must not run before EOCD count rejection")

    monkeypatch.setattr(
        builder.zipfile,
        "ZipFile",
        forbid_zipfile,
    )

    with pytest.raises(builder.VerificationError):
        builder._candidate_wheel_structural_preflight(payload)

    assert zipfile_calls == []


def test_candidate_wheel_structural_preflight_rejects_forged_low_eocd_count_before_zipfile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(builder, "_CANDIDATE_RAW_WHEEL_MEMBER_LIMIT", 2)
    members = [(f"member-{index}", bytes([index])) for index in range(3)]
    payload = _wheel_with_declared_eocd_count(
        _diagnostic_wheel(members), 1
    )
    zipfile_calls: list[bool] = []

    def forbid_zipfile(*_args, **_kwargs):
        zipfile_calls.append(True)
        raise AssertionError("ZipFile must not run before central count rejection")

    monkeypatch.setattr(
        builder.zipfile,
        "ZipFile",
        forbid_zipfile,
    )

    with pytest.raises(builder.VerificationError):
        builder._candidate_wheel_structural_preflight(payload)

    assert zipfile_calls == []


def test_candidate_wheel_structural_preflight_rejects_central_count_mismatch_before_zipfile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    members = [("first", b"first"), ("second", b"second")]
    payload = _wheel_with_declared_eocd_count(
        _diagnostic_wheel(members), 1
    )
    zipfile_calls: list[bool] = []

    def forbid_zipfile(*_args, **_kwargs):
        zipfile_calls.append(True)
        raise AssertionError("ZipFile must not run before central count rejection")

    monkeypatch.setattr(
        builder.zipfile,
        "ZipFile",
        forbid_zipfile,
    )

    with pytest.raises(builder.VerificationError):
        builder._candidate_wheel_structural_preflight(payload)

    assert zipfile_calls == []


@pytest.mark.parametrize(
    "mutate",
    (
        pytest.param(
            lambda payload: _wheel_with_zip64_trailer(payload, b"PK\x06\x06"),
            id="zip64-eocd",
        ),
        pytest.param(
            lambda payload: _wheel_with_zip64_trailer(payload, b"PK\x06\x07"),
            id="zip64-locator",
        ),
        pytest.param(
            lambda payload: _wheel_with_zip64_extra(payload, b"PK\x01\x02"),
            id="central-zip64-extra",
        ),
        pytest.param(
            lambda payload: _wheel_with_zip64_extra(payload, b"PK\x03\x04"),
            id="local-zip64-extra",
        ),
        pytest.param(
            lambda payload: _wheel_with_record_field(
                payload, b"PK\x01\x02", 20, "<L", 0xFFFFFFFF
            ),
            id="central-compressed-zip64",
        ),
        pytest.param(
            lambda payload: _wheel_with_record_field(
                payload, b"PK\x01\x02", 24, "<L", 0xFFFFFFFF
            ),
            id="central-uncompressed-zip64",
        ),
        pytest.param(
            lambda payload: _wheel_with_record_field(
                payload, b"PK\x01\x02", 42, "<L", 0xFFFFFFFF
            ),
            id="central-local-offset-zip64",
        ),
        pytest.param(
            lambda payload: _wheel_with_record_field(
                payload, b"PK\x01\x02", 34, "<H", 0xFFFF
            ),
            id="central-disk-zip64",
        ),
        pytest.param(
            lambda payload: _wheel_with_record_field(
                payload, b"PK\x01\x02", 34, "<H", 1
            ),
            id="central-disk-nonzero",
        ),
        pytest.param(
            lambda payload: _wheel_with_record_field(
                payload, b"PK\x03\x04", 18, "<L", 0xFFFFFFFF
            ),
            id="local-compressed-zip64",
        ),
        pytest.param(
            lambda payload: _wheel_with_record_field(
                payload, b"PK\x03\x04", 22, "<L", 0xFFFFFFFF
            ),
            id="local-uncompressed-zip64",
        ),
        pytest.param(
            lambda payload: _wheel_with_record_field(
                payload, b"PK\x01\x02", 28, "<H", 0xFFFF
            ),
            id="central-name-out-of-bounds",
        ),
        pytest.param(
            lambda payload: _wheel_with_record_field(
                payload, b"PK\x03\x04", 28, "<H", 0xFFFF
            ),
            id="local-extra-out-of-bounds",
        ),
        pytest.param(
            lambda payload: _wheel_with_nth_record_field(
                payload, LOCAL, 0, 4, "<H", 21
            ),
            id="local-central-needed-version-mismatch",
        ),
        pytest.param(
            lambda payload: _wheel_with_paired_record_field(
                payload, 0, 4, 6, "<H", 45
            ),
            id="unsupported-needed-version",
        ),
        pytest.param(
            lambda payload: _wheel_with_nth_record_field(
                payload, LOCAL, 0, 6, "<H", 0x0800
            ),
            id="local-central-flags-mismatch",
        ),
        pytest.param(
            lambda payload: _wheel_with_paired_record_field(
                payload, 0, 6, 8, "<H", 0x0001
            ),
            id="encrypted-flags",
        ),
        pytest.param(
            _wheel_with_data_descriptor,
            id="data-descriptor-flags-and-bytes",
        ),
        pytest.param(
            lambda payload: _wheel_with_nth_record_field(
                payload, LOCAL, 0, 8, "<H", zipfile.ZIP_DEFLATED
            ),
            id="local-central-compression-method-mismatch",
        ),
        pytest.param(
            lambda payload: _wheel_with_paired_record_field(
                payload, 0, 8, 10, "<H", 99
            ),
            id="unsupported-compression-method",
        ),
        pytest.param(
            lambda payload: _wheel_with_local_record_delta(
                payload, 0, 14, 16, "<L", 1
            ),
            id="local-central-crc-mismatch",
        ),
        pytest.param(
            lambda payload: _wheel_with_local_record_delta(
                payload, 0, 18, 20, "<L", 1
            ),
            id="local-central-compressed-size-mismatch",
        ),
        pytest.param(
            lambda payload: _wheel_with_local_record_delta(
                payload, 0, 22, 24, "<L", 1
            ),
            id="local-central-uncompressed-size-mismatch",
        ),
        pytest.param(
            _wheel_with_data_extending_into_central,
            id="compressed-data-extends-into-central-directory",
        ),
        pytest.param(
            _wheel_with_duplicated_central_record,
            id="duplicate-central-local-reference",
        ),
        pytest.param(
            lambda _payload: _wheel_with_first_region_overlap(
                _diagnostic_wheel(
                    [("first", b"first"), ("second", b"second")]
                )
            ),
            id="distinct-local-regions-overlap",
        ),
        pytest.param(
            lambda payload: _wheel_with_local_padding(
                payload, before_member=0
            ),
            id="prefix-before-first-local-region",
        ),
        pytest.param(
            lambda _payload: _wheel_with_local_padding(
                _diagnostic_wheel(
                    [("first", b"first"), ("second", b"second")]
                ),
                before_member=1,
            ),
            id="gap-between-local-regions",
        ),
    ),
)
def test_candidate_wheel_structural_preflight_rejects_unsupported_or_inconsistent_structure(
    monkeypatch: pytest.MonkeyPatch,
    mutate: Callable[[bytes], bytes],
) -> None:
    payload = mutate(
        _diagnostic_wheel([("nautilus_trader/module.py", b"payload")])
    )
    zipfile_calls: list[bool] = []

    def forbid_zipfile(*_args, **_kwargs):
        zipfile_calls.append(True)
        raise AssertionError("ZipFile must not run before structural rejection")

    monkeypatch.setattr(
        builder.zipfile,
        "ZipFile",
        forbid_zipfile,
    )

    with pytest.raises(builder.VerificationError):
        _candidate_structural_preflight_for_test(payload)

    assert zipfile_calls == []


def test_candidate_wheel_structural_preflight_rejects_duplicate_local_reference_without_rescanning_extra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _diagnostic_wheel(
        [("nautilus_trader/module.py", b"payload")]
    )
    payload = _wheel_with_extra_field(
        payload, LOCAL, struct.pack("<HH", 0xCAFE, 0)
    )
    payload = _wheel_with_duplicated_central_record(payload)
    eocd = payload.rfind(EOCD)
    central_offset = struct.unpack_from("<L", payload, eocd + 16)[0]
    extra_walks: list[int] = []
    walk_extra = builder._candidate_zip_extra_fields

    def count_extra(payload: bytes, start: int, length: int) -> None:
        if start < central_offset:
            extra_walks.append(start)
        walk_extra(payload, start, length)

    monkeypatch.setattr(builder, "_candidate_zip_extra_fields", count_extra)

    with pytest.raises(builder.VerificationError):
        _candidate_structural_preflight_for_test(payload)

    assert len(extra_walks) <= 1


def test_candidate_wheel_structural_preflight_rejects_malformed_archive_before_zipfile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"malformed candidate wheel"
    zipfile_calls: list[bool] = []

    def forbid_zipfile(*_args, **_kwargs):
        zipfile_calls.append(True)
        raise AssertionError("ZipFile must not run before malformed rejection")

    monkeypatch.setattr(builder.zipfile, "ZipFile", forbid_zipfile)

    with pytest.raises(builder.VerificationError):
        builder._candidate_wheel_structural_preflight(payload)

    assert zipfile_calls == []


def test_candidate_raw_wheel_diagnostic_truncates_serialized_names_and_tracks_duplicates() -> None:
    long_ascii = "nautilus_trader/" + "a" * 700 + ".py"
    long_unicode = "nautilus_trader/" + "é" * 300 + ".py"
    with pytest.warns(UserWarning, match="Duplicate name"):
        first = _diagnostic_wheel(
            [
                (long_ascii, b"first"),
                (long_unicode, b"first"),
                ("duplicate.py", b"a"),
                ("duplicate.py", b"b"),
            ]
        )
    with pytest.warns(UserWarning, match="Duplicate name"):
        second = _diagnostic_wheel(
            [
                (long_ascii, b"second"),
                (long_unicode, b"second"),
                ("duplicate.py", b"a"),
                ("duplicate.py", b"changed"),
            ]
        )

    diagnostic = _candidate_raw_wheel_diagnostic_for_test(first, second)

    assert diagnostic == _candidate_raw_wheel_diagnostic_for_test(first, second)
    assert diagnostic["member_differences"]["total"] == 3
    assert diagnostic["member_records"]["first"]["count"] == 4
    assert diagnostic["member_records"]["second"]["count"] == 4
    assert len(diagnostic["member_records"]["first"]["sha256"]) == 64
    assert {
        entry["occurrence"] for entry in diagnostic["member_differences"]["entries"]
        if entry["name"].startswith("duplicate.py")
    } == {1}
    for entry in diagnostic["member_differences"]["entries"]:
        serialized_name = json.dumps(entry["name"], ensure_ascii=True).encode("ascii")
        assert len(serialized_name) <= builder._CANDIDATE_RAW_WHEEL_NAME_BYTE_LIMIT
        assert len(entry["name_utf8_sha256"]) == 64


def test_candidate_attestor_routes_engine_identity_only_to_the_engine_wheel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _engine, inputs, _roots, _engine_path, _inputs_path = (
        _portable_candidate_policies(tmp_path)
    )
    click = next(
        record for record in inputs["runtime_wheels"] if record["package"] == "click"
    )
    wheel_root = tmp_path / "closure/files/engine/wheels"
    wheel_root.mkdir(parents=True)
    runtime_wheel = wheel_root / click["filename"]
    with zipfile.ZipFile(runtime_wheel, "w") as archive:
        archive.writestr("click/__init__.py", b"__version__ = 'fixture'\n")
    click["size"] = runtime_wheel.stat().st_size
    click["sha256"] = hashlib.sha256(runtime_wheel.read_bytes()).hexdigest()
    engine_wheel = wheel_root / WHEEL_FILENAME
    _write_candidate_wheel(engine_wheel, inputs)
    loader = tmp_path / "closure/files/lib64/ld-linux-x86-64.so.2"
    loader.parent.mkdir(parents=True)
    loader_raw = _elf64(soname="ld-linux-x86-64.so.2")
    loader.write_bytes(loader_raw)

    verified: list[str] = []
    real_verify = builder._verify_candidate_wheel_archive

    def verify_engine(path: Path) -> None:
        verified.append(path.name)
        real_verify(path)

    monkeypatch.setattr(builder, "_verify_candidate_wheel_archive", verify_engine)
    records, _loader = materializer._candidate_native_inventory(
        builder,
        tmp_path / "closure",
        [
            (runtime_wheel, f"/engine/wheels/{runtime_wheel.name}"),
            (engine_wheel, f"/engine/wheels/{engine_wheel.name}"),
        ],
    )

    assert records[0]["path"] == "/lib64/ld-linux-x86-64.so.2"
    assert verified == [WHEEL_FILENAME]


def _native_metadata_builder(metadata: dict[str, dict[str, object]]):
    class NativeMetadataBuilder:
        _CANDIDATE_WHEEL_FILENAME = WHEEL_FILENAME

        @staticmethod
        def _verify_candidate_wheel_archive(_wheel: Path) -> None:
            return None

        @staticmethod
        def _elf_metadata(_payload: bytes, label: str) -> dict[str, object]:
            return metadata[label]

    return NativeMetadataBuilder()


def _native_metadata(
    *,
    needed: list[str] | None = None,
    rpath: str | None = None,
    runpath: str | None = None,
    soname: str | None = None,
    interpreter: str | None = None,
) -> dict[str, object]:
    return {
        "abi_class": "ELF64",
        "abi_data": "little-endian",
        "machine": "EM_X86_64",
        "interpreter": interpreter,
        "needed": [] if needed is None else needed,
        "rpath": rpath,
        "runpath": runpath,
        "soname": soname,
    }


def _write_native_inventory_fixture(
    tmp_path: Path,
    members: dict[str, bytes],
    metadata: dict[str, dict[str, object]],
) -> tuple[object, Path, Path, str]:
    wheel = tmp_path / WHEEL_FILENAME
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, raw in members.items():
            archive.writestr(name, raw)
    staging = tmp_path / "staging"
    loader = staging / "files/lib64/ld-linux-x86-64.so.2"
    loader.parent.mkdir(parents=True)
    loader.write_bytes(b"\x7fELFloader")
    metadata["/lib64/ld-linux-x86-64.so.2"] = _native_metadata(
        soname="ld-linux-x86-64.so.2"
    )
    target = f"/engine/wheels/{WHEEL_FILENAME}"
    return _native_metadata_builder(metadata), staging, wheel, target


@pytest.mark.parametrize(
    ("payload", "message"),
    (
        (_elf64(elf_type=1), "type"),
        (_elf64(dynamic=False), "dynamic"),
        (_elf64(gnu_relro=False), "GNU_RELRO"),
        (_elf64(gnu_relro_size=0), "GNU_RELRO"),
        (_elf64(gnu_relro_vaddr=0x800000), "GNU_RELRO"),
        (_elf64(bind_now=False), "BIND_NOW"),
        (
            _elf64(bind_now=False, dynamic_flags=0x4, dynamic_flags_1=0x2),
            "BIND_NOW",
        ),
        (_elf64(gnu_stack_flags=None), "GNU_STACK"),
        (_elf64(gnu_stack_flags=7), "executable GNU_STACK"),
        (_elf64(needed=("libdep.so",), strsz=1), "string"),
        (_elf64(duplicate_soname=True), "singleton"),
        (
            _elf64(
                interpreter="/lib64/ld-linux-x86-64.so.2",
                terminate_interpreter=False,
            ),
            "interpreter",
        ),
        (_elf64(terminate_dynamic=False), "DT_NULL"),
    ),
)
def test_candidate_elf_parser_rejects_malformed_structures(
    payload: bytes, message: str
) -> None:
    with pytest.raises(builder.VerificationError, match=message):
        builder._elf_metadata(payload, "fixture.so")


def test_candidate_elf_parser_preserves_dt_needed_order_and_validates_soname() -> None:
    metadata = builder._elf_metadata(
        _elf64(needed=("libz.so", "liba.so"), soname="ordered.so"),
        "ordered.so",
    )

    assert metadata["needed"] == ["libz.so", "liba.so"]
    assert metadata["soname"] == "ordered.so"
    assert metadata["gnu_relro"] is True
    assert metadata["gnu_stack_executable"] is False

    with pytest.raises(builder.VerificationError, match="SONAME"):
        builder._elf_metadata(_elf64(soname="../foreign.so"), "foreign.so")


@pytest.mark.parametrize(
    "payload",
    (
        _elf64(),
        _elf64(bind_now=False, dynamic_flags=0x8),
        _elf64(bind_now=False, dynamic_flags_1=0x1),
    ),
)
def test_candidate_elf_parser_accepts_each_immediate_binding_form(
    payload: bytes,
) -> None:
    assert builder._elf_metadata(payload, "fixture.so")["gnu_relro"] is True


@pytest.mark.parametrize("identical", (False, True))
def test_candidate_native_soname_aliases_require_identical_payloads(
    tmp_path: Path, identical: bool
) -> None:
    target = f"/engine/wheels/{WHEEL_FILENAME}"
    metadata = {
        f"{target}!a/libfirst.so": _native_metadata(soname="libalias.so"),
        f"{target}!b/libsecond.so": _native_metadata(soname="libalias.so"),
    }
    first = b"same"
    second = first if identical else b"different"
    fake_builder, staging, wheel, target = _write_native_inventory_fixture(
        tmp_path,
        {"a/libfirst.so": first, "b/libsecond.so": second},
        metadata,
    )

    if identical:
        records, _loader = materializer._candidate_native_inventory(
            fake_builder, staging, [(wheel, target)]
        )
        assert len(records) == 3
    else:
        with pytest.raises(
            materializer.RuntimeClosureMaterializationError,
            match="alias collision",
        ):
            materializer._candidate_native_inventory(
                fake_builder, staging, [(wheel, target)]
            )


def test_candidate_native_allows_divergent_basename_only_in_isolated_roots(
    tmp_path: Path,
) -> None:
    target = f"/engine/wheels/{WHEEL_FILENAME}"
    basename = "lib.cpython-312-x86_64-linux-gnu.so"
    metadata = {
        f"{target}!package_a/{basename}": _native_metadata(),
        f"{target}!package_b/{basename}": _native_metadata(),
    }
    fake_builder, staging, wheel, target = _write_native_inventory_fixture(
        tmp_path,
        {f"package_a/{basename}": b"first", f"package_b/{basename}": b"second"},
        metadata,
    )

    records, _loader = materializer._candidate_native_inventory(
        fake_builder, staging, [(wheel, target)]
    )

    assert len(records) == 3


def test_candidate_native_loaded_object_reuse_precedes_later_search_hits(
    tmp_path: Path,
) -> None:
    target = f"/engine/wheels/{WHEEL_FILENAME}"
    metadata = {
        f"{target}!app/main.so": _native_metadata(
            needed=["libfirst.so", "libalias.so"], runpath="$ORIGIN/../libs"
        ),
        f"{target}!libs/libfirst.so": _native_metadata(soname="libalias.so"),
        f"{target}!libs/libalias.so": _native_metadata(soname="libalias.so"),
    }
    fake_builder, staging, wheel, target = _write_native_inventory_fixture(
        tmp_path,
        {
            "app/main.so": b"main",
            "libs/libfirst.so": b"identical-alias",
            "libs/libalias.so": b"identical-alias",
        },
        metadata,
    )

    records, _loader = materializer._candidate_native_inventory(
        fake_builder, staging, [(wheel, target)]
    )

    main = next(record for record in records if str(record["path"]).endswith("!app/main.so"))
    assert main["needed_resolution"] == [
        {"name": "libfirst.so", "resolved_path": f"{target}!libs/libfirst.so"},
        {"name": "libalias.so", "resolved_path": f"{target}!libs/libfirst.so"},
    ]


def test_candidate_native_lib64_is_interpreter_preload_not_search_root(
    tmp_path: Path,
) -> None:
    target = f"/engine/wheels/{WHEEL_FILENAME}"
    metadata = {
        f"{target}!app/main.so": _native_metadata(
            needed=["libprivate.so", "ld-linux-x86-64.so.2"],
            interpreter="/lib64/ld-linux-x86-64.so.2",
        ),
        "/lib64/libprivate.so": _native_metadata(soname="libprivate.so"),
    }
    fake_builder, staging, wheel, target = _write_native_inventory_fixture(
        tmp_path, {"app/main.so": b"main"}, metadata
    )
    private = staging / "files/lib64/libprivate.so"
    private.write_bytes(b"private")

    with pytest.raises(
        materializer.RuntimeClosureMaterializationError,
        match="DT_NEEDED closure is incomplete.*libprivate",
    ):
        materializer._candidate_native_inventory(
            fake_builder, staging, [(wheel, target)]
        )

    metadata[f"{target}!app/main.so"] = _native_metadata(
        needed=["ld-linux-x86-64.so.2"],
        interpreter="/lib64/ld-linux-x86-64.so.2",
    )
    records, _loader = materializer._candidate_native_inventory(
        fake_builder, staging, [(wheel, target)]
    )
    main = next(record for record in records if str(record["path"]).endswith("!app/main.so"))
    assert main["needed_resolution"] == [
        {
            "name": "ld-linux-x86-64.so.2",
            "resolved_path": "/lib64/ld-linux-x86-64.so.2",
        }
    ]


def test_candidate_native_resolution_uses_runpath_precedence_not_global_aliases(
    tmp_path: Path,
) -> None:
    target = f"/engine/wheels/{WHEEL_FILENAME}"
    metadata = {
        f"{target}!app/main.so": _native_metadata(
            needed=["libdep.so"],
            rpath="$ORIGIN/../decoy",
            runpath="$ORIGIN/../libs",
        ),
        f"{target}!libs/libdep.so": _native_metadata(soname="libdep.so"),
        f"{target}!decoy/libdep.so": _native_metadata(soname="libdep.so"),
    }
    fake_builder, staging, wheel, target = _write_native_inventory_fixture(
        tmp_path,
        {
            "app/main.so": b"main",
            "libs/libdep.so": b"identical",
            "decoy/libdep.so": b"identical",
        },
        metadata,
    )

    records, _loader = materializer._candidate_native_inventory(
        fake_builder, staging, [(wheel, target)]
    )

    main = next(
        record
        for record in records
        if str(record["path"]).endswith("!app/main.so")
    )
    assert main["needed_resolution"] == [
        {"name": "libdep.so", "resolved_path": f"{target}!libs/libdep.so"}
    ]


def test_candidate_native_resolution_rejects_globally_present_but_unreachable_dependency(
    tmp_path: Path,
) -> None:
    target = f"/engine/wheels/{WHEEL_FILENAME}"
    metadata = {
        f"{target}!app/main.so": _native_metadata(needed=["libdep.so"]),
        f"{target}!private/libdep.so": _native_metadata(soname="libdep.so"),
    }
    fake_builder, staging, wheel, target = _write_native_inventory_fixture(
        tmp_path,
        {"app/main.so": b"main", "private/libdep.so": b"unreachable"},
        metadata,
    )

    with pytest.raises(
        materializer.RuntimeClosureMaterializationError,
        match="DT_NEEDED closure is incomplete",
    ):
        materializer._candidate_native_inventory(
            fake_builder, staging, [(wheel, target)]
        )


def test_candidate_native_inventory_rejects_foreign_pt_interp(tmp_path: Path) -> None:
    target = f"/engine/wheels/{WHEEL_FILENAME}"
    metadata = {
        f"{target}!app/main.so": _native_metadata(interpreter="/tmp/foreign-loader"),
    }
    fake_builder, staging, wheel, target = _write_native_inventory_fixture(
        tmp_path, {"app/main.so": b"main"}, metadata
    )

    with pytest.raises(
        materializer.RuntimeClosureMaterializationError, match="PT_INTERP"
    ):
        materializer._candidate_native_inventory(
            fake_builder, staging, [(wheel, target)]
        )


def test_candidate_attestation_cli_is_exclusive_and_accepts_no_paths(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit):
        materializer._parser().parse_args(
            ["--materialize-candidate", "--attest-candidate"]
        )
    capsys.readouterr()
    with pytest.raises(SystemExit, match="accepts no caller-supplied authority"):
        materializer.main(
            ["--attest-candidate", "--destination", "/tmp/foreign"]
        )


@pytest.mark.parametrize(
    ("module_name", "function_name", "arguments", "result_expression"),
    (
        (
            "scripts.build_nautilus_engine",
            "build_candidate_a",
            [
                "--build-candidate-a",
                "--offline",
                "--authority-receipt",
                "/tmp/x4-receipt.json",
                "--authority-receipt-sha256",
                "0" * 64,
            ],
            "{}",
        ),
        (
            "scripts.materialize_nautilus_runtime_closure",
            "materialize_candidate_runtime_closure",
            ["--materialize-candidate"],
            "marker.parent / 'candidate-runtime'",
        ),
    ),
    ids=("build", "materialize"),
)
def test_mutating_candidate_cli_closed_stdout_after_publication_still_succeeds(
    tmp_path: Path,
    module_name: str,
    function_name: str,
    arguments: list[str],
    result_expression: str,
) -> None:
    marker = tmp_path / "published"
    program = f"""
import importlib
from pathlib import Path
import sys

tool = importlib.import_module({module_name!r})
marker = Path({str(marker)!r})

def publish(**_kwargs):
    marker.write_text('published', encoding='ascii')
    sys.stdout.close()
    return {result_expression}

setattr(tool, {function_name!r}, publish)
result = tool.main({arguments!r})
raise SystemExit(0 if result is None else result)
"""
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"

    result = subprocess.run(
        [sys.executable, "-c", program],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert marker.read_text(encoding="ascii") == "published"
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""


def test_direct_candidate_attestation_disables_bytecode_before_local_imports(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    script = repository / "scripts/materialize_nautilus_runtime_closure.py"
    script.parent.mkdir(parents=True)
    script.write_bytes(materializer.__file__ and Path(materializer.__file__).read_bytes())
    job_worker = repository / "services/job_worker"
    job_worker.mkdir(parents=True)
    for path in (
        repository / "services/__init__.py",
        job_worker / "__init__.py",
    ):
        path.write_text("", encoding="ascii")
    (job_worker / "engine_spawn_interface.py").write_text(
        "class EngineSpawnError(Exception):\n    pass\n", encoding="ascii"
    )
    (job_worker / "nautilus_closure.py").write_text(
        "class NautilusClosureConfig:\n    pass\n"
        "def attest_nautilus_backtest_closure(*args, **kwargs):\n    return None\n",
        encoding="ascii",
    )
    environment = dict(os.environ)
    environment.pop("PYTHONDONTWRITEBYTECODE", None)
    environment.pop("PYTHONPYCACHEPREFIX", None)

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--attest-candidate",
            "--destination",
            "/tmp/foreign",
        ],
        cwd=repository,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode != 0
    assert "candidate attestation accepts no caller-supplied authority" in result.stderr
    assert list(repository.rglob("*.pyc")) == []


def test_direct_candidate_attestation_never_mutates_host_filesystem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _engine, _inputs, _roots, _document = _write_candidate_artifact(
        tmp_path, monkeypatch
    )
    sandbox = tmp_path / "sandbox-fixture.py"
    sandbox.write_text("raise SystemExit(0)\n", encoding="ascii")
    candidate_runtime = tmp_path / "candidate-runtime"
    candidate_runtime.mkdir()
    closure_manifest = candidate_runtime / "closure-manifest.json"
    closure_manifest.write_bytes(b"{}\n")
    closure_manifest.chmod(0o400)
    candidate_runtime.chmod(0o500)
    program = f"""
import hashlib
import os
from pathlib import Path
import subprocess
import sys

WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
MUTATION_EVENTS = {{
    'os.chmod',
    'os.link',
    'os.mkdir',
    'os.remove',
    'os.rename',
    'os.rmdir',
    'os.symlink',
    'os.truncate',
    'os.utime',
}}
SAFE_SANDBOX = ({sys.executable!r}, {str(sandbox)!r})

def reject_host_mutation(event, args):
    mutated = event in MUTATION_EVENTS
    if event == 'open':
        mode = args[1]
        flags = args[2]
        mutated = bool(flags & WRITE_FLAGS) or (
            isinstance(mode, str) and any(value in mode for value in 'wax+')
        )
    elif event == 'subprocess.Popen':
        mutated = tuple(args[1]) != SAFE_SANDBOX
    if mutated:
        os.write(2, f'MUTATION:{{event}}:{{args[0] if args else ""}}\\n'.encode('utf-8'))
        raise RuntimeError('public candidate attestation mutated the host filesystem')

sys.addaudithook(reject_host_mutation)

import scripts.build_nautilus_engine as builder
import scripts.materialize_nautilus_runtime_closure as materializer

builder._CANDIDATE_ENGINE_POLICY = Path({str(tmp_path / "portable-policy/engine-build-policy.json")!r})
builder._CANDIDATE_TOOLCHAIN_INPUTS = Path({str(tmp_path / "portable-policy/toolchain-inputs.json")!r})
engine = builder._candidate_json(builder._CANDIDATE_ENGINE_POLICY)
inputs = builder._candidate_json(builder._CANDIDATE_TOOLCHAIN_INPUTS)
roots = builder._candidate_roots(engine)
roots['candidate_build_root'] = Path({str(tmp_path)!r})
roots['candidate_runtime_root'] = Path({str(candidate_runtime)!r})
roots['rollback_root'] = Path({str(tmp_path / "rollback")!r})
qualification_sha256 = hashlib.sha256(
    materializer._CANDIDATE_IMPORT_SCRIPT.encode('ascii')
).hexdigest()

def attest_closure(_root, *, artifact, **_kwargs):
    return {{
        'schema_version': 7,
        'engine': artifact['engine'],
        'source': artifact['source'],
        'python': artifact['python'],
        'policy_hashes': artifact['policy_hashes'],
        'toolchain': artifact['toolchain'],
        'network': artifact['network'],
        'runtime_wheels': artifact['runtime_wheels'],
        'base_runtime': {{}},
        'file_inventory_sha256': '0' * 64,
        'native_inventory_sha256': '1' * 64,
        'qualification': {{
            'script_sha256': qualification_sha256,
            'status': 'PASS',
        }},
    }}

def qualify(_root):
    subprocess.run(SAFE_SANDBOX, env={{}}, capture_output=True, check=True, timeout=30)
    return qualification_sha256

builder._verify_candidate_authority = lambda: (engine, inputs)
builder._candidate_git_identity = lambda: {{'head': 'a' * 40, 'tree': 'b' * 40}}
builder._candidate_external_identities = lambda *_args: {{'fixture': {{'sha256': 'f' * 64}}}}
builder._candidate_roots = lambda _engine: roots
materializer._candidate_builder_tool = lambda: builder
materializer._attest_candidate_closure = attest_closure
materializer._qualify_candidate_import = qualify
materializer.main(['--attest-candidate'])
"""
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment.pop("PYTHONPYCACHEPREFIX", None)

    result = subprocess.run(
        [sys.executable, "-c", program],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert json.loads(result.stdout)["source"]["verified_extracted_tree_sha256"]


def test_candidate_attestation_cli_emits_canonical_receipt_without_materializing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    receipt = {
        "schema_version": 1,
        "manifest_kind": "NAUTILUS_V1_231_CANDIDATE_CLOSURE_ATTESTATION",
        "qualification": {"sha256": "b" * 64, "status": "PASS"},
    }
    monkeypatch.setattr(
        materializer,
        "attest_candidate_runtime_closure",
        lambda: receipt,
        raising=False,
    )

    def fail_materialization() -> None:
        raise AssertionError("attestation reached the materializer")

    monkeypatch.setattr(
        materializer, "materialize_candidate_runtime_closure", fail_materialization
    )

    materializer.main(["--attest-candidate"])

    assert capsys.readouterr().out == json.dumps(
        receipt,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ) + "\n"


def test_candidate_attestation_binds_authority_and_rejects_published_byte_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attest = getattr(materializer, "attest_candidate_runtime_closure", None)
    assert callable(attest), "candidate read-only attestation entrypoint is missing"
    root, base_runtime, base_policy, artifact, inputs, artifact_sha256 = (
        _write_candidate_closure_fixture(tmp_path, monkeypatch)
    )
    manifest_raw = (root / "closure-manifest.json").read_bytes()
    manifest = json.loads(manifest_raw)
    qualification_sha256 = hashlib.sha256(
        materializer._CANDIDATE_IMPORT_SCRIPT.encode("ascii")
    ).hexdigest()

    class CandidateBuilder:
        _CANDIDATE_WHEEL_FILENAME = WHEEL_FILENAME

        @staticmethod
        def _verify_candidate_authority():
            return {}, inputs

        @staticmethod
        def _candidate_roots(_engine):
            return {
                "candidate_build_root": tmp_path / "build",
                "candidate_runtime_root": root,
                "rollback_root": base_runtime.parent,
            }

        @staticmethod
        def _candidate_json(path: Path):
            return json.loads(path.read_text(encoding="ascii"))

        @staticmethod
        def _verify_candidate_wheel_archive(_path: Path) -> None:
            return None

    monkeypatch.setattr(materializer, "_candidate_builder_tool", CandidateBuilder)
    monkeypatch.setattr(
        materializer,
        "_validate_candidate_artifact",
        lambda *_args: (tmp_path / WHEEL_FILENAME, artifact, b"artifact"),
    )
    monkeypatch.setattr(materializer, "_load_policy", lambda _path: base_policy)
    monkeypatch.setattr(
        materializer, "_qualify_candidate_import", lambda _root: qualification_sha256
    )

    assert attest() == {
        "schema_version": 1,
        "manifest_kind": "NAUTILUS_V1_231_CANDIDATE_CLOSURE_ATTESTATION",
        "activation_status": "CANDIDATE_ONLY_NOT_ACTIVATED",
        "closure_schema_version": 7,
        "engine": artifact["engine"],
        "source": artifact["source"],
        "python": artifact["python"],
        "policy_hashes": artifact["policy_hashes"],
        "toolchain": artifact["toolchain"],
        "network": artifact["network"],
        "runtime_wheels": artifact["runtime_wheels"],
        "base_runtime": manifest["base_runtime"],
        "artifact_manifest_sha256": artifact_sha256,
        "closure_manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
        "file_inventory_sha256": manifest["file_inventory_sha256"],
        "native_inventory_sha256": manifest["native_inventory_sha256"],
        "qualification": manifest["qualification"],
    }

    published_file = root / "files/usr/lib/libbase.so"
    published_file.chmod(0o600)
    published_file.write_bytes(b"drift")
    published_file.chmod(0o400)
    with pytest.raises(materializer.RuntimeClosureMaterializationError):
        attest()
    assert published_file.read_bytes() == b"drift"
