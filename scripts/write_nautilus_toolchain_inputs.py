#!/usr/bin/env python3
"""Verify sealed Nautilus 1.231 inputs and write their deterministic manifest."""

from __future__ import annotations

import argparse
import ast
import base64
import csv
from email.parser import BytesParser
from email.policy import compat32
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import sys
import tarfile
import tomllib
from typing import Any
import unicodedata
import zipfile


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
CANDIDATE = ROOT / "engines/nautilus/candidates/v1.231"
ENGINE_POLICY = CANDIDATE / "engine-build-policy.json"
INPUT_POLICY = CANDIDATE / "input-cache-policy.json"
WHEEL_POLICY = CANDIDATE / "wheel-cache-policy.json"
CARGO_POLICY = CANDIDATE / "cargo-registry-policy.json"
U02_POLICY = ROOT / "engines/nautilus/v1.231-provenance-policy.json"
_SHA256 = re.compile(r"[0-9a-f]{64}")
_REQUIREMENT_TOKEN = r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?"
_REQUIREMENT = re.compile(
    rf"^({_REQUIREMENT_TOKEN})(?:\[{_REQUIREMENT_TOKEN}(?:\s*,\s*{_REQUIREMENT_TOKEN})*\])?([^;]*)(?:;(.*))?$"
)
_MAX_ARCHIVE_MEMBERS = 20_000
_MAX_MEMBER_BYTES = 512 * 1024 * 1024
_NATIVE_SNAPSHOT_PAYLOAD_SHA256 = (
    "dca405a50542615a751b22e16b39f52fcfb637a4acb85f4ec945f96c0c0bcd57"
)
_NATIVE_SNAPSHOT_RECEIPT_SHA256 = (
    "3e9aad0b2a3b467ac1ab2b50ea58ab59ec0474d68e31f31f80023ee58d90f728"
)
_MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
_CRATES_IO_SOURCE = "registry+https://github.com/rust-lang/crates.io-index"
_TARGET = "x86_64-unknown-linux-gnu"
_EXTERNAL_ROOTS = {
    "candidate_build_root": "/home/thenam176/.cache/trading-agent/nautilus-v1.231-build-work",
    "candidate_cargo_home_root": "/home/thenam176/.cache/trading-agent/nautilus-v1.231-cargo-home",
    "candidate_forensic_root": "/home/thenam176/.cache/trading-agent/nautilus-v1.231-reproducibility-evidence",
    "candidate_input_root": "/home/thenam176/.cache/p1-u03-toolchain-policy-20260823/candidate-inputs",
    "candidate_llvm_toolchain_root": "/home/thenam176/.cache/trading-agent/nautilus-v1.231-llvm-toolchain",
    "candidate_runtime_root": "/home/thenam176/.cache/trading-agent/nautilus-v1.231-runtime",
    "candidate_rust_toolchain_root": "/home/thenam176/.cache/trading-agent/nautilus-v1.231-rust-toolchain",
    "candidate_toolchain_root": "/home/thenam176/.cache/trading-agent/nautilus-v1.231-toolchain",
    "candidate_vendor_root": "/home/thenam176/.cache/trading-agent/nautilus-v1.231-vendor",
    "rollback_root": "/home/thenam176/.cache/trading-agent/nautilus",
}
_ROOT_ACCESS = {
    "candidate_build_root": "WRITABLE_PARENT_FOR_ONE_FRESH_STAGING_CHILD",
    "candidate_cargo_home_root": "SEALED_READ_ONLY",
    "candidate_forensic_root": "ABSENT_THEN_SEALED_READ_ONLY_FORENSIC_EVIDENCE",
    "candidate_input_root": "SEALED_READ_ONLY",
    "candidate_llvm_toolchain_root": "SEALED_READ_ONLY",
    "candidate_runtime_root": "ABSENT_THEN_SEALED_READ_ONLY",
    "candidate_rust_toolchain_root": "SEALED_READ_ONLY",
    "candidate_toolchain_root": "SEALED_READ_ONLY_COMMAND_ROUTER",
    "candidate_vendor_root": "SEALED_READ_ONLY",
    "rollback_root": "EXISTING_SEALED_READ_ONLY",
}
_CANDIDATE_TOOLCHAIN_ROOT = _EXTERNAL_ROOTS["candidate_toolchain_root"]
_ROUTER_BIN = f"{_CANDIDATE_TOOLCHAIN_ROOT}/bin"
_INITIAL_RUSTFLAGS = (
    "-Dwarnings -Aclippy::drop_non_drop "
    "-C link-arg=-fuse-ld=lld "
    "-C link-arg=-Wl,--gc-sections "
    "-C link-arg=-Wl,--as-needed "
    "-C link-arg=-Wl,-z,norelro "
    "-C relocation-model=pic"
)
_RUSTFLAGS_RELEASE_APPEND = " -C link-arg=-s"
_STATIC_BUILD_ENVIRONMENT = {
    "ANNOTATION_MODE": "",
    "AR": "/usr/bin/ar",
    "AWS_LC_SYS_CMAKE_BUILDER": "0",
    "AWS_LC_SYS_USE_SYSTEM": "0",
    "BUILD_MODE": "release",
    "CARGO_BUILD_JOBS": "1",
    "CARGO_BUILD_TARGET": _TARGET,
    "CARGO_CACHE_RUSTC_INFO": "0",
    "CARGO_HOME": _EXTERNAL_ROOTS["candidate_cargo_home_root"],
    "CARGO_INCREMENTAL": "0",
    "CARGO_NET_OFFLINE": "true",
    "CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER": "clang",
    "CARGO_TERM_COLOR": "never",
    "COPY_TO_SOURCE": "true",
    "DRY_RUN": "",
    "FORCE_STRIP": "false",
    "HIGH_PRECISION": "true",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "PARALLEL_BUILD": "false",
    "PATH": _ROUTER_BIN,
    "PIP_DISABLE_PIP_VERSION_CHECK": "1",
    "PIP_FIND_LINKS": _EXTERNAL_ROOTS["candidate_input_root"] + "/wheels",
    "PIP_NO_CACHE_DIR": "1",
    "PIP_NO_INDEX": "1",
    "PIP_ONLY_BINARY": ":all:",
    "PROFILE_MODE": "",
    "PYO3_ONLY": "",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONHASHSEED": "0",
    "PYTHONNOUSERSITE": "1",
    "RUSTC": "rustc",
    "RUSTFLAGS": _INITIAL_RUSTFLAGS,
    "RUSTUP_TOOLCHAIN": "stable",
    "SKIP_RUST_DYLIB_COPY": "",
    "SOURCE_DATE_EPOCH": "0",
    "TZ": "UTC",
}
_DERIVED_PATH_SUFFIXES = {
    "artifacts": "/artifacts",
    "cargo_target": "/cargo-target",
    "dist": "/dist",
    "home": "/home",
    "source": "/source",
    "staging": "",
    "tmp": "/tmp",
    "venv": "/venv",
}
_DERIVED_ENVIRONMENT = {
    "CARGO_TARGET_DIR": "/cargo-target",
    "HOME": "/home",
    "P1_U04_ARTIFACTS_ROOT": "/artifacts",
    "P1_U04_DIST_ROOT": "/dist",
    "P1_U04_SOURCE_ROOT": "/source",
    "P1_U04_STAGING_ROOT": "",
    "P1_U04_VENV_ROOT": "/venv",
    "PWD": "/source",
    "PYO3_PYTHON": "/venv/bin/python",
    "TEMP": "/tmp",
    "TMP": "/tmp",
    "TMPDIR": "/tmp",
    "VIRTUAL_ENV": "/venv",
}
_BUILDER_DERIVED_ENVIRONMENT = {
    "P1_U04_SOURCE_ST_DEV": (
        "VERIFIED_SOURCE_FD_FSTAT_ST_DEV_CANONICAL_BASE10_POSITIVE"
    ),
    "P1_U04_SOURCE_ST_INO": (
        "VERIFIED_SOURCE_FD_FSTAT_ST_INO_CANONICAL_BASE10_POSITIVE"
    ),
}
_SOURCE_IDENTITY_HANDOFF = {
    "authority": "U04_BUILDER_INTERNAL_VERIFIED_SOURCE_IDENTITY_V1",
    "caller_supplied_identity": "PROHIBITED",
    "derivation_order": [
        "VERIFY_EXACT_U02_ARCHIVE",
        "SAFE_EXTRACT_TO_FRESH_STAGE",
        "VERIFY_FULL_EXTRACTED_TREE_SET_TYPE_MODE_SIZE_SHA256",
        "OPEN_STAGE_SOURCE_O_PATH_O_DIRECTORY_O_NOFOLLOW",
        "FSTAT_SOURCE_DIRECTORY",
        "DERIVE_EXACT_BUILD_ENVIRONMENT",
    ],
    "sandbox_mount": {
        "bubblewrap_option": "--bind-fd",
        "mount_count": 1,
        "mount_destination": "EXACT_STAGING_ROOT_ONLY",
        "mount_source": "PREOPENED_VERIFIED_STAGE_ROOT_FD",
        "nested_destinations_at_or_below_source": "PROHIBITED",
        "pass_fds": "EXACT_STAGE_ROOT_FD_ONLY",
        "stage_disposition": "SCRATCH_DISCARD_AFTER_BUILD",
    },
    "source_fd": {
        "open_call": "os.open(stage/source, O_PATH|O_DIRECTORY|O_NOFOLLOW)",
        "stat_call": "os.fstat(source_fd)",
        "source_symlink": "FAIL",
    },
    "threat_model": (
        "COOPERATIVE_HOST; CONCURRENT_SAME_UID_ROOT_PTRACE_KERNEL_MUTATION_"
        "OUT_OF_SCOPE"
    ),
}
_SOURCE_EFFECTIVE_OVERRIDES = {
    "CC": "clang",
    "CXX": "clang++",
    "LDSHARED": "clang -shared",
}
_PROHIBITED_SOURCE_ENVIRONMENT = [
    "CFLAGS",
    "LDFLAGS",
    "LD_LIBRARY_PATH",
    "PYTHONHOME",
    "PYTHON_LIB_DIR",
    "RUSTC_WRAPPER",
]
_STAGE_NAME = re.compile(r"stage-[0-9a-f]{16}")
_POSITIVE_DECIMAL = re.compile(r"[1-9][0-9]*")
_STRICT_VERSION = re.compile(
    r"(?P<release>(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*))*)"
    r"(?:\.post(?P<post>0|[1-9][0-9]*))?"
)
_CANONICAL_ALPHA_REQUIREMENT_OPERAND = re.compile(
    r"(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*))*a(?:0|[1-9][0-9]*)"
)
_CANONICAL_PREFIX_WILDCARD_REQUIREMENT_OPERAND = re.compile(
    r"(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*))*\.\*"
)


def _cargo_wrapper_source() -> str:
    static = repr(dict(sorted(_STATIC_BUILD_ENVIRONMENT.items())))
    derived = repr(dict(sorted(_DERIVED_ENVIRONMENT.items())))
    dynamic = repr(tuple(sorted(_BUILDER_DERIVED_ENVIRONMENT)))
    overrides = repr(dict(sorted(_SOURCE_EFFECTIVE_OVERRIDES.items())))
    cargo = f'{_EXTERNAL_ROOTS["candidate_rust_toolchain_root"]}/bin/cargo'
    build_root = _EXTERNAL_ROOTS["candidate_build_root"]
    return (
        "#!/usr/bin/python3.12 -IS\n"
        "import os\n"
        "import sys\n\n"
        f"BUILD_ROOT = {build_root!r}\n"
        f"CARGO = {cargo!r}\n"
        f"STATIC = {static}\n"
        f"DERIVED = {derived}\n"
        f"DYNAMIC = {dynamic}\n"
        f"OVERRIDES = {overrides}\n"
        f"RUSTFLAGS_APPEND = {_RUSTFLAGS_RELEASE_APPEND!r}\n\n"
        "stage = os.environ.get('P1_U04_STAGING_ROOT', '')\n"
        "prefix = BUILD_ROOT + '/stage-'\n"
        "token = stage[len(prefix):] if stage.startswith(prefix) else ''\n"
        "if len(token) != 16 or any(c not in '0123456789abcdef' for c in token):\n"
        "    raise SystemExit('cargo wrapper environment is not exact')\n"
        "expected = dict(STATIC)\n"
        "expected.update({name: stage + suffix for name, suffix in DERIVED.items()})\n"
        "dynamic = {}\n"
        "for name in DYNAMIC:\n"
        "    value = os.environ.get(name, '')\n"
        "    if not value or value[0] == '0' or not value.isascii() or not value.isdecimal():\n"
        "        raise SystemExit('cargo wrapper source identity is not exact')\n"
        "    dynamic[name] = value\n"
        "expected.update(dynamic)\n"
        "expected.update(OVERRIDES)\n"
        "expected['RUSTFLAGS'] += RUSTFLAGS_APPEND\n"
        "if dict(os.environ) != expected:\n"
        "    raise SystemExit('cargo wrapper environment is not exact')\n"
        "if len(sys.argv) < 2 or sys.argv[1] != 'build':\n"
        "    raise SystemExit('cargo wrapper only permits build')\n"
        "if os.getcwd() != expected['PWD']:\n"
        "    raise SystemExit('cargo wrapper working directory is not exact')\n"
        "source = os.stat('.')\n"
        "if (str(source.st_dev), str(source.st_ino)) != (\n"
        "    dynamic['P1_U04_SOURCE_ST_DEV'], dynamic['P1_U04_SOURCE_ST_INO']\n"
        "):\n"
        "    raise SystemExit('cargo wrapper source identity is not exact')\n"
        "os.execve(CARGO, [CARGO, 'build', '--locked', '--offline', *sys.argv[2:]], expected)\n"
    )


class VerificationError(RuntimeError):
    """The policy or supplied external evidence failed closed."""


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise VerificationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicates
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise VerificationError(f"invalid JSON policy {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise VerificationError(f"policy must be a JSON object: {path}")
    return value


def _canonical_bytes(value: object, *, pretty: bool = False) -> bytes:
    if pretty:
        encoded = json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True)
    else:
        encoded = json.dumps(
            value, ensure_ascii=True, separators=(",", ":"), sort_keys=True
        )
    return (encoded + "\n").encode("ascii")


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _mode(st: os.stat_result) -> str:
    return f"{stat.S_IMODE(st.st_mode):04o}"


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _require_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise VerificationError(f"{label} fields do not match the reviewed schema")


def _json_values_identical(observed: object, expected: object) -> bool:
    if type(observed) is not type(expected):
        return False
    if isinstance(expected, dict):
        return set(observed) == set(expected) and all(
            _json_values_identical(observed[key], value)
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return len(observed) == len(expected) and all(
            _json_values_identical(observed_item, expected_item)
            for observed_item, expected_item in zip(observed, expected, strict=True)
        )
    return observed == expected


def _require_sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise VerificationError(f"{label} is not an exact SHA-256")
    return value


def _verify_repo_file(record: dict[str, Any], prefix: str) -> None:
    path_value = record[f"{prefix}_path"]
    digest = record[f"{prefix}_sha256"]
    if not isinstance(path_value, str):
        raise VerificationError(f"{prefix} path is invalid")
    path = ROOT / path_value
    if not path.is_file() or _sha256_file(path) != _require_sha256(
        digest, f"{prefix} digest"
    ):
        raise VerificationError(f"{prefix} reviewed file identity drifted")


def _tree_inventory(root: Path, label: str) -> tuple[list[dict[str, Any]], os.stat_result]:
    try:
        root_st = root.lstat()
    except OSError as exc:
        raise VerificationError(f"{label} root unavailable: {exc}") from exc
    if not stat.S_ISDIR(root_st.st_mode) or root.is_symlink():
        raise VerificationError(f"{label} root must be a real directory")
    records: list[dict[str, Any]] = []
    try:
        paths = sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix())
        for path in paths:
            st = path.lstat()
            record: dict[str, Any] = {
                "mode": _mode(st),
                "path": path.relative_to(root).as_posix(),
            }
            if stat.S_ISREG(st.st_mode):
                record.update(
                    {
                        "sha256": _sha256_file(path),
                        "size": st.st_size,
                        "type": "file",
                    }
                )
            elif stat.S_ISDIR(st.st_mode):
                record["type"] = "directory"
            elif stat.S_ISLNK(st.st_mode):
                record.update({"target": os.readlink(path), "type": "symlink"})
            else:
                raise VerificationError(f"unsupported {label} entry: {path}")
            records.append(record)
    except OSError as exc:
        raise VerificationError(f"cannot inventory {label}: {exc}") from exc
    return records, root_st


def _verify_tree_inventory(inventory: dict[str, Any], label: str) -> None:
    _require_keys(
        inventory,
        {
            "directory_count",
            "file_count",
            "inventory_algorithm",
            "path",
            "record_count",
            "root_gid",
            "root_mode",
            "root_uid",
            "symlink_count",
            "tree_sha256",
        },
        f"{label} inventory",
    )
    if inventory["inventory_algorithm"] != "canonical-relative-lstat-sha256-v1":
        raise VerificationError(f"unknown {label} inventory algorithm")
    records, root_st = _tree_inventory(Path(inventory["path"]), label)
    observed = {
        "directory_count": sum(item["type"] == "directory" for item in records),
        "file_count": sum(item["type"] == "file" for item in records),
        "record_count": len(records),
        "root_gid": root_st.st_gid,
        "root_mode": _mode(root_st),
        "root_uid": root_st.st_uid,
        "symlink_count": sum(item["type"] == "symlink" for item in records),
        "tree_sha256": _sha256_bytes(_canonical_bytes(records)[:-1]),
    }
    if observed != {key: inventory[key] for key in observed}:
        raise VerificationError(f"{label} inventory drifted")


def _verify_path_record(record: dict[str, Any], label: str) -> None:
    common = {"gid", "mode", "path", "size", "type", "uid"}
    kind = record.get("type")
    expected = common | ({"sha256"} if kind == "file" else {"target"})
    _require_keys(record, expected, label)
    path = Path(record["path"])
    try:
        st = path.lstat()
    except OSError as exc:
        raise VerificationError(f"{label} unavailable: {exc}") from exc
    observed: dict[str, Any] = {
        "gid": st.st_gid,
        "mode": _mode(st),
        "path": str(path),
        "size": st.st_size,
        "type": "symlink" if stat.S_ISLNK(st.st_mode) else "file",
        "uid": st.st_uid,
    }
    if stat.S_ISREG(st.st_mode):
        observed["sha256"] = _sha256_file(path)
    elif stat.S_ISLNK(st.st_mode):
        observed["target"] = os.readlink(path)
    else:
        raise VerificationError(f"{label} is not a regular file or symlink")
    if observed != record:
        raise VerificationError(f"{label} identity drifted")


def _rust_router_target(
    inputs: dict[str, Any], name: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    component_name, archive_member, size, digest = {
        "cargo": (
            "cargo",
            "cargo-1.97.1-x86_64-unknown-linux-gnu/cargo/bin/cargo",
            42_839_824,
            "828980723df339d62434390e9fb8ef8831036583343ae2316b7ab5646b5c1953",
        ),
        "rustc": (
            "rustc",
            "rustc-1.97.1-x86_64-unknown-linux-gnu/rustc/bin/rustc",
            645_000,
            "d3a664c970a9fd8361b64194861bebc1ae37b9054e5ee3400dc1c9e691797eea",
        ),
    }[name]
    component = next(
        item for item in inputs["rust"]["components"] if item["name"] == component_name
    )
    path = f'{_EXTERNAL_ROOTS["candidate_rust_toolchain_root"]}/bin/{name}'
    resolved = {
        "mode": "0500",
        "path": path,
        "sha256": digest,
        "size": size,
        "type": "file",
    }
    authority = {
        "archive_filename": component["filename"],
        "archive_member": archive_member,
        "archive_sha256": component["sha256"],
        "archive_size": component["size"],
        "authority": "SEALED_RUST_COMPONENT_ARCHIVE_MEMBER",
    }
    return resolved, authority


def _expected_command_router(engine: dict[str, Any]) -> dict[str, Any]:
    inputs = load_json(INPUT_POLICY)
    llvm_path = ROOT / engine["llvm_toolchain"]["policy_path"]
    llvm = load_json(llvm_path)
    rust_cargo, cargo_authority = _rust_router_target(inputs, "cargo")
    rustc, rustc_authority = _rust_router_target(inputs, "rustc")
    wrapper_raw = _cargo_wrapper_source().encode("ascii")
    entries: list[dict[str, Any]] = [
        {
            "contents": wrapper_raw.decode("ascii"),
            "exec_target": {**rust_cargo, "source_authority": cargo_authority},
            "interpreter": {
                "kernel_shebang": "#!/usr/bin/python3.12 -IS",
                "mode": engine["python"]["executable_mode"],
                "path": engine["python"]["executable"],
                "sha256": engine["python"]["executable_sha256"],
                "size": engine["python"]["executable_size"],
                "startup_argv": [
                    engine["python"]["executable"],
                    "-IS",
                    f"{_ROUTER_BIN}/cargo",
                ],
                "startup_flags": ["-I", "-S"],
            },
            "mode": "0500",
            "name": "cargo",
            "path": "bin/cargo",
            "sha256": _sha256_bytes(wrapper_raw),
            "size": len(wrapper_raw),
            "source_authority": "INLINE_HASH_BOUND_STDLIB_CPYTHON_WRAPPER",
            "type": "file",
        }
    ]
    for name, tool_name in (
        ("clang", "clang"),
        ("clang++", "clang++"),
        ("ld", "ld.lld"),
        ("ld.lld", "ld.lld"),
    ):
        tool = llvm["tools"][tool_name]
        resolved = {
            "mode": "0500",
            "path": f'{_EXTERNAL_ROOTS["candidate_llvm_toolchain_root"]}/bin/{tool_name}',
            "sha256": tool["sha256"],
            "size": tool["size"],
            "type": "file",
        }
        entries.append(
            {
                "link_target": resolved["path"],
                "mode": "0777",
                "name": name,
                "path": f"bin/{name}",
                "resolved": resolved,
                "source_authority": {
                    "archive_path": tool["archive_path"],
                    "authority": "IMMUTABLE_LLVM_POLICY_TOOL",
                    "policy_path": engine["llvm_toolchain"]["policy_path"],
                    "policy_sha256": engine["llvm_toolchain"]["policy_sha256"],
                },
                "type": "symlink",
            }
        )
    entries.extend(
        [
            {
                "link_target": rustc["path"],
                "mode": "0777",
                "name": "rustc",
                "path": "bin/rustc",
                "resolved": rustc,
                "source_authority": rustc_authority,
                "type": "symlink",
            },
            {
                "link_target": "/usr/bin/strip",
                "mode": "0777",
                "name": "strip",
                "path": "bin/strip",
                "resolved": {
                    "mode": "0755",
                    "path": "/usr/bin/x86_64-linux-gnu-strip",
                    "sha256": "0d980587ada7ab12193f39271f060d5663aa2f289b0e80d2a0274ce7306e4e42",
                    "size": 166_568,
                    "type": "file",
                },
                "source_authority": {
                    "authority": "NATIVE_BUILD_SYSTEM_TOOL_CHAIN",
                    "link_path": "/usr/bin/strip",
                    "resolved_path": "/usr/bin/x86_64-linux-gnu-strip",
                },
                "type": "symlink",
            },
        ]
    )
    entries.sort(key=lambda item: item["name"])
    return {
        "authority": "EXACT_SEALED_COMMAND_ROUTER_V1",
        "directory_mode": "0500",
        "directory_set": [".", "bin"],
        "entries": entries,
        "file_set": [f"bin/{item['name']}" for item in entries],
        "root": _CANDIDATE_TOOLCHAIN_ROOT,
    }


def _verify_command_router_policy(
    policy: dict[str, Any], engine: dict[str, Any]
) -> None:
    if policy != _expected_command_router(engine):
        raise VerificationError("command router policy is not exact")


def _verify_command_router_layout(root: Path, policy: dict[str, Any]) -> None:
    _require_keys(
        policy,
        {
            "authority",
            "directory_mode",
            "directory_set",
            "entries",
            "file_set",
            "root",
        },
        "command router",
    )
    entries = policy["entries"]
    names = [item.get("name") for item in entries if isinstance(item, dict)]
    paths = [item.get("path") for item in entries if isinstance(item, dict)]
    if (
        len(names) != len(entries)
        or len(set(names)) != len(names)
        or len(set(paths)) != len(paths)
        or policy["file_set"] != paths
        or str(root) != policy["root"]
    ):
        raise VerificationError("command router policy has duplicate or invalid entries")
    try:
        root_st = root.lstat()
        bin_st = (root / "bin").lstat()
    except OSError as exc:
        raise VerificationError(f"command router is unavailable: {exc}") from exc
    if any(
        not stat.S_ISDIR(item.st_mode)
        or stat.S_IMODE(item.st_mode) != int(policy["directory_mode"], 8)
        or item.st_uid != os.geteuid()
        for item in (root_st, bin_st)
    ) or root.is_symlink() or (root / "bin").is_symlink():
        raise VerificationError("command router directory is mutable or unsafe")
    observed_directories = {"."}
    observed_files: set[str] = set()
    for current, directories, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        relative = current_path.relative_to(root)
        observed_directories.update(
            (relative / name).as_posix() for name in directories
        )
        observed_files.update((relative / name).as_posix() for name in files)
    if (
        observed_directories != set(policy["directory_set"])
        or observed_files != set(policy["file_set"])
    ):
        raise VerificationError("command router exact directory or file set drifted")
    for entry in entries:
        path = root / entry["path"]
        try:
            info = path.lstat()
        except OSError as exc:
            raise VerificationError(f"command router entry unavailable: {path}") from exc
        if entry["type"] == "file":
            raw = entry["contents"].encode("ascii")
            if (
                not stat.S_ISREG(info.st_mode)
                or path.is_symlink()
                or info.st_nlink != 1
                or info.st_uid != os.geteuid()
                or _mode(info) != entry["mode"]
                or info.st_size != entry["size"]
                or path.read_bytes() != raw
                or _sha256_bytes(raw) != entry["sha256"]
            ):
                raise VerificationError(f"command router file identity drifted: {path}")
            continue
        if (
            entry["type"] != "symlink"
            or not stat.S_ISLNK(info.st_mode)
            or _mode(info) != entry["mode"]
            or os.readlink(path) != entry["link_target"]
        ):
            raise VerificationError(f"command router symlink drifted: {path}")
        try:
            resolved_path = path.resolve(strict=True)
            resolved_st = resolved_path.lstat()
        except OSError as exc:
            raise VerificationError(f"command router target unavailable: {path}") from exc
        resolved = entry["resolved"]
        if (
            str(resolved_path) != resolved["path"]
            or not stat.S_ISREG(resolved_st.st_mode)
            or resolved_path.is_symlink()
            or _mode(resolved_st) != resolved["mode"]
            or resolved_st.st_size != resolved["size"]
            or _sha256_file(resolved_path) != resolved["sha256"]
        ):
            raise VerificationError(f"command router resolved target drifted: {path}")


def _verify_snapshot_python_policy(
    policy: dict[str, Any], native_authority: dict[str, Any]
) -> None:
    expected = {
        "abi",
        "admitted_sys_path",
        "authority",
        "executable",
        "executable_gid",
        "executable_mode",
        "executable_sha256",
        "executable_size",
        "executable_uid",
        "explicit_build_path_admission",
        "identity",
        "implementation",
        "libpython",
        "minor_version",
        "platform",
        "excluded_startup_paths",
        "selection_basis",
        "shared_writable_state",
        "startup_argv",
        "stdlib_external_symlinks",
        "stdlib_inventory",
    }
    _require_keys(policy, expected, "Python policy")
    if (
        policy["authority"] != "REVIEWED_SYSTEM_BINARY_AND_STDLIB_INVENTORY"
        or policy["implementation"] != "CPython"
        or policy["identity"] != "CPython 3.12.3"
        or policy["minor_version"] != "3.12"
        or policy["abi"] != "cp312"
        or policy["platform"] != "linux_x86_64"
        or policy["selection_basis"]
        != "APPROVED_PLAN_AND_UPSTREAM_SUPPORTED_BASELINE"
        or policy["shared_writable_state"] != "PROHIBITED"
        or not _json_values_identical(
            policy["startup_argv"], ["/usr/bin/python3.12", "-I", "-S"]
        )
        or not _json_values_identical(
            policy["admitted_sys_path"],
            [
                "/usr/lib/python312.zip",
                "/usr/lib/python3.12",
                "/usr/lib/python3.12/lib-dynload",
            ],
        )
        or not _json_values_identical(
            policy["excluded_startup_paths"],
            [
                "/etc/python3.12/sitecustomize.py",
                "/usr/lib/python3/dist-packages",
                "/usr/lib/python3.12/site-packages",
                "/usr/local/lib/python3.12/dist-packages",
                "/usr/local/lib/python3.12/site-packages",
            ],
        )
        or not _json_values_identical(
            policy["stdlib_external_symlinks"],
            [
                {
                    "disposition": "BOUND_BY_LIBPYTHON_RECORD",
                    "path": "/usr/lib/python3.12/config-3.12-x86_64-linux-gnu/libpython3.12.so",
                    "resolved_path": "/usr/lib/x86_64-linux-gnu/libpython3.12.so.1.0",
                    "target": "../../x86_64-linux-gnu/libpython3.12.so.1",
                },
                {
                    "disposition": "EXCLUDED_BY_ISOLATED_NO_SITE_STARTUP",
                    "path": "/usr/lib/python3.12/sitecustomize.py",
                    "resolved_path": "/etc/python3.12/sitecustomize.py",
                    "target": "/etc/python3.12/sitecustomize.py",
                },
            ],
        )
        or not _json_values_identical(
            policy["explicit_build_path_admission"],
            {
                "authority": "EXACT_WHEEL_CACHE_POLICY_FILENAMES_ONLY",
                "environment_pythonpath": "PROHIBITED",
                "injection": "EXPLICIT_SYS_PATH_PREPEND_BEFORE_IMPORT",
                "root": "/home/thenam176/.cache/p1-u03-toolchain-policy-20260823/candidate-inputs/wheels",
            },
        )
    ):
        raise VerificationError("Python authority is not the reviewed CPython 3.12 input")

    if (
        policy["executable"] != "/usr/bin/python3.12"
        or not _json_values_identical(policy["executable_gid"], 0)
        or policy["executable_mode"] != "0755"
        or policy["executable_sha256"]
        != "1643dacd9feaedc58f3cc581e4d22577dfe25c09b10282936186ccf0f2e61118"
        or not _json_values_identical(policy["executable_size"], 8020928)
        or not _json_values_identical(policy["executable_uid"], 0)
        or not _json_values_identical(
            policy["libpython"],
            {
                "gid": 0,
                "mode": "0644",
                "path": "/usr/lib/x86_64-linux-gnu/libpython3.12.so.1.0",
                "sha256": "a4c35494d197a92f08a9d0a94975d9558e7a50880c42947484f01b720b68d423",
                "size": 9061000,
                "uid": 0,
            },
        )
        or not _json_values_identical(
            policy["stdlib_inventory"],
            {
                "directory_count": 92,
                "file_count": 1213,
                "inventory_algorithm": "canonical-relative-lstat-sha256-v1",
                "path": "/usr/lib/python3.12",
                "record_count": 1308,
                "root_gid": 0,
                "root_mode": "0755",
                "root_uid": 0,
                "symlink_count": 3,
                "tree_sha256": "0c17594ac603d6ba61b6b25c25f7f4e748fecb3362741bf191e717f36a39522e",
            },
        )
    ):
        raise VerificationError("Python authority is not the reviewed CPython 3.12 input")
    snapshot = (
        native_authority.get("snapshot")
        if isinstance(native_authority, dict)
        else None
    )
    mappings = snapshot.get("mappings") if isinstance(snapshot, dict) else None
    if not isinstance(mappings, list) or not all(
        isinstance(record, dict) and isinstance(record.get("destination"), str)
        for record in mappings
    ):
        raise VerificationError("Python policy is not covered by native snapshot mappings")
    destinations = {record["destination"] for record in mappings}
    if not {
        policy["executable"],
        policy["stdlib_inventory"]["path"],
        str(Path(policy["libpython"]["path"]).parent),
    }.issubset(destinations):
        raise VerificationError("Python policy is not covered by native snapshot mappings")
    _verify_native_build_authority(native_authority)


def _verify_native_build_authority(policy: dict[str, Any]) -> None:
    from scripts import materialize_nautilus_native_authority as snapshot_authority

    _require_keys(
        policy,
        {
            "authority",
            "llvm_authority",
            "snapshot",
            "usage",
        },
        "native build authority",
    )
    if (
        policy["authority"]
        != "P1_U04_IMMUTABLE_NATIVE_AUTHORITY_SNAPSHOT_V1"
        or policy["usage"] != "U04_OFFLINE_BUILD_ONLY"
        or policy["llvm_authority"] != "IMMUTABLE_LLVM_POLICY_REFERENCE"
    ):
        raise VerificationError("native build authority is ambiguous")
    binding = policy["snapshot"]
    if not isinstance(binding, dict):
        raise VerificationError("native snapshot policy binding is invalid")
    _require_keys(
        binding,
        {
            "authority",
            "mappings",
            "payload_tree_sha256",
            "receipt_path",
            "receipt_sha256",
            "root",
            "schema_version",
            "threat_model",
        },
        "native snapshot policy",
    )
    expected_mappings = [
        {"destination": destination, "source": source}
        for source, destination in snapshot_authority.SOURCE_DESTINATION_MAPPINGS
    ]
    expected_binding = {
        "authority": "P1_U04_IMMUTABLE_NATIVE_AUTHORITY_SNAPSHOT_V1",
        "mappings": expected_mappings,
        "payload_tree_sha256": _NATIVE_SNAPSHOT_PAYLOAD_SHA256,
        "receipt_path": str(snapshot_authority.RECEIPT_PATH),
        "receipt_sha256": _NATIVE_SNAPSHOT_RECEIPT_SHA256,
        "root": str(snapshot_authority.SNAPSHOT_ROOT),
        "schema_version": 1,
        "threat_model": "COOPERATIVE_HOST",
    }
    if not _json_values_identical(binding, expected_binding):
        raise VerificationError("native snapshot policy binding drifted")
    _require_sha256(binding["payload_tree_sha256"], "native snapshot payload tree")
    _require_sha256(binding["receipt_sha256"], "native snapshot receipt")


def _list_prefix(value: ast.AST) -> list[str]:
    if not isinstance(value, ast.List):
        return []
    prefix: list[str] = []
    for item in value.elts:
        if not isinstance(item, ast.Constant) or not isinstance(item.value, str):
            break
        prefix.append(item.value)
    return prefix


def _environment_subscript(node: ast.AST) -> str | None:
    if (
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Attribute)
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "os"
        and node.value.attr == "environ"
        and isinstance(node.slice, ast.Constant)
        and isinstance(node.slice.value, str)
    ):
        return node.slice.value
    return None


def _trace_build_script(raw: bytes) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8")
        tree = ast.parse(text, filename="build.py")
    except (SyntaxError, UnicodeError, ValueError) as exc:
        raise VerificationError(f"build.py source trace is invalid: {exc}") from exc

    def source(node: ast.AST) -> str:
        value = ast.get_source_segment(text, node)
        if value is None:
            raise VerificationError("build.py source trace has an unknown expression")
        return value

    nodes = sorted(
        ast.walk(tree),
        key=lambda node: (
            getattr(node, "lineno", -1),
            getattr(node, "col_offset", -1),
        ),
    )
    list_assignments: dict[str, set[tuple[str, ...]]] = {}
    for node in nodes:
        value: ast.AST | None = None
        targets: list[ast.AST] = []
        if isinstance(node, ast.Assign):
            value = node.value
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            value = node.value
            targets = [node.target]
        if value is None:
            continue
        prefix = _list_prefix(value)
        if not prefix:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                list_assignments.setdefault(target.id, set()).add(tuple(prefix))

    reads: set[str] = set()
    deletes: list[str] = []
    writes: list[dict[str, str]] = []
    subprocess_calls: list[dict[str, Any]] = []
    bare: set[str] = set()
    for node in nodes:
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load):
            name = _environment_subscript(node)
            if name is not None:
                reads.add(name)
        if isinstance(node, ast.Assign):
            for target in node.targets:
                name = _environment_subscript(target)
                if name is not None:
                    writes.append({"name": name, "value_source": source(node.value)})
        if isinstance(node, ast.Compare):
            values = [node.left, *node.comparators]
            for left, right in zip(values, values[1:]):
                if not any(isinstance(operator, (ast.In, ast.NotIn)) for operator in node.ops):
                    continue
                if (
                    isinstance(left, ast.Constant)
                    and isinstance(left.value, str)
                    and isinstance(right, ast.Attribute)
                    and isinstance(right.value, ast.Name)
                    and right.value.id == "os"
                    and right.attr == "environ"
                ):
                    reads.add(left.value)
        if not isinstance(node, ast.Call):
            continue
        if (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "os"
            and node.func.attr == "getenv"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            reads.add(node.args[0].value)
        if (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Attribute)
            and isinstance(node.func.value.value, ast.Name)
            and node.func.value.value.id == "os"
            and node.func.value.attr == "environ"
            and node.func.attr in {"get", "pop"}
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            reads.add(node.args[0].value)
            if node.func.attr == "pop":
                deletes.append(node.args[0].value)
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "print_env_var_if_exists"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            reads.add(node.args[0].value)
        if (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "subprocess"
            and node.func.attr == "run"
            and node.args
        ):
            argument = node.args[0]
            if isinstance(argument, ast.Name):
                prefixes = sorted(list_assignments.get(argument.id, set()))
            else:
                prefix = _list_prefix(argument)
                prefixes = [tuple(prefix)] if prefix else []
            if not prefixes:
                raise VerificationError("build.py source trace has an unknown subprocess")
            bare.update(prefix[0] for prefix in prefixes if prefix)
            subprocess_calls.append(
                {
                    "argument_source": source(argument),
                    "argv_prefixes": [list(prefix) for prefix in prefixes],
                }
            )
    return {
        "bare_subprocess_executables": sorted(bare),
        "environment_deletes": deletes,
        "environment_reads": sorted(reads),
        "environment_writes": writes,
        "subprocess_calls": subprocess_calls,
    }


def _verify_build_script_trace(raw: bytes, expected: dict[str, Any]) -> None:
    if _trace_build_script(raw) != expected:
        raise VerificationError("build.py source trace drifted")


def _verify_build_environment_policy(policy: dict[str, Any]) -> None:
    _require_keys(
        policy,
        {
            "builder_derived_environment",
            "construction",
            "derived_environment",
            "derived_paths",
            "effective_source_overrides",
            "inherited_allowlist",
            "prohibited_source_environment",
            "route",
            "sealed_source_trace",
            "source_identity_handoff",
            "staging",
            "static_exact_values",
            "target",
        },
        "build environment",
    )
    trace = policy["sealed_source_trace"]
    expected_native_trace = {
        "archive_finalization": "AR_S_NO_RANLIB_PROCESS",
        "aws-lc-sys": {
            "route_source": "builder/main.rs:get_builder",
            "sha256": "43103168cc76fe62678a375e722fc9cb3a0146159ac5828bc4f0dfd755c2224c",
            "version": "0.43.0",
        },
        "cc": {
            "archive_source": "src/lib.rs:compile",
            "sha256": "5add81bb678e6cb321aff7fa0dc7689ad82b112dbc032cea19f91d6b8e3582b9",
            "version": "1.4.0",
        },
        "selection": "USE_SYSTEM_FALSE_THEN_CMAKE_BUILDER_FALSE",
    }
    expected_overrides = {
        **_SOURCE_EFFECTIVE_OVERRIDES,
        "RUSTFLAGS": _INITIAL_RUSTFLAGS + _RUSTFLAGS_RELEASE_APPEND,
    }
    expected_staging = {
        "child_name_pattern": "stage-[0-9a-f]{16}",
        "derived_path_suffixes": _DERIVED_PATH_SUFFIXES,
        "materialized_directory_mode": "0700",
        "parent_root": _EXTERNAL_ROOTS["candidate_build_root"],
        "required_initial_state": "ABSENT",
        "writable_scope": "DERIVED_PATHS_ONLY",
    }
    if (
        policy["construction"] != "EMPTY_THEN_SET_EXACT_VALUES"
        or policy["inherited_allowlist"] != []
        or policy["route"] != "AWS_LC_SOURCE_DIRECT_CC"
        or policy["target"] != _TARGET
        or policy["static_exact_values"] != _STATIC_BUILD_ENVIRONMENT
        or policy["builder_derived_environment"]
        != _BUILDER_DERIVED_ENVIRONMENT
        or policy["derived_environment"] != _DERIVED_ENVIRONMENT
        or policy["derived_paths"] != _DERIVED_PATH_SUFFIXES
        or policy["effective_source_overrides"] != expected_overrides
        or policy["prohibited_source_environment"]
        != _PROHIBITED_SOURCE_ENVIRONMENT
        or policy["staging"] != expected_staging
        or policy["source_identity_handoff"] != _SOURCE_IDENTITY_HANDOFF
        or not isinstance(trace, dict)
        or set(trace) != {*expected_native_trace, "build.py"}
        or {key: trace[key] for key in expected_native_trace}
        != expected_native_trace
        or not isinstance(trace["build.py"], dict)
    ):
        raise VerificationError("build environment is not exact")
    controlled = (
        set(_STATIC_BUILD_ENVIRONMENT)
        | set(_DERIVED_ENVIRONMENT)
        | set(expected_overrides)
        | set(_PROHIBITED_SOURCE_ENVIRONMENT)
    )
    if set(trace["build.py"].get("environment_reads", [])) - controlled:
        raise VerificationError("build.py source environment reads are not closed")
    if trace["build.py"].get("bare_subprocess_executables") != [
        "cargo",
        "clang",
        "rustc",
        "strip",
    ]:
        raise VerificationError("build.py bare executable set is not exact")


def _verify_build_environment(
    policy: dict[str, Any],
    inherited: dict[str, str] | None,
    staging_child: Path,
    *,
    verified_source_fd: int | None,
    mount_destinations: list[Path] | tuple[Path, ...],
    supplied_initial: dict[str, str] | None = None,
    supplied_effective: dict[str, str] | None = None,
) -> dict[str, dict[str, str]]:
    _verify_build_environment_policy(policy)
    if type(verified_source_fd) is not int:
        raise VerificationError("verified source fd authority is required")
    try:
        source_stat = os.fstat(verified_source_fd)
    except OSError as exc:
        raise VerificationError(f"verified source fd is invalid: {exc}") from exc
    if (
        not stat.S_ISDIR(source_stat.st_mode)
        or source_stat.st_dev <= 0
        or source_stat.st_ino <= 0
    ):
        raise VerificationError("verified source fd is not a positive directory identity")
    if not isinstance(inherited, dict) or any(
        not isinstance(name, str) or not isinstance(value, str)
        for name, value in inherited.items()
    ):
        raise VerificationError("ambient build environment is invalid")
    if inherited:
        raise VerificationError(
            f"ambient build override is prohibited: {sorted(inherited)}"
        )
    build_root = Path(_EXTERNAL_ROOTS["candidate_build_root"])
    raw_stage = str(staging_child)
    if (
        not staging_child.is_absolute()
        or os.path.normpath(raw_stage) != raw_stage
        or staging_child.parent != build_root
        or _STAGE_NAME.fullmatch(staging_child.name) is None
        or staging_child.exists()
        or staging_child.is_symlink()
    ):
        raise VerificationError("staging child must be one fresh absent direct child")
    current = Path("/")
    for part in staging_child.parts[1:]:
        current /= part
        if current.is_symlink():
            raise VerificationError("staging child has a symlinked ancestor")
        if not current.exists():
            break
    if build_root.exists():
        root_st = build_root.lstat()
        if (
            not stat.S_ISDIR(root_st.st_mode)
            or build_root.is_symlink()
            or root_st.st_uid != os.geteuid()
            or _mode(root_st) != "0700"
        ):
            raise VerificationError("staging child parent is not private")
    stage = str(staging_child)
    if (
        not isinstance(mount_destinations, (list, tuple))
        or list(mount_destinations) != [staging_child]
    ):
        raise VerificationError("sandbox mount graph is not exact")
    paths = {
        name: stage + suffix for name, suffix in _DERIVED_PATH_SUFFIXES.items()
    }
    if len(set(paths.values())) != len(paths):
        raise VerificationError("derived staging paths overlap")
    for name, root in _EXTERNAL_ROOTS.items():
        if name == "candidate_build_root":
            continue
        root_parts = Path(root).parts
        stage_parts = staging_child.parts
        if _overlap(root_parts, stage_parts):
            raise VerificationError("derived staging path overlaps a static root")
    initial = dict(_STATIC_BUILD_ENVIRONMENT)
    initial.update(
        {name: stage + suffix for name, suffix in _DERIVED_ENVIRONMENT.items()}
    )
    source_identity = {
        "P1_U04_SOURCE_ST_DEV": str(source_stat.st_dev),
        "P1_U04_SOURCE_ST_INO": str(source_stat.st_ino),
    }
    if any(
        _POSITIVE_DECIMAL.fullmatch(value) is None
        for value in source_identity.values()
    ):
        raise VerificationError("verified source identity is not canonical")
    initial.update(source_identity)
    if initial["PWD"] != paths["source"]:
        raise VerificationError("sandbox chdir and PWD are not exact")
    effective = dict(initial)
    effective.update(_SOURCE_EFFECTIVE_OVERRIDES)
    effective["RUSTFLAGS"] += _RUSTFLAGS_RELEASE_APPEND
    for supplied in (supplied_initial, supplied_effective):
        if supplied is not None and any(
            not isinstance(supplied.get(name), str)
            or _POSITIVE_DECIMAL.fullmatch(supplied[name]) is None
            for name in _BUILDER_DERIVED_ENVIRONMENT
        ):
            raise VerificationError("supplied source identity is not canonical")
    if supplied_initial is not None and supplied_initial != initial:
        raise VerificationError("initial environment is missing, extra, or drifted")
    if supplied_effective is not None and supplied_effective != effective:
        raise VerificationError("effective environment is missing, extra, or drifted")
    return {
        "derived_paths": paths,
        "effective_environment": effective,
        "initial_environment": initial,
    }


def _safe_namespace(value: object, label: str) -> tuple[str, ...]:
    if (
        not isinstance(value, str)
        or not value
        or value.startswith("/")
        or "\\" in value
        or "//" in value
        or re.match(r"^[A-Za-z]:", value)
    ):
        raise VerificationError(f"{label} namespace is unsafe")
    parts = tuple(value.split("/"))
    if any(
        part in {"", ".", ".."} or re.match(r"^[A-Za-z]:", part)
        for part in parts
    ):
        raise VerificationError(f"{label} namespace is unsafe")
    return parts


def _overlap(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    width = min(len(left), len(right))
    return left[:width] == right[:width]


def _validate_external_roots(
    isolation: dict[str, Any], *, require_exact: bool = False
) -> None:
    _require_keys(
        isolation,
        {
            "candidate_namespace",
            "directory_mode",
            "external_roots",
            "file_mode",
            "rollback_namespace",
            "root_access",
            "shared_writable_paths",
            "writable_build_namespace",
        },
        "external cache isolation",
    )
    namespaces = {
        key: _safe_namespace(isolation[key], key)
        for key in (
            "candidate_namespace",
            "rollback_namespace",
            "writable_build_namespace",
        )
    }
    namespace_items = list(namespaces.items())
    for index, (left_name, left) in enumerate(namespace_items):
        for right_name, right in namespace_items[index + 1 :]:
            if _overlap(left, right):
                raise VerificationError(
                    f"namespace overlap: {left_name} and {right_name}"
                )
    roots = isolation["external_roots"]
    expected_roots = {
        "candidate_build_root",
        "candidate_cargo_home_root",
        "candidate_forensic_root",
        "candidate_input_root",
        "candidate_llvm_toolchain_root",
        "candidate_runtime_root",
        "candidate_rust_toolchain_root",
        "candidate_toolchain_root",
        "candidate_vendor_root",
        "rollback_root",
    }
    if not isinstance(roots, dict):
        raise VerificationError("external roots must be an object")
    _require_keys(roots, expected_roots, "external roots")
    parsed: dict[str, tuple[str, ...]] = {}
    for name, value in roots.items():
        if (
            not isinstance(value, str)
            or not value
            or not value.startswith("/")
            or value == "/"
            or "\\" in value
            or "//" in value
            or re.match(r"^[A-Za-z]:", value)
        ):
            raise VerificationError(f"external root is unsafe: {name}")
        parts = tuple(Path(value).parts)
        if os.path.normpath(value) != value or any(
            part in {"", ".", ".."} or re.match(r"^[A-Za-z]:", part)
            for part in parts[1:]
        ):
            raise VerificationError(f"external root is noncanonical: {name}")
        parsed[name] = parts
        current = Path("/")
        for part in parts[1:]:
            current /= part
            if current.is_symlink():
                raise VerificationError(f"external root has a symlinked ancestor: {name}")
            if not current.exists():
                break
    root_items = list(parsed.items())
    for index, (left_name, left) in enumerate(root_items):
        for right_name, right in root_items[index + 1 :]:
            if _overlap(left, right):
                raise VerificationError(f"external root overlap: {left_name} and {right_name}")
    if (
        isolation["directory_mode"] != "0500"
        or isolation["file_mode"] != "0400"
        or isolation["root_access"] != _ROOT_ACCESS
        or isolation["shared_writable_paths"] != "PROHIBITED"
    ):
        raise VerificationError("external root access policy drifted")
    if require_exact and roots != _EXTERNAL_ROOTS:
        raise VerificationError("static external roots drifted")


def _verify_source_authority(inputs: dict[str, Any]) -> None:
    authority = load_json(U02_POLICY)
    source = inputs["source"]
    if source["artifact"] != authority["source_authority"]["primary"]:
        raise VerificationError("input does not equal U02 primary source authority")
    expected_candidate = {
        "engine_name": authority["engine_name"],
        "release": authority["engine_version"],
        "repository": authority["upstream"]["repository"],
        "tag_object": authority["upstream"]["tag_object"],
        "upstream_commit": authority["upstream"]["peeled_commit"],
        "upstream_tag": authority["upstream"]["tag"],
    }
    if inputs["candidate"] != expected_candidate:
        raise VerificationError("input candidate does not equal U02 candidate authority")
    if source["u02_cache_layout"] != authority["cache_layout"]:
        raise VerificationError("input layout does not equal U02 cache layout authority")


def _verify_policies(
    engine: dict[str, Any],
    inputs: dict[str, Any],
    wheels: dict[str, Any],
    cargo: dict[str, Any],
) -> None:
    _require_keys(
        engine,
        {
            "activation_status",
            "build_execution",
            "candidate",
            "candidate_wheel_metadata",
            "command_router",
            "external_cache_isolation",
            "llvm_toolchain",
            "native_build_authority",
            "native_build_environment",
            "native_entry_guard",
            "python",
            "rust",
            "safety",
            "schema_version",
        },
        "engine policy",
    )
    _require_keys(
        inputs,
        {
            "cache_layout",
            "cache_trust_model",
            "candidate",
            "provenance_authority",
            "rust",
            "schema_version",
            "source",
        },
        "input policy",
    )
    _require_keys(
        wheels,
        {
            "bootstrap_exception",
            "build_closure",
            "candidate",
            "resolution",
            "runtime_direct",
            "runtime_transitive",
            "schema_version",
            "wheel_artifacts",
        },
        "wheel policy",
    )
    _require_keys(
        cargo,
        {
            "cache_directory",
            "candidate",
            "cargo_lock_sha256",
            "offline_cargo_config",
            "package_count",
            "packages",
            "registry_source",
            "schema_version",
            "vendor_materialization",
        },
        "Cargo registry policy",
    )
    if {
        engine["schema_version"],
        inputs["schema_version"],
        wheels["schema_version"],
        cargo["schema_version"],
    } != {1}:
        raise VerificationError("candidate policies must use reviewed schema 1")
    resolution = wheels["resolution"]
    _require_keys(
        resolution,
        {
            "allow_duplicate_files",
            "allow_extra_files",
            "allow_index_fallback",
            "allow_sdist_fallback",
            "installer_mode",
        },
        "wheel resolution policy",
    )
    if (
        any(resolution[key] is not False for key in resolution if key.startswith("allow_"))
        or resolution["installer_mode"] != "EXACT_LOCAL_FILENAMES_ONLY"
    ):
        raise VerificationError("wheel resolution is not closed and offline")
    for field in ("build_closure", "runtime_direct", "runtime_transitive"):
        values = wheels[field]
        if (
            not isinstance(values, list)
            or any(not isinstance(value, str) or not value for value in values)
            or values != sorted(set(values))
        ):
            raise VerificationError(f"{field} must be a sorted exact package set")
    artifact_keys = {
        "active_dependencies",
        "filename",
        "mode",
        "package",
        "roles",
        "sha256",
        "size",
        "source_classification",
        "tags",
        "url",
        "version",
    }
    previous_package = ""
    for artifact in wheels["wheel_artifacts"]:
        if not isinstance(artifact, dict):
            raise VerificationError("wheel artifact must be an object")
        _require_keys(artifact, artifact_keys, "wheel artifact")
        package = artifact["package"]
        version = artifact["version"]
        if not isinstance(version, str):
            raise VerificationError("wheel artifact version is invalid")
        try:
            _parse_version(version)
        except VerificationError as exc:
            raise VerificationError(
                "wheel artifact version is not an exact reviewed identity"
            ) from exc
        if (
            not isinstance(package, str)
            or package != _normalize(package)
            or package <= previous_package
            or artifact["mode"] != "0400"
            or type(artifact["size"]) is not int
            or artifact["size"] <= 0
            or not isinstance(artifact["filename"], str)
            or not artifact["filename"].endswith(".whl")
            or not isinstance(artifact["url"], str)
            or not artifact["url"].startswith("https://files.pythonhosted.org/")
            or not artifact["url"].endswith("/" + artifact["filename"])
        ):
            raise VerificationError("wheel artifact is not an exact reviewed identity")
        previous_package = package
        _require_sha256(artifact["sha256"], f"{package} wheel")
        if (
            artifact["roles"] != sorted(set(artifact["roles"]))
            or not artifact["roles"]
            or not set(artifact["roles"]).issubset({"build", "runtime"})
            or artifact["tags"] != list(dict.fromkeys(artifact["tags"]))
            or not artifact["tags"]
        ):
            raise VerificationError("wheel artifact roles or tags are ambiguous")
        previous_dependency = ""
        for dependency in artifact["active_dependencies"]:
            if not isinstance(dependency, dict):
                raise VerificationError("wheel dependency must be an object")
            _require_keys(dependency, {"package", "version"}, "wheel dependency")
            dependency_package = dependency["package"]
            dependency_version = dependency["version"]
            if not isinstance(dependency_version, str):
                raise VerificationError("wheel dependency version is invalid")
            _parse_version(dependency_version)
            if (
                not isinstance(dependency_package, str)
                or dependency_package <= previous_dependency
            ):
                raise VerificationError("wheel dependency is not an exact sorted pin")
            previous_dependency = dependency_package
    candidate_identity = {
        (policy["candidate"]["release"], policy["candidate"]["upstream_commit"])
        for policy in (engine, inputs, wheels, cargo)
    }
    if candidate_identity != {("1.231.0", "27a8e54e7ac3c57d6cbf8891f0283dfbaee97317")}:
        raise VerificationError("candidate identity drifted")
    requirements = _candidate_wheel_requires_dist(engine)
    active_names = []
    for requirement in requirements:
        name, _specifier, marker = _parse_requirement(requirement)
        if _metadata_marker_active(marker):
            active_names.append(name)
    if (
        len(active_names) != 11
        or len(requirements) - len(active_names) != 9
        or set(active_names) != set(wheels["runtime_direct"])
        or len(active_names) != len(set(active_names))
    ):
        raise VerificationError("candidate wheel metadata dependency partition drifted")
    _validate_external_roots(engine["external_cache_isolation"], require_exact=True)
    build = engine["build_execution"]
    _require_keys(
        build,
        {
            "allow_index_resolution",
            "allow_network",
            "allow_sdist_fallback",
            "cargo_entrypoint",
            "cargo_locked",
            "cargo_offline",
            "wheel_install_mode",
        },
        "build execution",
    )
    if (
        build["allow_index_resolution"] is not False
        or build["allow_network"] is not False
        or build["allow_sdist_fallback"] is not False
        or build["cargo_entrypoint"] != "SEALED_COMMAND_ROUTER_WRAPPER"
        or build["cargo_locked"] is not True
        or build["cargo_offline"] is not True
        or build["wheel_install_mode"] != "EXACT_LOCAL_FILENAMES_ONLY"
    ):
        raise VerificationError("offline exact build policy drifted")
    if engine["activation_status"] != "POLICY_ONLY_NOT_ACTIVATED" or any(
        value != "PROHIBITED" for value in engine["safety"].values() if value != "BUILD_INPUT_POLICY_ONLY"
    ):
        raise VerificationError("candidate safety boundary drifted")
    llvm = engine["llvm_toolchain"]
    if (
        llvm["version"] != "22.1.3"
        or llvm["consumption"] != "IMMUTABLE_READ_ONLY_POLICY_REFERENCE"
        or llvm["candidate_materialization"] != "SEPARATE_ROOT_REQUIRED"
        or llvm["shared_writable_cache"] != "PROHIBITED"
    ):
        raise VerificationError("LLVM immutable reuse policy drifted")
    _verify_repo_file(llvm, "policy")
    _verify_repo_file(llvm, "validator")
    _verify_command_router_policy(engine["command_router"], engine)
    provenance = inputs["provenance_authority"]
    _verify_repo_file(provenance, "policy")
    _verify_repo_file(provenance, "verifier")
    guard = engine["native_entry_guard"]
    if guard["status"] != "UNCHANGED_SEPARATE_REVIEWED_V1_227_TOOLCHAIN":
        raise VerificationError("native entry guard was retargeted")
    for path_key, digest_key in (
        ("source", "source_sha256"),
        ("cargo_manifest", "cargo_manifest_sha256"),
        ("cargo_lock", "cargo_lock_sha256"),
        ("rust_toolchain_policy", "rust_toolchain_policy_sha256"),
    ):
        path = ROOT / guard[path_key]
        if not path.is_file() or _sha256_file(path) != _require_sha256(
            guard[digest_key], digest_key
        ):
            raise VerificationError("native entry guard reviewed identity drifted")
    if guard["llvm_toolchain_policy_sha256"] != llvm["policy_sha256"]:
        raise VerificationError("native entry guard LLVM policy drifted")
    if engine["rust"]["rustc_version"] != "1.97.1" or engine["rust"]["cargo_version"] != "1.97.1":
        raise VerificationError("candidate Rust/Cargo version drifted")
    if inputs["rust"]["version"] != "1.97.1" or inputs["rust"]["target"] != engine["rust"]["target"]:
        raise VerificationError("Rust input policy does not match engine policy")
    _require_keys(
        inputs["source"],
        {"artifact", "build_inputs", "u02_cache_layout"},
        "source input authority",
    )
    config = cargo["offline_cargo_config"]
    _require_keys(
        config,
        {
            "cargo_home_root",
            "config_relative_path",
            "contents",
            "mode",
            "sha256",
            "vendor_root",
        },
        "offline Cargo config",
    )
    roots = engine["external_cache_isolation"]["external_roots"]
    expected_config = (
        '[net]\noffline = true\n\n[source.crates-io]\n'
        'replace-with = "candidate-vendor"\n\n[source.candidate-vendor]\n'
        f'directory = "{roots["candidate_vendor_root"]}"\n'
    )
    if (
        cargo["cache_directory"] != "cargo-registry"
        or config["cargo_home_root"] != roots["candidate_cargo_home_root"]
        or config["vendor_root"] != roots["candidate_vendor_root"]
        or config["config_relative_path"] != "config.toml"
        or config["mode"] != "0400"
        or config["contents"] != expected_config
        or config["sha256"] != _sha256_bytes(expected_config.encode("ascii"))
    ):
        raise VerificationError("offline Cargo home/source config drifted")
    if cargo["vendor_materialization"] != {
        "archive_format": "CRATES_IO_DOT_CRATE_TAR_GZIP",
        "checksum_file": ".cargo-checksum.json",
        "checksum_file_algorithm": "SORTED_RELATIVE_FILE_SHA256_WITH_LOCK_PACKAGE_CHECKSUM_V1",
        "directory_name_pattern": "{name}-{version}",
        "expected_directory_count": 862,
        "network": "PROHIBITED",
        "source": "EXACT_SEALED_CRATE_ARCHIVES_ONLY",
    }:
        raise VerificationError("Cargo vendor materialization policy drifted")
    _verify_source_authority(inputs)
    _verify_snapshot_python_policy(
        engine["python"], engine["native_build_authority"]
    )
    _verify_build_environment_policy(engine["native_build_environment"])


def _expected_cache_files(
    inputs: dict[str, Any], wheels: dict[str, Any], cargo: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}

    def add(relative: str, record: dict[str, Any]) -> None:
        if relative in records:
            raise VerificationError(f"duplicate artifact record: {relative}")
        records[relative] = record

    source_record = {
        **inputs["source"]["artifact"],
        "mode": inputs["cache_layout"]["file_mode"],
    }
    add("source-inputs/" + source_record["filename"], source_record)
    manifest = inputs["rust"]["channel_manifest"]
    add("rust-inputs/" + manifest["filename"], manifest)
    component_names: set[str] = set()
    for component in inputs["rust"]["components"]:
        if component["name"] in component_names:
            raise VerificationError("duplicate Rust component")
        component_names.add(component["name"])
        add("rust-inputs/" + component["filename"], component)
    if component_names != {"cargo", "rust-std", "rustc"}:
        raise VerificationError("Rust component closure is not exact")
    packages: set[str] = set()
    for artifact in wheels["wheel_artifacts"]:
        package = artifact["package"]
        if package in packages:
            raise VerificationError(f"duplicate wheel package: {package}")
        packages.add(package)
        add("wheels/" + artifact["filename"], artifact)
    for package in cargo["packages"]:
        add(cargo["cache_directory"] + "/" + package["filename"], package)
    return records


def _verify_cache(
    cache: Path,
    inputs: dict[str, Any],
    wheels: dict[str, Any],
    cargo: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], str]:
    try:
        root_st = cache.lstat()
    except OSError as exc:
        raise VerificationError(f"evidence cache unavailable: {exc}") from exc
    if not stat.S_ISDIR(root_st.st_mode) or cache.is_symlink():
        raise VerificationError("evidence cache must be a real directory")
    expected_mode = inputs["cache_layout"]["directory_mode"]
    if _mode(root_st) != expected_mode or root_st.st_uid != os.getuid():
        raise VerificationError("evidence cache root ownership or mode is invalid")
    expected_dirs = set(inputs["cache_layout"]["directories"])
    observed_root = {item.name for item in cache.iterdir()}
    if observed_root != expected_dirs:
        raise VerificationError("evidence cache directory set is not exact")
    for name in expected_dirs:
        path = cache / name
        st = path.lstat()
        if (
            not stat.S_ISDIR(st.st_mode)
            or path.is_symlink()
            or _mode(st) != expected_mode
            or st.st_uid != os.getuid()
        ):
            raise VerificationError(f"cache directory is mutable or unsafe: {name}")
    expected = _expected_cache_files(inputs, wheels, cargo)
    observed = {
        path.relative_to(cache).as_posix()
        for name in expected_dirs
        for path in (cache / name).iterdir()
    }
    if observed != set(expected):
        raise VerificationError("cache artifact set has extra, missing, or duplicate files")
    tree_records: list[dict[str, Any]] = []
    for relative, record in sorted(expected.items()):
        path = cache / relative
        st = path.lstat()
        digest = _sha256_file(path) if stat.S_ISREG(st.st_mode) else ""
        if (
            not stat.S_ISREG(st.st_mode)
            or path.is_symlink()
            or st.st_nlink != 1
            or st.st_uid != os.getuid()
            or _mode(st) != record["mode"]
            or st.st_size != record["size"]
            or digest != _require_sha256(record["sha256"], relative)
        ):
            raise VerificationError(f"cache artifact identity is invalid: {relative}")
        tree_records.append(
            {
                "mode": _mode(st),
                "path": relative,
                "sha256": digest,
                "size": st.st_size,
            }
        )
    tree_hash = _sha256_bytes(_canonical_bytes(tree_records)[:-1])
    return expected, tree_hash


def _source_members(
    archive_path: Path, inputs: dict[str, Any]
) -> dict[str, bytes]:
    artifact = inputs["source"]["artifact"]
    prefix = artifact["top_level_root"] + "/"
    wanted = {item["path"]: item for item in inputs["source"]["build_inputs"]}
    found: dict[str, bytes] = {}
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            for member in archive.getmembers():
                if not member.name.startswith(prefix):
                    continue
                relative = member.name[len(prefix) :]
                if relative not in wanted:
                    continue
                if relative in found or not member.isfile():
                    raise VerificationError(f"source member is duplicate or unsafe: {relative}")
                stream = archive.extractfile(member)
                if stream is None:
                    raise VerificationError(f"source member is unreadable: {relative}")
                raw = stream.read()
                record = wanted[relative]
                if len(raw) != record["size"] or _sha256_bytes(raw) != record["sha256"]:
                    raise VerificationError(f"source member identity drifted: {relative}")
                found[relative] = raw
    except (OSError, tarfile.TarError) as exc:
        raise VerificationError(f"source archive is invalid: {exc}") from exc
    if set(found) != set(wanted):
        raise VerificationError("source archive is missing reviewed build inputs")
    return found


def _parse_requirement(value: str) -> tuple[str, str, str | None]:
    match = _REQUIREMENT.fullmatch(value.strip())
    if match is None:
        raise VerificationError(f"unsupported source requirement: {value}")
    specifier = match.group(2).strip()
    if "(" in specifier or ")" in specifier:
        if (
            not specifier.startswith("(")
            or not specifier.endswith(")")
            or len(specifier) == 2
            or "(" in specifier[1:-1]
            or ")" in specifier[1:-1]
        ):
            raise VerificationError(f"unsupported source requirement: {value}")
        specifier = specifier[1:-1]
    _parse_specifier(specifier)
    return _normalize(match.group(1)), specifier, match.group(3)


def _parse_version(value: str) -> tuple[tuple[int, ...], int | None]:
    match = _STRICT_VERSION.fullmatch(value)
    if match is None:
        raise VerificationError(f"unsupported version: {value}")
    release = tuple(int(number) for number in match.group("release").split("."))
    post = match.group("post")
    return release, None if post is None else int(post)


def _compare_versions(
    left: tuple[tuple[int, ...], int | None],
    right: tuple[tuple[int, ...], int | None],
) -> int:
    left_release, left_post = left
    right_release, right_post = right
    width = max(len(left_release), len(right_release))
    left_key = left_release + (0,) * (width - len(left_release))
    right_key = right_release + (0,) * (width - len(right_release))
    if left_key != right_key:
        return -1 if left_key < right_key else 1
    left_post_key = -1 if left_post is None else left_post
    right_post_key = -1 if right_post is None else right_post
    return (left_post_key > right_post_key) - (left_post_key < right_post_key)


def _parse_specifier(
    specifier: str,
) -> tuple[tuple[str, str], ...]:
    if not specifier:
        return ()
    clauses = specifier.split(",")
    if any(not clause for clause in clauses):
        raise VerificationError(
            f"unsupported source version constraint: {specifier}"
        )
    parsed = []
    for clause in clauses:
        match = re.fullmatch(r"(==|!=|~=|>=|<=|>|<)([^,]+)", clause)
        if match is None:
            raise VerificationError(f"unsupported source version constraint: {clause}")
        expected_value = match.group(2)
        try:
            _parse_version(expected_value)
        except VerificationError as exc:
            canonical_alpha = (
                _CANONICAL_ALPHA_REQUIREMENT_OPERAND.fullmatch(expected_value)
                is not None
            )
            canonical_prefix_wildcard = (
                match.group(1) in {"==", "!="}
                and _CANONICAL_PREFIX_WILDCARD_REQUIREMENT_OPERAND.fullmatch(
                    expected_value
                )
                is not None
            )
            if not canonical_alpha and not canonical_prefix_wildcard:
                raise VerificationError(
                    f"unsupported source version constraint: {clause}"
                ) from exc
        parsed.append((match.group(1), expected_value))
    return tuple(parsed)


def _satisfies(version: str, specifier: str) -> bool:
    selected = _parse_version(version)
    for operator, expected_value in _parse_specifier(specifier):
        expected = _parse_version(expected_value)
        comparison = _compare_versions(selected, expected)
        outcomes = {
            "==": version == expected_value,
            "!=": version != expected_value,
            ">=": comparison >= 0,
            "<=": comparison <= 0,
            ">": comparison > 0,
            "<": comparison < 0,
        }
        if operator == "~=":
            selected_release = selected[0]
            expected_release = expected[0]
            upper_prefix = (
                expected_release[:-1]
                if len(expected_release) > 2
                else expected_release[:1]
            )
            outcomes[operator] = (
                comparison >= 0
                and selected_release[: len(upper_prefix)] == upper_prefix
            )
        if not outcomes[operator]:
            return False
    return True


def _marker_active(marker: str | None) -> bool:
    if marker is None:
        return True
    normalized = _normalized_marker(marker)
    if normalized in _marker_quote_variants("sys_platform != 'win32'"):
        return True
    if normalized in _marker_quote_variants(
        "sys_platform == 'win32'",
        "sys_platform == 'emscripten' or sys_platform == 'win32'",
    ):
        return False
    raise VerificationError(f"unsupported locked runtime marker: {marker}")


def _validate_runtime_closure(
    lock_packages: dict[str, dict[str, Any]],
    artifacts: dict[str, dict[str, Any]],
    direct: set[str],
    wheels: dict[str, Any],
) -> dict[str, list[str]]:
    closure = set(direct)
    pending = list(sorted(direct))
    active_edges: dict[str, list[str]] = {}
    while pending:
        name = pending.pop()
        package = lock_packages.get(name)
        if package is None:
            raise VerificationError(f"runtime dependency absent from uv.lock: {name}")
        dependencies = sorted(
            item["name"]
            for item in package.get("dependencies", [])
            if _marker_active(item.get("marker"))
        )
        active_edges[name] = dependencies
        for dependency in dependencies:
            if dependency not in closure:
                closure.add(dependency)
                pending.append(dependency)
    runtime_artifacts = {
        name for name, artifact in artifacts.items() if "runtime" in artifact["roles"]
    }
    if closure != runtime_artifacts or closure - direct != set(wheels["runtime_transitive"]):
        raise VerificationError("runtime dependency closure is not exact")
    for name in closure:
        artifact = artifacts[name]
        locked = lock_packages[name]
        if locked["version"] != artifact["version"]:
            raise VerificationError(f"runtime version drifted from uv.lock: {name}")
        expected_dependencies = [
            {"package": dependency, "version": lock_packages[dependency]["version"]}
            for dependency in active_edges[name]
        ]
        if artifact["active_dependencies"] != expected_dependencies:
            raise VerificationError(f"runtime dependency edges drifted: {name}")
        wheel_records = [
            item
            for item in locked.get("wheels", [])
            if item["url"].rsplit("/", 1)[-1] == artifact["filename"]
        ]
        if len(wheel_records) != 1:
            raise VerificationError(f"runtime wheel is not selected by uv.lock: {name}")
        locked_wheel = wheel_records[0]
        if (
            locked_wheel["hash"] != "sha256:" + artifact["sha256"]
            or locked_wheel["size"] != artifact["size"]
            or locked_wheel["url"] != artifact["url"]
        ):
            raise VerificationError(f"runtime wheel artifact drifted from uv.lock: {name}")
    return active_edges


def _verify_cargo_registry(
    registry: Path,
    cargo_lock: dict[str, Any],
    cargo_policy: dict[str, Any],
) -> str:
    packages = cargo_policy["packages"]
    if cargo_policy["package_count"] != len(packages):
        raise VerificationError("Cargo registry policy count drifted")
    locked = sorted(
        (
            item["name"],
            item["version"],
            item["source"],
            item["checksum"],
        )
        for item in cargo_lock["package"]
        if "source" in item and item["source"].startswith("registry+")
    )
    reviewed: list[tuple[str, str, str, str]] = []
    previous: tuple[str, str, str] | None = None
    expected_files: set[str] = set()
    records: list[dict[str, Any]] = []
    package_keys = {
        "checksum",
        "filename",
        "mode",
        "name",
        "sha256",
        "size",
        "source",
        "url",
        "version",
    }
    for package in packages:
        _require_keys(package, package_keys, "Cargo registry package")
        identity = (package["name"], package["version"], package["source"])
        filename = f"{package['name']}-{package['version']}.crate"
        if (
            previous is not None
            and identity <= previous
            or package["source"] != _CRATES_IO_SOURCE
            or package["filename"] != filename
            or package["url"] != f"https://static.crates.io/crates/{package['name']}/{filename}"
            or package["mode"] != "0400"
            or package["checksum"] != package["sha256"]
            or type(package["size"]) is not int
            or package["size"] <= 0
        ):
            raise VerificationError("Cargo registry package identity is invalid")
        _require_sha256(package["sha256"], f"Cargo package {filename}")
        previous = identity
        reviewed.append((*identity, package["checksum"]))
        expected_files.add(filename)
    if reviewed != locked:
        raise VerificationError("Cargo registry policy drifted from Cargo.lock")
    try:
        root_st = registry.lstat()
        observed_files = {item.name for item in registry.iterdir()}
    except OSError as exc:
        raise VerificationError(f"Cargo registry cache unavailable: {exc}") from exc
    if (
        not stat.S_ISDIR(root_st.st_mode)
        or registry.is_symlink()
        or _mode(root_st) != "0500"
        or root_st.st_uid != os.getuid()
        or observed_files != expected_files
    ):
        raise VerificationError("Cargo registry cache has missing or extra artifacts")
    by_filename = {package["filename"]: package for package in packages}
    for filename in sorted(expected_files):
        path = registry / filename
        package = by_filename[filename]
        try:
            st = path.lstat()
        except OSError as exc:
            raise VerificationError(f"Cargo artifact unavailable: {filename}") from exc
        digest = _sha256_file(path) if stat.S_ISREG(st.st_mode) else ""
        if (
            not stat.S_ISREG(st.st_mode)
            or path.is_symlink()
            or st.st_nlink != 1
            or st.st_uid != os.getuid()
            or _mode(st) != package["mode"]
            or st.st_size != package["size"]
            or digest != package["sha256"]
        ):
            raise VerificationError(f"Cargo artifact identity drifted: {filename}")
        records.append(
            {
                "mode": _mode(st),
                "path": filename,
                "sha256": digest,
                "size": st.st_size,
            }
        )
    return _sha256_bytes(_canonical_bytes(records)[:-1])


def _verify_source_derivation(
    cache: Path,
    engine: dict[str, Any],
    inputs: dict[str, Any],
    wheels: dict[str, Any],
    cargo_registry: dict[str, Any],
) -> dict[str, Any]:
    _verify_source_authority(inputs)
    source_record = inputs["source"]["artifact"]
    source = _source_members(cache / "source-inputs" / source_record["filename"], inputs)
    _verify_build_script_trace(
        source["build.py"],
        engine["native_build_environment"]["sealed_source_trace"]["build.py"],
    )
    try:
        pyproject = tomllib.loads(source["pyproject.toml"].decode("utf-8"))
        uv_lock = tomllib.loads(source["uv.lock"].decode("utf-8"))
        cargo_toml = tomllib.loads(source["Cargo.toml"].decode("utf-8"))
        cargo_lock = tomllib.loads(source["Cargo.lock"].decode("utf-8"))
        rust_toolchain = tomllib.loads(source["rust-toolchain.toml"].decode("utf-8"))
    except (UnicodeError, tomllib.TOMLDecodeError, KeyError) as exc:
        raise VerificationError(f"source build metadata is invalid: {exc}") from exc
    if (
        pyproject["project"]["name"] != "nautilus_trader"
        or pyproject["project"]["version"] != "1.231.0"
        or pyproject["project"]["requires-python"] != ">=3.12,<3.15"
    ):
        raise VerificationError("source Python project identity drifted")
    if (
        rust_toolchain["toolchain"]["channel"] != "1.97.1"
        or cargo_toml["workspace"]["package"]["rust-version"] != "1.97.1"
    ):
        raise VerificationError("source Rust toolchain requirement drifted")
    cargo_policy = inputs["rust"]["cargo_lock"]
    if (
        cargo_lock["version"] != cargo_policy["lock_version"]
        or len(cargo_lock["package"]) != cargo_policy["package_count"]
        or sum("checksum" in item for item in cargo_lock["package"])
        != cargo_policy["checksum_count"]
    ):
        raise VerificationError("Cargo.lock closure drifted")
    if (
        cargo_registry["cargo_lock_sha256"] != cargo_policy["sha256"]
        or cargo_registry["registry_source"] != _CRATES_IO_SOURCE
    ):
        raise VerificationError("Cargo registry lock authority drifted")
    cargo_tree_hash = _verify_cargo_registry(
        cache / cargo_registry["cache_directory"], cargo_lock, cargo_registry
    )

    artifacts = {item["package"]: item for item in wheels["wheel_artifacts"]}
    lock_packages = {item["name"]: item for item in uv_lock["package"]}
    build_requirements = pyproject["build-system"]["requires"]
    source_build_names: set[str] = set()
    for requirement in build_requirements:
        name, specifier, marker = _parse_requirement(requirement)
        if marker is not None or name not in artifacts or "build" not in artifacts[name]["roles"]:
            raise VerificationError("source build dependency is not exactly admitted")
        if not _satisfies(artifacts[name]["version"], specifier):
            raise VerificationError(f"selected build dependency violates source: {name}")
        source_build_names.add(name)
    if source_build_names != {"cython", "numpy", "poetry-core", "setuptools"}:
        raise VerificationError("source build dependency set drifted")
    imports = set()
    for node in ast.walk(ast.parse(source["build.py"], filename="build.py")):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
    if not {"Cython", "numpy", "packaging", "setuptools"}.issubset(imports):
        raise VerificationError("build.py import-derived dependency set drifted")
    if set(wheels["build_closure"]) != {
        "cython",
        "numpy",
        "packaging",
        "pip",
        "poetry-core",
        "setuptools",
    }:
        raise VerificationError("build wheel closure drifted")
    bootstrap = wheels["bootstrap_exception"]
    if (
        bootstrap["package"] != "pip"
        or bootstrap["version"] != artifacts["pip"]["version"]
        or bootstrap["source_classification"]
        != "SEPARATELY_REVIEWED_LOCAL_BUILD_BOOTSTRAP_NOT_UV_LOCK_AUTHORITY"
        or artifacts["pip"]["source_classification"]
        != bootstrap["source_classification"]
    ):
        raise VerificationError("pip bootstrap authority is ambiguous")
    for name in {"cython", "numpy", "packaging", "setuptools"}:
        if lock_packages[name]["version"] != artifacts[name]["version"]:
            raise VerificationError(f"build wheel version is not uv.lock exact: {name}")

    direct_requirements = [
        _parse_requirement(value) for value in pyproject["project"]["dependencies"]
    ]
    direct = {name for name, _, marker in direct_requirements if _marker_active(marker)}
    if direct != set(wheels["runtime_direct"]):
        raise VerificationError("runtime direct dependency set drifted")
    for name, specifier, marker in direct_requirements:
        if _marker_active(marker) and not _satisfies(artifacts[name]["version"], specifier):
            raise VerificationError(f"runtime wheel violates source requirement: {name}")
    project_lock = lock_packages["nautilus-trader"]
    locked_direct = {
        item["name"] for item in project_lock["dependencies"] if _marker_active(item.get("marker"))
    }
    if locked_direct != direct:
        raise VerificationError("uv.lock direct runtime dependency set drifted")

    active_edges = _validate_runtime_closure(lock_packages, artifacts, direct, wheels)
    for name, artifact in artifacts.items():
        if artifact["source_classification"] in {
            "SEPARATELY_REVIEWED_LOCAL_BUILD_BOOTSTRAP_NOT_UV_LOCK_AUTHORITY",
            "UPSTREAM_BUILD_SYSTEM_EXACT_PIN",
        }:
            continue
        locked = lock_packages.get(name)
        if locked is None or locked["version"] != artifact["version"]:
            raise VerificationError(f"wheel lacks exact uv.lock authority: {name}")
        matches = [
            item for item in locked.get("wheels", [])
            if item["url"].rsplit("/", 1)[-1] == artifact["filename"]
        ]
        if len(matches) != 1 or matches[0]["hash"] != "sha256:" + artifact["sha256"]:
            raise VerificationError(f"wheel bytes lack exact uv.lock authority: {name}")
    return {
        "cargo_lock_package_count": len(cargo_lock["package"]),
        "cargo_registry_package_count": cargo_registry["package_count"],
        "cargo_registry_tree_sha256": cargo_tree_hash,
        "runtime_dependency_edges": active_edges,
        "uv_lock_package_count": len(uv_lock["package"]),
        "uv_lock_revision": uv_lock["revision"],
    }


def _verify_rust_manifest(cache: Path, inputs: dict[str, Any]) -> None:
    rust = inputs["rust"]
    record = rust["channel_manifest"]
    try:
        manifest = tomllib.loads(
            (cache / "rust-inputs" / record["filename"]).read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise VerificationError(f"Rust channel manifest is invalid: {exc}") from exc
    if manifest["date"] != record["date"] or manifest["manifest-version"] != "2":
        raise VerificationError("Rust channel manifest identity drifted")
    target = rust["target"]
    for component in rust["components"]:
        package = manifest["pkg"][component["name"]]["target"][target]
        if (
            package["available"] is not True
            or package["xz_url"] != component["url"]
            or package["xz_hash"] != component["sha256"]
        ):
            raise VerificationError(f"Rust channel component drifted: {component['name']}")


def _verify_rust_router_sources(cache: Path, engine: dict[str, Any]) -> None:
    entries = {item["name"]: item for item in engine["command_router"]["entries"]}
    for name in ("cargo", "rustc"):
        entry = entries[name]
        resolved = entry["exec_target"] if name == "cargo" else entry["resolved"]
        authority = (
            resolved["source_authority"]
            if name == "cargo"
            else entry["source_authority"]
        )
        archive_path = cache / "rust-inputs" / authority["archive_filename"]
        try:
            with tarfile.open(archive_path, "r:xz") as archive:
                members = [
                    member
                    for member in archive.getmembers()
                    if member.name == authority["archive_member"]
                ]
                if (
                    len(members) != 1
                    or not members[0].isfile()
                    or members[0].issym()
                    or members[0].islnk()
                    or members[0].mode & 0o777 != 0o755
                    or members[0].size != resolved["size"]
                ):
                    raise VerificationError(
                        f"Rust command-router source member is invalid: {name}"
                    )
                source = archive.extractfile(members[0])
                if source is None:
                    raise VerificationError(
                        f"Rust command-router source member is unreadable: {name}"
                    )
                digest = hashlib.sha256()
                size = 0
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(block)
                    size += len(block)
                if size != resolved["size"] or digest.hexdigest() != resolved["sha256"]:
                    raise VerificationError(
                        f"Rust command-router source member drifted: {name}"
                    )
        except (OSError, tarfile.TarError) as exc:
            raise VerificationError(
                f"Rust command-router source archive is invalid: {name}: {exc}"
            ) from exc


def _wheel_path(name: str) -> str:
    raw = name.rstrip("/")
    if (
        not raw
        or "\\" in raw
        or raw.startswith("/")
        or re.match(r"^[A-Za-z]:", raw)
        or "//" in raw
        or "\x00" in raw
        or any(part in {"", ".", ".."} for part in raw.split("/"))
    ):
        raise VerificationError("unsafe wheel path")
    return raw


def _register_wheel_path(path: str, nodes: dict[str, tuple[str, str]]) -> None:
    key = unicodedata.normalize("NFC", path).casefold()
    parts = path.split("/")
    parents = ["/".join(parts[:index]) for index in range(1, len(parts))]
    if key in nodes:
        raise VerificationError("wheel path collision")
    for parent in parents:
        prior = nodes.get(unicodedata.normalize("NFC", parent).casefold())
        if prior is not None and prior != ("directory", parent):
            raise VerificationError("wheel path collision")
    nodes[key] = ("file", path)
    for parent in parents:
        nodes.setdefault(
            unicodedata.normalize("NFC", parent).casefold(), ("directory", parent)
        )


def _register_wheel_directory(
    path: str,
    nodes: dict[str, tuple[str, str]],
    explicit: set[str],
) -> None:
    key = unicodedata.normalize("NFC", path).casefold()
    parts = path.split("/")
    parents = ["/".join(parts[:index]) for index in range(1, len(parts))]
    prior = nodes.get(key)
    if key in explicit or (prior is not None and prior != ("directory", path)):
        raise VerificationError("wheel path collision")
    for parent in parents:
        parent_key = unicodedata.normalize("NFC", parent).casefold()
        prior = nodes.get(parent_key)
        if prior is not None and prior != ("directory", parent):
            raise VerificationError("wheel path collision")
        nodes.setdefault(parent_key, ("directory", parent))
    nodes.setdefault(key, ("directory", path))
    explicit.add(key)


def _wheel_filename_identity(
    artifact: dict[str, Any]
) -> tuple[set[str], str]:
    filename = artifact["filename"]
    if not filename.endswith(".whl"):
        raise VerificationError("wheel filename is invalid")
    parts = filename[:-4].rsplit("-", 3)
    expected_prefix = (
        re.sub(r"[-_.]+", "_", artifact["package"])
        + "-"
        + artifact["version"].replace("-", "_")
    )
    if len(parts) != 4 or parts[0] != expected_prefix:
        raise VerificationError("wheel filename does not match canonical package identity")
    tags = {
        f"{python}-{abi}-{platform}"
        for python in parts[1].split(".")
        for abi in parts[2].split(".")
        for platform in parts[3].split(".")
    }
    return tags, expected_prefix + ".dist-info"


def _marker_quote_variants(*markers: str) -> set[str]:
    return {
        variant
        for marker in markers
        for variant in (marker, marker.replace("'", '"'))
    }


def _normalized_marker(marker: str) -> str:
    normalized = re.sub(r"\s+", " ", marker.strip())
    if "'" in normalized and '"' in normalized:
        raise VerificationError(f"unsupported marker quote delimiters: {marker}")
    return normalized


def _extra_marker(marker: str, operator: str) -> bool:
    return re.fullmatch(
        rf"extra\s*{re.escape(operator)}\s*(?:'{_REQUIREMENT_TOKEN}'|\"{_REQUIREMENT_TOKEN}\")",
        marker,
    ) is not None


def _metadata_marker_active(marker: str | None) -> bool:
    if marker is None:
        return True
    normalized = _normalized_marker(marker)
    if re.search(r"\bextra\b", normalized):
        inactive_extra = _extra_marker(normalized, "==")
        prefix, separator, extra = normalized.rpartition(" and ")
        admitted_prefixes = _marker_quote_variants(
            "(python_version < '3.10')",
            "(python_version < '3.14')",
            "(python_version >= '3.9' and sys_platform != 'cygwin')",
            "(sys_platform != 'cygwin')",
            "platform_python_implementation != 'PyPy'",
            "platform_python_implementation == 'PyPy'",
            "python_version < '3.10'",
            "python_version < '3.11'",
            "sys_platform != 'cygwin'",
        )
        if inactive_extra or (
            separator
            and prefix in admitted_prefixes
            and _extra_marker(extra, "==")
        ):
            return False
        if _extra_marker(normalized, "!="):
            return True
        raise VerificationError(f"unsupported active wheel marker: {marker}")
    outcomes = {
        variant: outcome
        for marker, outcome in (
            ("platform_system == 'Windows'", False),
            ("python_version < '3.14'", True),
            ("python_version >= '3.14'", False),
            ("sys_platform != 'win32'", True),
            ("sys_platform == 'win32'", False),
            ("sys_platform == 'emscripten'", False),
        )
        for variant in _marker_quote_variants(marker)
    }
    if normalized in outcomes:
        return outcomes[normalized]
    raise VerificationError(f"unsupported active wheel marker: {marker}")


def _candidate_wheel_requires_dist(engine: dict[str, Any]) -> tuple[str, ...]:
    authority = engine.get("candidate_wheel_metadata")
    if not isinstance(authority, dict):
        raise VerificationError("candidate wheel metadata authority is invalid")
    _require_keys(
        authority,
        {"authority", "requires_dist", "source_correspondence"},
        "candidate wheel metadata authority",
    )
    source = authority["source_correspondence"]
    if not isinstance(source, dict):
        raise VerificationError("candidate wheel metadata source authority is invalid")
    _require_keys(
        source,
        {
            "generator",
            "generator_module_path",
            "generator_module_sha256",
            "project_path",
            "project_sha256",
            "upstream_commit",
        },
        "candidate wheel metadata source authority",
    )
    requirements = authority["requires_dist"]
    if (
        authority["authority"] != "EXACT_ORDERED_REQUIRES_DIST_V1"
        or source["generator"] != "poetry-core 2.3.1"
        or source["generator_module_path"] != "poetry/core/masonry/metadata.py"
        or source["project_path"] != "pyproject.toml"
        or source["upstream_commit"]
        != "27a8e54e7ac3c57d6cbf8891f0283dfbaee97317"
        or not isinstance(requirements, list)
        or len(requirements) != 20
        or not all(
            isinstance(value, str)
            and value
            and "[" not in value
            and "]" not in value
            for value in requirements
        )
        or len(set(requirements)) != 20
    ):
        raise VerificationError("candidate wheel metadata authority is invalid")
    _require_sha256(source["generator_module_sha256"], "metadata generator module")
    _require_sha256(source["project_sha256"], "metadata project source")
    for requirement in requirements:
        _parse_requirement(requirement)
    return tuple(requirements)


def _verify_wheel_record(
    archive: zipfile.ZipFile,
    infos: dict[str, zipfile.ZipInfo],
    record_path: str,
) -> None:
    try:
        record_text = archive.read(record_path).decode("utf-8")
        rows = list(csv.reader(io.StringIO(record_text, newline="")))
    except (UnicodeError, csv.Error) as exc:
        raise VerificationError("wheel RECORD is invalid") from exc
    recorded: dict[str, tuple[str, str]] = {}
    nodes: dict[str, tuple[str, str]] = {}
    for row in rows:
        if len(row) != 3:
            raise VerificationError("wheel RECORD row is invalid")
        name = _wheel_path(row[0])
        _register_wheel_path(name, nodes)
        if name in recorded:
            raise VerificationError("wheel RECORD contains a duplicate path")
        recorded[name] = (row[1], row[2])
    if set(recorded) != set(infos):
        raise VerificationError("wheel RECORD set is not exact")
    for name, info in infos.items():
        encoded_hash, encoded_size = recorded[name]
        if name == record_path:
            if encoded_hash or encoded_size:
                raise VerificationError("wheel RECORD self-row is invalid")
            continue
        if not encoded_hash.startswith("sha256=") or not encoded_size.isdecimal():
            raise VerificationError("wheel RECORD hash or size is invalid")
        digest = hashlib.sha256()
        size = 0
        with archive.open(info) as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
                size += len(block)
        expected_hash = base64.urlsafe_b64encode(digest.digest()).rstrip(b"=").decode()
        if encoded_hash != "sha256=" + expected_hash or int(encoded_size) != size:
            raise VerificationError("wheel RECORD hash or size drifted")


def _verify_wheel_metadata(cache: Path, wheels: dict[str, Any]) -> None:
    for artifact in wheels["wheel_artifacts"]:
        version = artifact.get("version")
        if not isinstance(version, str):
            raise VerificationError("wheel version is invalid")
        _parse_version(version)
        filename_tags, expected_dist_info = _wheel_filename_identity(artifact)
        path = cache / "wheels" / artifact["filename"]
        try:
            with zipfile.ZipFile(path) as archive:
                archive_infos = archive.infolist()
                if not archive_infos or len(archive_infos) > _MAX_ARCHIVE_MEMBERS:
                    raise VerificationError("wheel member count is invalid")
                nodes: dict[str, tuple[str, str]] = {}
                explicit_directories: set[str] = set()
                dist_info_roots: set[str] = set()
                infos: dict[str, zipfile.ZipInfo] = {}
                total = 0
                for info in archive_infos:
                    name = _wheel_path(info.filename)
                    top_level = unicodedata.normalize("NFC", name.split("/", 1)[0])
                    if top_level.casefold().endswith(".dist-info"):
                        dist_info_roots.add(top_level)
                    mode = (info.external_attr >> 16) & 0xFFFF
                    file_type = stat.S_IFMT(mode)
                    if info.is_dir():
                        if file_type not in {0, stat.S_IFDIR} or info.file_size != 0:
                            raise VerificationError("non-regular wheel member")
                        _register_wheel_directory(name, nodes, explicit_directories)
                        continue
                    _register_wheel_path(name, nodes)
                    if stat.S_ISLNK(mode) or file_type not in {0, stat.S_IFREG}:
                        raise VerificationError("non-regular wheel member")
                    if info.flag_bits & 0x1:
                        raise VerificationError("encrypted wheel member")
                    if info.file_size < 0 or info.file_size > _MAX_MEMBER_BYTES:
                        raise VerificationError("wheel member size is invalid")
                    total += info.file_size
                    if total > _MAX_TOTAL_BYTES:
                        raise VerificationError("wheel expanded size is invalid")
                    infos[name] = info
                if dist_info_roots != {expected_dist_info}:
                    raise VerificationError(
                        f"wheel identity metadata root set is invalid: {path.name}"
                    )
                metadata_names = [
                    name for name in infos
                    if name.endswith(".dist-info/METADATA") and name.count("/") == 1
                ]
                wheel_names = [
                    name for name in infos
                    if name.endswith(".dist-info/WHEEL") and name.count("/") == 1
                ]
                record_names = [
                    name for name in infos
                    if name.endswith(".dist-info/RECORD") and name.count("/") == 1
                ]
                if (
                    len(metadata_names) != 1
                    or len(wheel_names) != 1
                    or len(record_names) != 1
                    or len({name.split("/", 1)[0] for name in metadata_names + wheel_names + record_names}) != 1
                    or metadata_names[0].split("/", 1)[0] != expected_dist_info
                ):
                    raise VerificationError(f"wheel identity metadata layout is invalid: {path.name}")
                _verify_wheel_record(archive, infos, record_names[0])
                metadata = BytesParser(policy=compat32).parsebytes(
                    archive.read(metadata_names[0])
                )
                wheel_metadata = BytesParser(policy=compat32).parsebytes(
                    archive.read(wheel_names[0])
                )
        except (OSError, zipfile.BadZipFile) as exc:
            raise VerificationError(f"invalid wheel {path.name}: {exc}") from exc
        names = metadata.get_all("Name", [])
        versions = metadata.get_all("Version", [])
        if len(names) != 1 or len(versions) != 1:
            raise VerificationError(
                f"wheel identity headers are not singletons: {path.name}"
            )
        tags = [str(value) for value in wheel_metadata.get_all("Tag", [])]
        if (
            _normalize(str(names[0])) != artifact["package"]
            or str(versions[0]) != artifact["version"]
            or tags != artifact["tags"]
        ):
            raise VerificationError(f"wheel name, version, or tags drifted: {path.name}")
        if filename_tags != set(tags):
            raise VerificationError(f"wheel filename tags drifted: {path.name}")
        if not all(
            tag in {"py2-none-any", "py3-none-any"}
            or (tag.startswith("cp312-cp312-") and "x86_64" in tag and "manylinux" in tag)
            for tag in tags
        ):
            raise VerificationError(f"wheel has a foreign Python/platform tag: {path.name}")
        expected_dependencies = {
            item["package"]: item["version"] for item in artifact["active_dependencies"]
        }
        observed_dependencies: dict[str, str] = {}
        for requirement in metadata.get_all("Requires-Dist", []):
            name, specifier, marker = _parse_requirement(str(requirement))
            if not _metadata_marker_active(marker):
                continue
            if name in observed_dependencies or name not in expected_dependencies:
                raise VerificationError(f"wheel active Requires-Dist drifted: {path.name}")
            version = expected_dependencies[name]
            if specifier and not _satisfies(version, specifier):
                raise VerificationError(f"wheel active Requires-Dist pin is incompatible: {path.name}")
            observed_dependencies[name] = version
        if observed_dependencies != expected_dependencies:
            raise VerificationError(f"wheel active Requires-Dist set drifted: {path.name}")


def generate(evidence_cache: Path) -> dict[str, Any]:
    engine = load_json(ENGINE_POLICY)
    inputs = load_json(INPUT_POLICY)
    wheels = load_json(WHEEL_POLICY)
    cargo = load_json(CARGO_POLICY)
    _verify_policies(engine, inputs, wheels, cargo)
    expected_cache = engine["external_cache_isolation"]["external_roots"][
        "candidate_input_root"
    ]
    if str(evidence_cache) != expected_cache:
        raise VerificationError("evidence cache is not the reviewed candidate input root")
    _, tree_hash = _verify_cache(evidence_cache, inputs, wheels, cargo)
    _verify_rust_manifest(evidence_cache, inputs)
    _verify_rust_router_sources(evidence_cache, engine)
    derivation = _verify_source_derivation(
        evidence_cache, engine, inputs, wheels, cargo
    )
    _verify_wheel_metadata(evidence_cache, wheels)
    policy_hashes = {
        "engine_build_policy_sha256": _sha256_file(ENGINE_POLICY),
        "cargo_registry_policy_sha256": _sha256_file(CARGO_POLICY),
        "generator_sha256": _sha256_file(Path(__file__)),
        "input_cache_policy_sha256": _sha256_file(INPUT_POLICY),
        "llvm_toolchain_policy_sha256": engine["llvm_toolchain"]["policy_sha256"],
        "llvm_toolchain_validator_sha256": engine["llvm_toolchain"]["validator_sha256"],
        "release_provenance_policy_sha256": inputs["provenance_authority"]["policy_sha256"],
        "release_provenance_verifier_sha256": inputs["provenance_authority"]["verifier_sha256"],
        "wheel_cache_policy_sha256": _sha256_file(WHEEL_POLICY),
    }
    return {
        "build_wheels": [
            artifact
            for artifact in wheels["wheel_artifacts"]
            if "build" in artifact["roles"]
        ],
        "cache_tree_sha256": tree_hash,
        "candidate": engine["candidate"],
        "command_router": engine["command_router"],
        "derivation": derivation,
        "external_cache_isolation": engine["external_cache_isolation"],
        "manifest_kind": "NAUTILUS_V1_231_DETERMINISTIC_TOOLCHAIN_INPUTS",
        "native_entry_guard": engine["native_entry_guard"],
        "native_build_authority": engine["native_build_authority"],
        "native_build_environment": engine["native_build_environment"],
        "network": "DISABLED_BY_CONSTRUCTION",
        "policy_hashes": policy_hashes,
        "python": engine["python"],
        "runtime_wheels": [
            artifact
            for artifact in wheels["wheel_artifacts"]
            if "runtime" in artifact["roles"]
        ],
        "rust": inputs["rust"],
        "cargo_registry": {
            "cache_directory": cargo["cache_directory"],
            "offline_cargo_config": cargo["offline_cargo_config"],
            "package_count": cargo["package_count"],
            "registry_source": cargo["registry_source"],
            "vendor_materialization": cargo["vendor_materialization"],
        },
        "schema_version": 1,
        "source": inputs["source"],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-cache", type=Path)
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--output", type=Path)
    output.add_argument("--check", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.evidence_cache is None:
        print(
            "NAUTILUS_TOOLCHAIN_INPUTS=DEFERRED reason=evidence-cache-not-supplied"
        )
        return 3
    try:
        document = generate(args.evidence_cache)
        encoded = _canonical_bytes(document, pretty=True)
        if args.output is not None:
            if args.output.exists() or args.output.is_symlink():
                raise VerificationError("output path must be explicitly absent")
            with args.output.open("xb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            print(
                "NAUTILUS_TOOLCHAIN_INPUTS=PASS "
                f"sha256={_sha256_bytes(encoded)} output={args.output}"
            )
        elif args.check is not None:
            try:
                observed = args.check.read_bytes()
            except OSError as exc:
                raise VerificationError(f"checked manifest unavailable: {exc}") from exc
            if observed != encoded:
                raise VerificationError("checked manifest is not byte-identical")
            print(
                "NAUTILUS_TOOLCHAIN_INPUTS=PASS "
                f"sha256={_sha256_bytes(encoded)} checked={args.check}"
            )
        else:
            sys.stdout.buffer.write(encoded)
    except VerificationError as exc:
        print(f"NAUTILUS_TOOLCHAIN_INPUTS=FAIL reason={exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
