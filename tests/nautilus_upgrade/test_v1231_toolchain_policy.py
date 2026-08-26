from __future__ import annotations

import ast
import base64
import copy
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from typing import Any
import zipfile

import pytest

import scripts.write_nautilus_toolchain_inputs as toolchain


ROOT = Path(__file__).resolve().parents[2]
GENERATOR = ROOT / "scripts/write_nautilus_toolchain_inputs.py"
CANDIDATE = ROOT / "engines/nautilus/candidates/v1.231"
ENGINE_POLICY = CANDIDATE / "engine-build-policy.json"
INPUT_POLICY = CANDIDATE / "input-cache-policy.json"
WHEEL_POLICY = CANDIDATE / "wheel-cache-policy.json"
CARGO_POLICY = CANDIDATE / "cargo-registry-policy.json"
MANIFEST = CANDIDATE / "toolchain-inputs.json"
CACHE_ENV = "P1_U03_TOOLCHAIN_CACHE"
_REVIEWED_CANDIDATE_PATH_SHA256 = {
    "explicit_wheel_root": (
        "e4a11ba5c0e583e6537cfcb282d7913a49418d81b554c9d4694a72ff86257886"
    ),
    "offline_cargo_config": (
        "b54f157d4d5fd19cab80fe1227859ee413a41538a5948b8bd85bd55b6c99f0c9"
    ),
    "forensic_root": (
        "3b3b612ed34a92d2854abf131408dfe7ea51d25ddf27e401ce24930963536a31"
    ),
}


@pytest.fixture
def verified_source_fd(tmp_path: Path) -> int:
    source = tmp_path / "verified-source"
    source.mkdir()
    descriptor = os.open(
        source, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW
    )
    try:
        yield descriptor
    finally:
        os.close(descriptor)


def _build_environment(
    policy: dict[str, Any],
    inherited: dict[str, str],
    stage: Path,
    verified_source_fd: int,
    **kwargs: dict[str, str],
) -> dict[str, dict[str, str]]:
    return toolchain._verify_build_environment(
        policy,
        inherited,
        stage,
        verified_source_fd=verified_source_fd,
        mount_destinations=[stage],
        **kwargs,
    )


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="ascii",
    )


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _assert_reviewed_candidate_path_identities(
    engine: dict[str, Any], cargo: dict[str, Any]
) -> None:
    roots = engine["external_cache_isolation"]["external_roots"]
    actual = {
        "explicit_wheel_root": engine["python"][
            "explicit_build_path_admission"
        ]["root"],
        "offline_cargo_config": cargo["offline_cargo_config"]["contents"],
        "forensic_root": roots["candidate_forensic_root"],
    }

    assert {
        name: _sha256(str(value).encode("ascii"))
        for name, value in actual.items()
    } == _REVIEWED_CANDIDATE_PATH_SHA256


def test_reviewed_path_identity_rejects_coordinated_policy_drift() -> None:
    engine = copy.deepcopy(toolchain.load_json(ENGINE_POLICY))
    cargo = copy.deepcopy(toolchain.load_json(CARGO_POLICY))
    roots = engine["external_cache_isolation"]["external_roots"]
    roots["candidate_input_root"] = "/tmp/drift-input"
    engine["python"]["explicit_build_path_admission"]["root"] = (
        "/tmp/drift-input/wheels"
    )
    roots["candidate_vendor_root"] = "/tmp/drift-vendor"
    cargo["offline_cargo_config"]["contents"] = (
        "[net]\noffline = true\n\n[source.crates-io]\n"
        'replace-with = "candidate-vendor"\n\n[source.candidate-vendor]\n'
        'directory = "/tmp/drift-vendor"\n'
    )
    roots["candidate_build_root"] = "/tmp/drift-build"
    roots["candidate_forensic_root"] = (
        "/tmp/nautilus-v1.231-reproducibility-evidence"
    )
    checker = globals().get("_assert_reviewed_candidate_path_identities")

    assert callable(checker), "independent reviewed-path verifier is missing"
    with pytest.raises(AssertionError):
        checker(engine, cargo)


def test_missing_external_evidence_is_explicitly_deferred() -> None:
    result = subprocess.run(
        [sys.executable, str(GENERATOR)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 3
    assert result.stdout == (
        "NAUTILUS_TOOLCHAIN_INPUTS=DEFERRED "
        "reason=evidence-cache-not-supplied\n"
    )
    assert result.stderr == ""


def test_candidate_policy_is_exact_source_derived_and_isolated() -> None:
    engine = toolchain.load_json(ENGINE_POLICY)
    inputs = toolchain.load_json(INPUT_POLICY)
    wheels = toolchain.load_json(WHEEL_POLICY)
    cargo = toolchain.load_json(CARGO_POLICY)
    _assert_reviewed_candidate_path_identities(engine, cargo)

    assert engine["candidate"] == {
        "release": "1.231.0",
        "upstream_commit": "27a8e54e7ac3c57d6cbf8891f0283dfbaee97317",
        "upstream_tag": "v1.231.0",
    }
    assert engine["rust"]["rustc_version"] == "1.97.1"
    assert engine["rust"]["cargo_version"] == "1.97.1"
    assert inputs["rust"]["version"] == "1.97.1"
    assert engine["python"]["identity"] == "CPython 3.12.3"
    assert engine["python"]["abi"] == "cp312"
    assert engine["python"]["executable_sha256"] == (
        "1643dacd9feaedc58f3cc581e4d22577dfe25c09b10282936186ccf0f2e61118"
    )
    assert engine["python"]["stdlib_inventory"]["record_count"] == 1308
    assert engine["python"]["stdlib_inventory"]["tree_sha256"] == (
        "0c17594ac603d6ba61b6b25c25f7f4e748fecb3362741bf191e717f36a39522e"
    )
    assert engine["llvm_toolchain"] == {
        "candidate_materialization": "SEPARATE_ROOT_REQUIRED",
        "consumption": "IMMUTABLE_READ_ONLY_POLICY_REFERENCE",
        "policy_path": "engines/nautilus/llvm-toolchain-policy.json",
        "policy_sha256": "7ce6888a582343edc823780485f942c7627f60ce9b37e497c7ce03f403e8d56f",
        "shared_writable_cache": "PROHIBITED",
        "validator_path": "scripts/prepare_nautilus_llvm_toolchain.py",
        "validator_sha256": "71f794bb1c5f70ec427ff33c21f27debd066fcf76567d07241170da5527a41c2",
        "version": "22.1.3",
    }
    assert engine["native_entry_guard"]["status"] == (
        "UNCHANGED_SEPARATE_REVIEWED_V1_227_TOOLCHAIN"
    )
    isolation = engine["external_cache_isolation"]
    assert isolation["candidate_namespace"] != isolation["rollback_namespace"]
    assert isolation["shared_writable_paths"] == "PROHIBITED"
    roots = isolation["external_roots"]
    assert all(Path(value).is_absolute() for value in roots.values())
    assert len(set(roots.values())) == len(roots)

    assert engine["python"]["startup_argv"] == ["/usr/bin/python3.12", "-I", "-S"]
    assert engine["python"]["admitted_sys_path"] == [
        "/usr/lib/python312.zip",
        "/usr/lib/python3.12",
        "/usr/lib/python3.12/lib-dynload",
    ]
    assert engine["python"]["excluded_startup_paths"] == [
        "/etc/python3.12/sitecustomize.py",
        "/usr/lib/python3/dist-packages",
        "/usr/lib/python3.12/site-packages",
        "/usr/local/lib/python3.12/dist-packages",
        "/usr/local/lib/python3.12/site-packages",
    ]
    assert engine["python"]["explicit_build_path_admission"] == {
        "authority": "EXACT_WHEEL_CACHE_POLICY_FILENAMES_ONLY",
        "environment_pythonpath": "PROHIBITED",
        "injection": "EXPLICIT_SYS_PATH_PREPEND_BEFORE_IMPORT",
        "root": str(Path(roots["candidate_input_root"]) / "wheels"),
    }
    native = engine["native_build_authority"]
    assert native["authority"] == "P1_U04_IMMUTABLE_NATIVE_AUTHORITY_SNAPSHOT_V1"
    destinations = [item["destination"] for item in native["snapshot"]["mappings"]]
    assert "/usr/include" in destinations
    assert "/usr/bin/strip" in destinations
    build_environment = engine["native_build_environment"]
    assert build_environment["route"] == "AWS_LC_SOURCE_DIRECT_CC"
    assert build_environment["construction"] == "EMPTY_THEN_SET_EXACT_VALUES"
    assert build_environment["inherited_allowlist"] == []

    artifacts = wheels["wheel_artifacts"]
    assert len(artifacts) == 19
    assert len({item["package"] for item in artifacts}) == 19
    assert len({item["filename"] for item in artifacts}) == 19
    assert {item["package"] for item in artifacts if "build" in item["roles"]} == {
        "cython",
        "numpy",
        "packaging",
        "pip",
        "poetry-core",
        "setuptools",
    }
    assert {item["version"] for item in artifacts if item["package"] == "cython"} == {
        "3.2.9"
    }
    assert {item["version"] for item in artifacts if item["package"] == "poetry-core"} == {
        "2.3.1"
    }
    assert {item["version"] for item in artifacts if item["package"] == "setuptools"} == {
        "83.0.0"
    }
    assert {item["version"] for item in artifacts if item["package"] == "numpy"} == {
        "2.5.1"
    }
    assert {item["version"] for item in artifacts if item["package"] == "packaging"} == {
        "26.2"
    }
    assert wheels["bootstrap_exception"] == {
        "package": "pip",
        "reason": "U04_REQUIRES_AN_EXACT_LOCAL_INSTALLER_BEFORE_BUILD_BACKEND_SETUP",
        "source_classification": (
            "SEPARATELY_REVIEWED_LOCAL_BUILD_BOOTSTRAP_NOT_UV_LOCK_AUTHORITY"
        ),
        "version": "26.1",
    }
    for artifact in artifacts:
        assert artifact["mode"] == "0400"
        assert re_full_sha256(artifact["sha256"])
        assert artifact["size"] > 0
        assert artifact["filename"].endswith(".whl")
        assert artifact["url"].endswith("/" + artifact["filename"])


def test_cargo_registry_policy_is_exact_offline_lock_closure() -> None:
    cargo = toolchain.load_json(CARGO_POLICY)
    engine = toolchain.load_json(ENGINE_POLICY)
    _assert_reviewed_candidate_path_identities(engine, cargo)
    vendor_root = engine["external_cache_isolation"]["external_roots"][
        "candidate_vendor_root"
    ]
    assert cargo["package_count"] == 862
    assert len(cargo["packages"]) == 862
    assert len({(item["name"], item["version"], item["source"]) for item in cargo["packages"]}) == 862
    assert all(item["checksum"] == item["sha256"] for item in cargo["packages"])
    assert all(item["mode"] == "0400" and item["size"] > 0 for item in cargo["packages"])
    assert cargo["offline_cargo_config"]["contents"] == (
        '[net]\noffline = true\n\n[source.crates-io]\nreplace-with = "candidate-vendor"\n\n'
        '[source.candidate-vendor]\ndirectory = '
        f'"{vendor_root}"\n'
    )


def re_full_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def test_all_candidate_json_is_canonical_and_duplicate_keys_fail_closed(
    tmp_path: Path,
) -> None:
    for path in sorted(CANDIDATE.glob("*.json")):
        document = toolchain.load_json(path)
        assert path.read_bytes() == toolchain._canonical_bytes(document, pretty=True)

    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"outer":{"key":1,"key":2}}\n', encoding="ascii")
    with pytest.raises(toolchain.VerificationError, match="duplicate JSON key"):
        toolchain.load_json(duplicate)


def test_generator_is_stdlib_only_and_has_no_network_or_process_path() -> None:
    tree = ast.parse(GENERATOR.read_text(encoding="utf-8"), filename=str(GENERATOR))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported.isdisjoint(
        {"httpx", "pip", "requests", "socket", "subprocess", "urllib", "uv"}
    )


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ("range", "exact reviewed identity"),
        ("latest", "reviewed schema"),
        ("root_collision", "namespace overlap"),
        ("network", "offline exact build"),
        ("index", "closed and offline"),
        ("active_policy", "reviewed file identity drifted"),
    ),
)
def test_range_fallback_root_collision_or_active_policy_drift_fails_closed(
    mutation: str, message: str
) -> None:
    engine = copy.deepcopy(toolchain.load_json(ENGINE_POLICY))
    inputs = copy.deepcopy(toolchain.load_json(INPUT_POLICY))
    wheels = copy.deepcopy(toolchain.load_json(WHEEL_POLICY))
    cargo = copy.deepcopy(toolchain.load_json(CARGO_POLICY))
    if mutation == "range":
        wheels["wheel_artifacts"][0]["version"] = ">=8.4.2"
    elif mutation == "latest":
        wheels["latest"] = True
    elif mutation == "root_collision":
        engine["external_cache_isolation"]["rollback_namespace"] = engine[
            "external_cache_isolation"
        ]["candidate_namespace"]
    elif mutation == "network":
        engine["build_execution"]["allow_network"] = True
    elif mutation == "index":
        wheels["resolution"]["allow_index_fallback"] = True
    else:
        engine["llvm_toolchain"]["policy_sha256"] = "0" * 64
    with pytest.raises(toolchain.VerificationError, match=message):
        toolchain._verify_policies(engine, inputs, wheels, cargo)


def test_source_artifact_must_equal_u02_primary_before_member_derivation() -> None:
    inputs = copy.deepcopy(toolchain.load_json(INPUT_POLICY))
    toolchain._verify_source_authority(inputs)
    inputs["source"]["artifact"]["sha256"] = "0" * 64
    with pytest.raises(toolchain.VerificationError, match="U02 primary source authority"):
        toolchain._verify_source_authority(inputs)


@pytest.mark.parametrize(
    "value",
    ["", ".", "..", "/", "candidate\\cache", "C:/candidate", "/tmp/a/../b"],
)
def test_unsafe_or_noncanonical_external_root_fails_closed(value: str) -> None:
    isolation = copy.deepcopy(
        toolchain.load_json(ENGINE_POLICY)["external_cache_isolation"]
    )
    isolation["external_roots"]["candidate_input_root"] = value
    with pytest.raises(toolchain.VerificationError, match="external root"):
        toolchain._validate_external_roots(isolation)


def test_equal_ancestor_or_symlinked_external_root_fails_closed(tmp_path: Path) -> None:
    isolation = copy.deepcopy(
        toolchain.load_json(ENGINE_POLICY)["external_cache_isolation"]
    )
    isolation["external_roots"]["candidate_build_root"] = isolation[
        "external_roots"
    ]["candidate_input_root"] + "/child"
    with pytest.raises(toolchain.VerificationError, match="overlap"):
        toolchain._validate_external_roots(isolation)

    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    for key in isolation["external_roots"]:
        isolation["external_roots"][key] = str(tmp_path / key)
    isolation["external_roots"]["candidate_build_root"] = str(alias / "child")
    with pytest.raises(toolchain.VerificationError, match="symlinked ancestor"):
        toolchain._validate_external_roots(isolation)


def test_python_startup_or_external_symlink_authority_drift_fails_closed() -> None:
    python = copy.deepcopy(toolchain.load_json(ENGINE_POLICY)["python"])
    python["startup_argv"] = ["/usr/bin/python3.12"]
    with pytest.raises(toolchain.VerificationError, match="reviewed CPython"):
        toolchain._verify_snapshot_python_policy(
            python,
            toolchain.load_json(ENGINE_POLICY)["native_build_authority"],
        )

    python = copy.deepcopy(toolchain.load_json(ENGINE_POLICY)["python"])
    python["stdlib_external_symlinks"][1]["disposition"] = "ADMIT"
    with pytest.raises(toolchain.VerificationError, match="reviewed CPython"):
        toolchain._verify_snapshot_python_policy(
            python,
            toolchain.load_json(ENGINE_POLICY)["native_build_authority"],
        )


def test_full_policy_validation_rejects_unreviewed_python_selection_basis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = copy.deepcopy(toolchain.load_json(ENGINE_POLICY))
    engine["python"]["selection_basis"] = "UNREVIEWED"
    monkeypatch.setattr(
        toolchain, "_verify_command_router_policy", lambda *_args: None
    )

    with pytest.raises(toolchain.VerificationError, match="reviewed CPython"):
        toolchain._verify_policies(
            engine,
            toolchain.load_json(INPUT_POLICY),
            toolchain.load_json(WHEEL_POLICY),
            toolchain.load_json(CARGO_POLICY),
        )


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        (None, "executable_gid", False),
        (None, "executable_gid", 0.0),
        (None, "executable_size", 8020928.0),
        (None, "executable_uid", False),
        (None, "executable_uid", 0.0),
        ("libpython", "gid", False),
        ("libpython", "gid", 0.0),
        ("libpython", "size", 9061000.0),
        ("libpython", "uid", False),
        ("libpython", "uid", 0.0),
        ("stdlib_inventory", "directory_count", 92.0),
        ("stdlib_inventory", "file_count", 1213.0),
        ("stdlib_inventory", "record_count", 1308.0),
        ("stdlib_inventory", "root_gid", False),
        ("stdlib_inventory", "root_gid", 0.0),
        ("stdlib_inventory", "root_uid", False),
        ("stdlib_inventory", "root_uid", 0.0),
        ("stdlib_inventory", "symlink_count", 3.0),
    ],
)
def test_full_policy_validation_rejects_python_numeric_type_confusion(
    section: str | None,
    field: str,
    value: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = copy.deepcopy(toolchain.load_json(ENGINE_POLICY))
    target = engine["python"] if section is None else engine["python"][section]
    target[field] = value
    monkeypatch.setattr(
        toolchain, "_verify_command_router_policy", lambda *_args: None
    )

    with pytest.raises(toolchain.VerificationError, match="reviewed CPython"):
        toolchain._verify_policies(
            engine,
            toolchain.load_json(INPUT_POLICY),
            toolchain.load_json(WHEEL_POLICY),
            toolchain.load_json(CARGO_POLICY),
        )


def test_native_snapshot_schema_rejects_boolean_integer_substitution() -> None:
    native = copy.deepcopy(
        toolchain.load_json(ENGINE_POLICY)["native_build_authority"]
    )
    native["snapshot"]["schema_version"] = True

    with pytest.raises(toolchain.VerificationError, match="snapshot"):
        toolchain._verify_native_build_authority(native)


@pytest.mark.parametrize(
    "destination",
    [
        "/usr/bin/python3.12",
        "/usr/lib/python3.12",
        "/usr/lib/x86_64-linux-gnu",
    ],
)
def test_python_snapshot_cross_binding_missing_destination_fails_closed(
    destination: str,
) -> None:
    engine = copy.deepcopy(toolchain.load_json(ENGINE_POLICY))
    snapshot = engine["native_build_authority"]["snapshot"]
    snapshot["mappings"] = [
        record
        for record in snapshot["mappings"]
        if record["destination"] != destination
    ]

    with pytest.raises(toolchain.VerificationError, match="covered by native snapshot"):
        toolchain._verify_snapshot_python_policy(
            engine["python"], engine["native_build_authority"]
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("root", "/tmp/foreign"),
        ("receipt_path", "/tmp/foreign-receipt.json"),
        ("receipt_sha256", "0" * 64),
        ("payload_tree_sha256", "0" * 64),
        ("mappings", []),
        ("threat_model", "HOSTILE_HOST"),
    ),
)
def test_native_snapshot_policy_binding_drift_fails_closed(
    field: str, value: object
) -> None:
    native = copy.deepcopy(
        toolchain.load_json(ENGINE_POLICY)["native_build_authority"]
    )
    native["snapshot"][field] = value
    with pytest.raises(toolchain.VerificationError, match="snapshot"):
        toolchain._verify_native_build_authority(native)


def test_direct_cc_archiver_route_is_exact_and_has_no_unneeded_companion() -> None:
    engine = toolchain.load_json(ENGINE_POLICY)
    native = engine["native_build_authority"]
    destinations = {
        item["destination"] for item in native["snapshot"]["mappings"]
    }
    assert "/usr/bin/ar" in destinations
    assert "/usr/bin/x86_64-linux-gnu-ar" in destinations
    assert not any("ranlib" in path for path in destinations)
    route = engine["native_build_environment"]["sealed_source_trace"]
    assert route["aws-lc-sys"]["version"] == "0.43.0"
    assert route["aws-lc-sys"]["sha256"] == (
        "43103168cc76fe62678a375e722fc9cb3a0146159ac5828bc4f0dfd755c2224c"
    )
    assert route["cc"]["version"] == "1.4.0"
    assert route["cc"]["sha256"] == (
        "5add81bb678e6cb321aff7fa0dc7689ad82b112dbc032cea19f91d6b8e3582b9"
    )
    assert route["archive_finalization"] == "AR_S_NO_RANLIB_PROCESS"


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ("missing", "build environment"),
        ("drift", "build environment"),
        ("archiver", "snapshot"),
    ),
)
def test_direct_cc_environment_omission_or_archiver_drift_fails_closed(
    mutation: str,
    message: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = copy.deepcopy(toolchain.load_json(ENGINE_POLICY))
    if mutation == "missing":
        del engine["native_build_environment"]["static_exact_values"]["AR"]
    elif mutation == "drift":
        engine["native_build_environment"]["static_exact_values"][
            "AWS_LC_SYS_CMAKE_BUILDER"
        ] = "1"
    else:
        snapshot = engine["native_build_authority"]["snapshot"]
        snapshot["mappings"] = [
            record
            for record in snapshot["mappings"]
            if record["destination"] != "/usr/bin/ar"
        ]
    with pytest.raises(toolchain.VerificationError, match=message):
        if mutation == "archiver":
            toolchain._verify_native_build_authority(
                engine["native_build_authority"]
            )
        else:
            toolchain._verify_build_environment_policy(
                engine["native_build_environment"]
            )


def test_full_policy_validation_does_not_call_mutable_live_python_verifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = toolchain.load_json(ENGINE_POLICY)

    def reject_live_python(_policy: dict[str, Any]) -> None:
        raise AssertionError("mutable live Python authority was read")

    monkeypatch.setattr(
        toolchain, "_verify_system_python", reject_live_python, raising=False
    )
    monkeypatch.setattr(
        toolchain, "_verify_command_router_policy", lambda *_args: None
    )

    toolchain._verify_policies(
        engine,
        toolchain.load_json(INPUT_POLICY),
        toolchain.load_json(WHEEL_POLICY),
        toolchain.load_json(CARGO_POLICY),
    )


@pytest.mark.parametrize(
    "name",
    [
        "AR_x86_64-unknown-linux-gnu",
        "AR_x86_64_unknown_linux_gnu",
        "HOST_AR",
        "RANLIB",
        "CC_x86_64-unknown-linux-gnu",
        "CXX_x86_64_unknown_linux_gnu",
        "HOST_CFLAGS",
        "CPPFLAGS",
        "LDFLAGS",
        "RUSTFLAGS",
        "CARGO_ENCODED_RUSTFLAGS",
        "PKG_CONFIG",
        "CMAKE",
    ],
)
def test_ambient_native_build_override_fails_closed(
    name: str, verified_source_fd: int
) -> None:
    engine = toolchain.load_json(ENGINE_POLICY)
    environment = engine["native_build_environment"]
    stage = Path(roots_path(engine, "candidate_build_root")) / (
        "stage-aabbccddeeff0011"
    )
    with pytest.raises(toolchain.VerificationError, match="ambient build override"):
        _build_environment(
            environment, {name: "/tmp/host-override"}, stage, verified_source_fd
        )


def test_u04_command_router_policy_closes_the_exact_bare_executable_set() -> None:
    engine = toolchain.load_json(ENGINE_POLICY)
    cargo = toolchain.load_json(CARGO_POLICY)
    _assert_reviewed_candidate_path_identities(engine, cargo)
    roots = engine["external_cache_isolation"]["external_roots"]
    forensic_root = Path(roots["candidate_forensic_root"])
    assert forensic_root.parent == Path(roots["candidate_build_root"]).parent
    assert forensic_root.name == "nautilus-v1.231-reproducibility-evidence"
    assert {
        "candidate_llvm_toolchain_root",
        "candidate_rust_toolchain_root",
    } <= set(roots)
    assert "command_router" in engine
    toolchain._verify_command_router_policy(engine["command_router"], engine)
    assert [
        entry["name"] for entry in engine["command_router"]["entries"]
    ] == ["cargo", "clang", "clang++", "ld", "ld.lld", "rustc", "strip"]
    entries = {
        entry["name"]: entry for entry in engine["command_router"]["entries"]
    }
    assert entries["ld"] == {
        **entries["ld.lld"],
        "name": "ld",
        "path": "bin/ld",
    }
    cargo = engine["command_router"]["entries"][0]
    assert cargo["interpreter"] == {
        "kernel_shebang": "#!/usr/bin/python3.12 -IS",
        "mode": engine["python"]["executable_mode"],
        "path": engine["python"]["executable"],
        "sha256": engine["python"]["executable_sha256"],
        "size": engine["python"]["executable_size"],
        "startup_argv": [
            engine["python"]["executable"],
            "-IS",
            roots["candidate_toolchain_root"] + "/bin/cargo",
        ],
        "startup_flags": ["-I", "-S"],
    }
    assert engine["external_cache_isolation"]["root_access"] == {
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


def test_u04_build_environment_is_empty_exact_and_stage_derived(
    verified_source_fd: int,
) -> None:
    engine = toolchain.load_json(ENGINE_POLICY)
    policy = engine["native_build_environment"]
    build_root = Path(
        engine["external_cache_isolation"]["external_roots"][
            "candidate_build_root"
        ]
    )
    stage = build_root / "stage-0123456789abcdef"
    contract = _build_environment(policy, {}, stage, verified_source_fd)
    initial = contract["initial_environment"]
    effective = contract["effective_environment"]
    paths = contract["derived_paths"]

    assert policy["builder_derived_environment"] == {
        "P1_U04_SOURCE_ST_DEV": (
            "VERIFIED_SOURCE_FD_FSTAT_ST_DEV_CANONICAL_BASE10_POSITIVE"
        ),
        "P1_U04_SOURCE_ST_INO": (
            "VERIFIED_SOURCE_FD_FSTAT_ST_INO_CANONICAL_BASE10_POSITIVE"
        ),
    }
    assert policy["source_identity_handoff"] == {
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

    assert paths == {
        "artifacts": str(stage / "artifacts"),
        "cargo_target": str(stage / "cargo-target"),
        "dist": str(stage / "dist"),
        "home": str(stage / "home"),
        "source": str(stage / "source"),
        "staging": str(stage),
        "tmp": str(stage / "tmp"),
        "venv": str(stage / "venv"),
    }
    assert initial["PATH"] == roots_path(engine, "candidate_toolchain_root") + "/bin"
    assert initial["CARGO_HOME"] == roots_path(engine, "candidate_cargo_home_root")
    assert initial["CARGO_NET_OFFLINE"] == "true"
    assert initial["CARGO_BUILD_TARGET"] == "x86_64-unknown-linux-gnu"
    assert initial["CARGO_TARGET_DIR"] == paths["cargo_target"]
    assert initial["HOME"] == paths["home"]
    assert initial["PWD"] == effective["PWD"] == paths["source"]
    source_stat = os.fstat(verified_source_fd)
    assert initial["P1_U04_SOURCE_ST_DEV"] == str(source_stat.st_dev)
    assert initial["P1_U04_SOURCE_ST_INO"] == str(source_stat.st_ino)
    assert {
        name: initial[name]
        for name in ("P1_U04_SOURCE_ST_DEV", "P1_U04_SOURCE_ST_INO")
    } == {
        name: effective[name]
        for name in ("P1_U04_SOURCE_ST_DEV", "P1_U04_SOURCE_ST_INO")
    }
    assert initial["TMP"] == initial["TEMP"] == initial["TMPDIR"] == paths["tmp"]
    assert initial["VIRTUAL_ENV"] == paths["venv"]
    assert initial["PIP_FIND_LINKS"].endswith("/candidate-inputs/wheels")
    assert initial["PIP_NO_INDEX"] == initial["PIP_NO_CACHE_DIR"] == "1"
    assert initial["BUILD_MODE"] == "release"
    assert initial["HIGH_PRECISION"] == "true"
    assert initial["PARALLEL_BUILD"] == "false"
    assert "STRIP" not in initial and "STRIP" not in effective
    assert "CC" not in initial and effective["CC"] == "clang"
    assert "CXX" not in initial and effective["CXX"] == "clang++"
    assert "LDSHARED" not in initial and effective["LDSHARED"] == "clang -shared"
    assert effective["RUSTFLAGS"] == initial["RUSTFLAGS"] + " -C link-arg=-s"


def test_u04_build_environment_rejects_missing_or_wrong_pwd(
    verified_source_fd: int,
) -> None:
    engine = toolchain.load_json(ENGINE_POLICY)
    policy = engine["native_build_environment"]
    stage = Path(roots_path(engine, "candidate_build_root")) / (
        "stage-1234567890abcdef"
    )
    contract = _build_environment(policy, {}, stage, verified_source_fd)
    missing = dict(contract["initial_environment"])
    missing.pop("PWD", None)
    with pytest.raises(toolchain.VerificationError, match="initial environment"):
        _build_environment(
            policy,
            {},
            stage,
            verified_source_fd,
            supplied_initial=missing,
        )
    wrong = dict(contract["initial_environment"], PWD=str(stage))
    with pytest.raises(toolchain.VerificationError, match="initial environment"):
        _build_environment(
            policy,
            {},
            stage,
            verified_source_fd,
            supplied_initial=wrong,
        )


def test_u04_build_environment_accepts_only_source_pwd(
    verified_source_fd: int,
) -> None:
    engine = toolchain.load_json(ENGINE_POLICY)
    policy = engine["native_build_environment"]
    stage = Path(roots_path(engine, "candidate_build_root")) / (
        "stage-abcdef0123456789"
    )
    contract = _build_environment(policy, {}, stage, verified_source_fd)
    supplied = dict(contract["initial_environment"], PWD=str(stage / "source"))
    assert _build_environment(
        policy,
        {},
        stage,
        verified_source_fd,
        supplied_initial=supplied,
    ) == contract


def roots_path(engine: dict[str, Any], name: str) -> str:
    return engine["external_cache_isolation"]["external_roots"][name]


def test_u04_build_environment_rejects_ambient_missing_extra_and_bad_stage(
    verified_source_fd: int,
) -> None:
    engine = toolchain.load_json(ENGINE_POLICY)
    policy = engine["native_build_environment"]
    build_root = Path(roots_path(engine, "candidate_build_root"))
    stage = build_root / "stage-fedcba9876543210"
    contract = _build_environment(policy, {}, stage, verified_source_fd)

    with pytest.raises(toolchain.VerificationError, match="ambient"):
        _build_environment(
            policy, {"PATH": "/usr/bin"}, stage, verified_source_fd
        )
    missing = dict(contract["initial_environment"])
    missing.pop("CARGO_NET_OFFLINE")
    with pytest.raises(toolchain.VerificationError, match="initial environment"):
        _build_environment(
            policy,
            {},
            stage,
            verified_source_fd,
            supplied_initial=missing,
        )
    extra = dict(contract["effective_environment"], CARGO="/tmp/ambient-cargo")
    with pytest.raises(toolchain.VerificationError, match="effective environment"):
        _build_environment(
            policy,
            {},
            stage,
            verified_source_fd,
            supplied_effective=extra,
        )
    with pytest.raises(toolchain.VerificationError, match="staging child"):
        _build_environment(
            policy,
            {},
            build_root / "nested" / "stage-fedcba9876543210",
            verified_source_fd,
        )
    for section, key, value in (
        ("static_exact_values", "PATH", "/usr/bin"),
        (
            "static_exact_values",
            "CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER",
            "ld",
        ),
        ("static_exact_values", "RUSTFLAGS", "-C ambient"),
        ("derived_environment", "CARGO_TARGET_DIR", "/shared-target"),
        ("effective_source_overrides", "CC", "/usr/bin/cc"),
        ("effective_source_overrides", "LDSHARED", "cc -shared"),
    ):
        drifted = copy.deepcopy(policy)
        drifted[section][key] = value
        with pytest.raises(toolchain.VerificationError, match="build environment"):
            _build_environment(drifted, {}, stage, verified_source_fd)


def test_u04_source_identity_requires_verified_fd_and_canonical_exact_fields(
    verified_source_fd: int,
) -> None:
    engine = toolchain.load_json(ENGINE_POLICY)
    policy = engine["native_build_environment"]
    stage = Path(roots_path(engine, "candidate_build_root")) / (
        "stage-13579bdf02468ace"
    )
    contract = _build_environment(policy, {}, stage, verified_source_fd)

    with pytest.raises(toolchain.VerificationError, match="verified source"):
        toolchain._verify_build_environment(
            policy,
            {},
            stage,
            verified_source_fd=None,
            mount_destinations=[stage],
        )
    extra = dict(
        contract["effective_environment"], P1_U04_SOURCE_ST_UID="1000"
    )
    with pytest.raises(toolchain.VerificationError, match="effective environment"):
        _build_environment(
            policy,
            {},
            stage,
            verified_source_fd,
            supplied_effective=extra,
        )
    for name in ("P1_U04_SOURCE_ST_DEV", "P1_U04_SOURCE_ST_INO"):
        missing = dict(contract["initial_environment"])
        missing.pop(name)
        with pytest.raises(toolchain.VerificationError, match="source identity"):
            _build_environment(
                policy,
                {},
                stage,
                verified_source_fd,
                supplied_initial=missing,
            )
        for invalid in ("", "0", "01", "+1", "-1", "1.0", " 1"):
            noncanonical = dict(contract["effective_environment"])
            noncanonical[name] = invalid
            with pytest.raises(
                toolchain.VerificationError, match="source identity"
            ):
                _build_environment(
                    policy,
                    {},
                    stage,
                    verified_source_fd,
                    supplied_effective=noncanonical,
                )
        wrong = dict(contract["effective_environment"])
        wrong[name] = str(int(wrong[name]) + 1)
        with pytest.raises(toolchain.VerificationError, match="effective environment"):
            _build_environment(
                policy,
                {},
                stage,
                verified_source_fd,
                supplied_effective=wrong,
            )


def test_u04_source_symlink_fails_required_nofollow_open(tmp_path: Path) -> None:
    real = tmp_path / "real-source"
    real.mkdir()
    source = tmp_path / "source"
    source.symlink_to(real, target_is_directory=True)

    with pytest.raises(OSError):
        os.open(source, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW)


def test_u04_mount_graph_allows_only_one_stage_root_destination(
    verified_source_fd: int,
) -> None:
    engine = toolchain.load_json(ENGINE_POLICY)
    policy = engine["native_build_environment"]
    stage = Path(roots_path(engine, "candidate_build_root")) / (
        "stage-2468ace013579bdf"
    )
    _build_environment(policy, {}, stage, verified_source_fd)

    for destinations in (
        [],
        [stage / "source"],
        [stage / "source" / "nested"],
        [stage, stage / "source"],
        [stage, stage / "tmp"],
    ):
        with pytest.raises(toolchain.VerificationError, match="mount graph"):
            toolchain._verify_build_environment(
                policy,
                {},
                stage,
                verified_source_fd=verified_source_fd,
                mount_destinations=destinations,
            )


def test_cargo_wrapper_rejects_other_commands_and_execs_exact_offline_build(
    monkeypatch: pytest.MonkeyPatch,
    verified_source_fd: int,
) -> None:
    engine = toolchain.load_json(ENGINE_POLICY)
    policy = engine["native_build_environment"]
    stage = Path(roots_path(engine, "candidate_build_root")) / (
        "stage-0011223344556677"
    )
    contract = _build_environment(policy, {}, stage, verified_source_fd)
    wrapper = next(
        entry
        for entry in engine["command_router"]["entries"]
        if entry["name"] == "cargo"
    )
    source = wrapper["contents"]
    captured: dict[str, Any] = {}

    def fake_execve(path: str, argv: list[str], environment: dict[str, str]) -> None:
        captured.update(path=path, argv=argv, environment=environment)

    original_stat = os.stat
    verified_source_stat = os.fstat(verified_source_fd)
    monkeypatch.setattr(os, "execve", fake_execve)
    monkeypatch.setattr(os, "getcwd", lambda: str(stage / "source"))
    monkeypatch.setattr(
        os,
        "stat",
        lambda path, *args, **kwargs: (
            verified_source_stat
            if path == "."
            else original_stat(path, *args, **kwargs)
        ),
    )
    monkeypatch.setattr(os, "environ", dict(contract["effective_environment"]))
    monkeypatch.setattr(sys, "argv", ["cargo", "metadata"])
    with pytest.raises(SystemExit, match="only permits build"):
        exec(compile(source, "cargo-wrapper", "exec"), {"__name__": "__main__"})
    assert captured == {}

    monkeypatch.setattr(sys, "argv", ["cargo", "build", "--release"])
    exec(compile(source, "cargo-wrapper", "exec"), {"__name__": "__main__"})
    assert captured == {
        "path": wrapper["exec_target"]["path"],
        "argv": [
            wrapper["exec_target"]["path"],
            "build",
            "--locked",
            "--offline",
            "--release",
        ],
        "environment": contract["effective_environment"],
    }

    captured.clear()
    monkeypatch.setattr(
        os,
        "environ",
        dict(contract["effective_environment"], CARGO="/tmp/ambient-cargo"),
    )
    with pytest.raises(SystemExit, match="environment is not exact"):
        exec(compile(source, "cargo-wrapper", "exec"), {"__name__": "__main__"})
    assert captured == {}

    for name, value in (
        ("P1_U04_SOURCE_ST_DEV", "01"),
        (
            "P1_U04_SOURCE_ST_INO",
            str(int(contract["effective_environment"]["P1_U04_SOURCE_ST_INO"]) + 1),
        ),
    ):
        captured.clear()
        monkeypatch.setattr(
            os,
            "environ",
            dict(contract["effective_environment"], **{name: value}),
        )
        with pytest.raises(SystemExit, match="source identity is not exact"):
            exec(
                compile(source, "cargo-wrapper", "exec"),
                {"__name__": "__main__"},
            )
        assert captured == {}


def _router_fixture(
    root: Path, attack: str | None = None
) -> tuple[Path, dict[str, Any]]:
    policy = copy.deepcopy(toolchain.load_json(ENGINE_POLICY)["command_router"])
    router = root / "router"
    binary_dir = router / "bin"
    targets = root / "targets"
    binary_dir.mkdir(parents=True)
    targets.mkdir()
    policy["root"] = str(router)
    for entry in policy["entries"]:
        destination = binary_dir / entry["name"]
        if entry["type"] == "file":
            destination.write_bytes(entry["contents"].encode("ascii"))
            destination.chmod(0o500)
            continue
        target = targets / entry["name"]
        raw = (entry["name"] + " sealed target\n").encode("ascii")
        target.write_bytes(raw)
        target.chmod(0o500)
        entry["link_target"] = str(target)
        entry["resolved"] = {
            "mode": "0500",
            "path": str(target),
            "sha256": _sha256(raw),
            "size": len(raw),
            "type": "file",
        }
        destination.symlink_to(target)
    if attack == "missing":
        (binary_dir / "strip").unlink()
    elif attack == "extra":
        (binary_dir / "ambient").write_bytes(b"ambient")
        (binary_dir / "ambient").chmod(0o500)
    elif attack == "duplicate":
        policy["entries"].append(copy.deepcopy(policy["entries"][0]))
    elif attack == "mutable":
        (binary_dir / "cargo").chmod(0o700)
    elif attack == "wrong-target":
        link = binary_dir / "rustc"
        link.unlink()
        wrong = targets / "wrong-rustc"
        wrong.write_bytes(b"wrong")
        wrong.chmod(0o500)
        link.symlink_to(wrong)
    binary_dir.chmod(0o500)
    router.chmod(0o500)
    return router, policy


def test_command_router_rejects_missing_extra_duplicate_mutable_or_wrong_target(
) -> None:
    with tempfile.TemporaryDirectory(prefix="p1-u03-router-", dir="/tmp") as raw:
        test_root = Path(raw)
        valid_root = test_root / "valid"
        valid_router, valid_policy = _router_fixture(valid_root)
        try:
            toolchain._verify_command_router_layout(valid_router, valid_policy)
        finally:
            (valid_router / "bin").chmod(0o700)
            valid_router.chmod(0o700)
            (valid_root / "targets").chmod(0o700)
        for attack in ("missing", "extra", "duplicate", "mutable", "wrong-target"):
            attack_root = test_root / attack
            router, policy = _router_fixture(attack_root, attack)
            try:
                with pytest.raises(toolchain.VerificationError, match="command router"):
                    toolchain._verify_command_router_layout(router, policy)
            finally:
                (router / "bin").chmod(0o700)
                router.chmod(0o700)
                (attack_root / "targets").chmod(0o700)


def _cache_fixture(
    tmp_path: Path, attack: str | None = None
) -> tuple[Path, dict[str, Any], dict[str, Any], dict[str, Any]]:
    cache = tmp_path / "cache"
    directories = ["cargo-registry", "rust-inputs", "source-inputs", "wheels"]
    for directory in directories:
        (cache / directory).mkdir(parents=True, exist_ok=True)
    raw_by_path = {
        "source-inputs/source.tar.gz": b"source",
        "rust-inputs/channel.toml": b"channel",
        "rust-inputs/cargo.tar.xz": b"cargo",
        "rust-inputs/rust-std.tar.xz": b"std",
        "rust-inputs/rustc.tar.xz": b"rustc",
        "wheels/demo-1.0-py3-none-any.whl": b"wheel",
    }
    for relative, raw in raw_by_path.items():
        (cache / relative).write_bytes(raw)

    def record(relative: str) -> dict[str, Any]:
        raw = raw_by_path[relative]
        return {
            "filename": Path(relative).name,
            "mode": "0400",
            "sha256": _sha256(raw),
            "size": len(raw),
        }

    inputs = {
        "cache_layout": {
            "directories": directories,
            "directory_mode": "0500",
            "file_mode": "0400",
        },
        "rust": {
            "channel_manifest": record("rust-inputs/channel.toml"),
            "components": [
                {**record("rust-inputs/cargo.tar.xz"), "name": "cargo"},
                {**record("rust-inputs/rust-std.tar.xz"), "name": "rust-std"},
                {**record("rust-inputs/rustc.tar.xz"), "name": "rustc"},
            ],
        },
        "source": {"artifact": record("source-inputs/source.tar.gz")},
    }
    wheels = {
        "wheel_artifacts": [
            {
                **record("wheels/demo-1.0-py3-none-any.whl"),
                "package": "demo",
            }
        ]
    }
    cargo = {"cache_directory": "cargo-registry", "packages": []}
    target = cache / "wheels/demo-1.0-py3-none-any.whl"
    if attack == "extra":
        (cache / "wheels/extra.whl").write_bytes(b"extra")
    elif attack == "missing":
        target.unlink()
    elif attack == "content":
        target.write_bytes(b"changed")
    elif attack == "mutable":
        target.chmod(0o600)
    elif attack == "symlink":
        target.unlink()
        target.symlink_to(tmp_path / "outside")
    elif attack == "hardlink":
        outside = tmp_path / "outside"
        outside.write_bytes(b"wheel")
        target.unlink()
        os.link(outside, target)
    for path in cache.rglob("*"):
        if path.is_file() and not path.is_symlink() and not (
            attack == "mutable" and path == target
        ):
            path.chmod(0o400)
    for path in sorted(
        (item for item in cache.rglob("*") if item.is_dir()), reverse=True
    ):
        path.chmod(0o500)
    cache.chmod(0o500)
    return cache, inputs, wheels, cargo


@pytest.mark.parametrize(
    ("attack", "message"),
    (
        ("extra", "extra, missing, or duplicate"),
        ("missing", "extra, missing, or duplicate"),
        ("content", "artifact identity"),
        ("mutable", "artifact identity"),
        ("symlink", "artifact identity"),
        ("hardlink", "artifact identity"),
    ),
)
def test_cache_extra_missing_mutable_or_aliased_artifact_fails_closed(
    attack: str, message: str
) -> None:
    with tempfile.TemporaryDirectory(prefix="p1-u03-test-", dir="/tmp") as raw:
        cache, inputs, wheels, cargo = _cache_fixture(Path(raw), attack)
        try:
            with pytest.raises(toolchain.VerificationError, match=message):
                toolchain._verify_cache(cache, inputs, wheels, cargo)
        finally:
            cache.chmod(0o700)
            for directory in ("cargo-registry", "rust-inputs", "source-inputs", "wheels"):
                path = cache / directory
                if path.exists():
                    path.chmod(0o700)


def _runtime_fixture() -> tuple[
    dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, Any]
]:
    def artifact(name: str, dependencies: list[dict[str, str]]) -> dict[str, Any]:
        filename = f"{name}-1.0-py3-none-any.whl"
        return {
            "active_dependencies": dependencies,
            "filename": filename,
            "roles": ["runtime"],
            "sha256": "a" * 64,
            "size": 1,
            "url": f"https://files.pythonhosted.org/packages/example/{filename}",
            "version": "1.0",
        }

    artifacts = {
        "direct": artifact("direct", [{"package": "transitive", "version": "1.0"}]),
        "transitive": artifact("transitive", []),
    }
    lock_packages = {
        name: {
            "dependencies": ([{"name": "transitive"}] if name == "direct" else []),
            "name": name,
            "version": "1.0",
            "wheels": [
                {
                    "hash": "sha256:" + value["sha256"],
                    "size": value["size"],
                    "url": value["url"],
                }
            ],
        }
        for name, value in artifacts.items()
    }
    wheels = {"runtime_transitive": ["transitive"]}
    return lock_packages, artifacts, wheels


def test_runtime_dependency_graph_is_exact_and_closed() -> None:
    lock_packages, artifacts, wheels = _runtime_fixture()
    assert toolchain._validate_runtime_closure(
        lock_packages, artifacts, {"direct"}, wheels
    ) == {"direct": ["transitive"], "transitive": []}

    artifacts["direct"]["active_dependencies"] = []
    with pytest.raises(toolchain.VerificationError, match="dependency edges drifted"):
        toolchain._validate_runtime_closure(
            lock_packages, artifacts, {"direct"}, wheels
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ("version", "version drifted"),
        ("hash", "artifact drifted"),
        ("size", "artifact drifted"),
        ("filename", "not selected by uv.lock"),
    ),
)
def test_runtime_artifact_version_hash_size_or_filename_drift_fails_closed(
    mutation: str, message: str
) -> None:
    lock_packages, artifacts, wheels = _runtime_fixture()
    artifact = artifacts["transitive"]
    if mutation == "version":
        artifact["version"] = "2.0"
    elif mutation == "hash":
        artifact["sha256"] = "b" * 64
    elif mutation == "size":
        artifact["size"] = 2
    else:
        artifact["filename"] = "foreign-1.0-py3-none-any.whl"
    with pytest.raises(toolchain.VerificationError, match=message):
        toolchain._validate_runtime_closure(
            lock_packages, artifacts, {"direct"}, wheels
        )


def _write_wheel(
    path: Path,
    *,
    version: str = "1.0",
    tag: str = "py3-none-any",
    requirement: str | None = None,
    attack: str | None = None,
) -> None:
    metadata = f"Metadata-Version: 2.1\nName: demo\nVersion: {version}\n"
    if attack == "duplicate-name":
        metadata += "Name: foreign\n"
    elif attack == "duplicate-version":
        metadata += "Version: 9.9\n"
    if requirement is not None:
        metadata += f"Requires-Dist: {requirement}\n"
    dist_info = (
        "foreign-9.9.dist-info"
        if attack == "foreign-dist-info"
        else f"demo-{version}.dist-info"
    )
    members: dict[str, bytes] = {
        "demo/__init__.py": b"",
        f"{dist_info}/METADATA": (metadata + "\n").encode(),
        f"{dist_info}/WHEEL": (
            f"Wheel-Version: 1.0\nTag: {tag}\n\n"
        ).encode(),
    }
    explicit_directories: list[str] = []
    if attack == "traversal":
        members["../escape"] = b"escape"
    elif attack == "drive":
        members["C:/escape"] = b"escape"
    elif attack == "backslash":
        members["demo\\escape"] = b"escape"
    elif attack == "casefold":
        members["Demo/__init__.py"] = b"collision"
    elif attack == "nfc":
        members["d\u00e9mo/file"] = b"one"
        members["de\u0301mo/file"] = b"two"
    elif attack == "ancestor":
        members["demo"] = b"collision"
    elif attack == "coexisting-foreign-file":
        members["foreign-9.9.dist-info/ancillary"] = b"foreign"
    elif attack == "coexisting-empty-directory":
        explicit_directories.append("foreign-9.9.dist-info/")
    record_path = f"{dist_info}/RECORD"
    rows = []
    for name, raw in members.items():
        if attack == "record-missing" and name == "demo/__init__.py":
            continue
        digest = base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=").decode()
        rows.append([name, "sha256=" + digest, str(len(raw))])
    rows.append([record_path, "", ""])
    record = io.StringIO(newline="")
    csv.writer(record, lineterminator="\n").writerows(rows)
    if attack == "bad-record":
        record = io.StringIO(record.getvalue().replace("sha256=", "sha256=bad", 1))
    elif attack == "record-size":
        record = io.StringIO(record.getvalue().replace(",0\n", ",1\n", 1))
    members[record_path] = record.getvalue().encode()
    with zipfile.ZipFile(path, "w") as archive:
        for name, raw in members.items():
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            if attack == "symlink" and name == "demo/__init__.py":
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                raw = b"/tmp/outside"
            archive.writestr(info, raw)
        for name in explicit_directories:
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = (stat.S_IFDIR | 0o755) << 16
            archive.writestr(info, b"")


def _verify_wheel_requirement(
    tmp_path: Path,
    requirement: str,
    active_dependencies: list[dict[str, str]],
) -> None:
    wheels_dir = tmp_path / "wheels"
    wheels_dir.mkdir(exist_ok=True)
    filename = "demo-1.0-py3-none-any.whl"
    _write_wheel(wheels_dir / filename, requirement=requirement)
    toolchain._verify_wheel_metadata(
        tmp_path,
        {
            "wheel_artifacts": [
                {
                    "active_dependencies": active_dependencies,
                    "filename": filename,
                    "package": "demo",
                    "tags": ["py3-none-any"],
                    "version": "1.0",
                }
            ]
        },
    )


def test_wrong_python_or_platform_wheel_tag_fails_closed(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    wheels_dir = cache / "wheels"
    wheels_dir.mkdir(parents=True)
    filename = "demo-1.0-cp311-cp311-manylinux_2_28_x86_64.whl"
    _write_wheel(wheels_dir / filename, tag="cp311-cp311-manylinux_2_28_x86_64")
    policy = {
        "wheel_artifacts": [
            {
                "filename": filename,
                "package": "demo",
                "tags": ["cp311-cp311-manylinux_2_28_x86_64"],
                "version": "1.0",
            }
        ]
    }
    with pytest.raises(toolchain.VerificationError, match="foreign Python/platform tag"):
        toolchain._verify_wheel_metadata(cache, policy)


@pytest.mark.parametrize(
    "attack",
    ["foreign-dist-info", "duplicate-name", "duplicate-version"],
)
def test_wheel_dist_info_and_identity_headers_are_canonical_singletons(
    tmp_path: Path, attack: str
) -> None:
    wheels_dir = tmp_path / "wheels"
    wheels_dir.mkdir()
    filename = "demo-1.0-py3-none-any.whl"
    _write_wheel(wheels_dir / filename, attack=attack)
    policy = {
        "wheel_artifacts": [
            {
                "active_dependencies": [],
                "filename": filename,
                "package": "demo",
                "tags": ["py3-none-any"],
                "version": "1.0",
            }
        ]
    }
    with pytest.raises(toolchain.VerificationError, match="wheel identity"):
        toolchain._verify_wheel_metadata(tmp_path, policy)


@pytest.mark.parametrize(
    "attack", ["coexisting-foreign-file", "coexisting-empty-directory"]
)
def test_wheel_rejects_every_coexisting_foreign_dist_info_component(
    tmp_path: Path, attack: str
) -> None:
    wheels_dir = tmp_path / "wheels"
    wheels_dir.mkdir()
    filename = "demo-1.0-py3-none-any.whl"
    _write_wheel(wheels_dir / filename, attack=attack)
    policy = {
        "wheel_artifacts": [
            {
                "active_dependencies": [],
                "filename": filename,
                "package": "demo",
                "tags": ["py3-none-any"],
                "version": "1.0",
            }
        ]
    }
    with pytest.raises(toolchain.VerificationError, match="wheel identity"):
        toolchain._verify_wheel_metadata(tmp_path, policy)


@pytest.mark.parametrize(
    ("attack", "message"),
    (
        ("traversal", "unsafe wheel path"),
        ("drive", "unsafe wheel path"),
        ("backslash", "unsafe wheel path"),
        ("casefold", "wheel path collision"),
        ("nfc", "wheel path collision"),
        ("ancestor", "wheel path collision"),
        ("symlink", "non-regular wheel member"),
        ("bad-record", "RECORD"),
        ("record-missing", "RECORD set"),
        ("record-size", "RECORD hash or size"),
    ),
)
def test_adversarial_wheel_namespace_or_record_fails_closed(
    tmp_path: Path, attack: str, message: str
) -> None:
    wheels_dir = tmp_path / "wheels"
    wheels_dir.mkdir()
    filename = "demo-1.0-py3-none-any.whl"
    _write_wheel(wheels_dir / filename, attack=attack)
    policy = {
        "wheel_artifacts": [
            {
                "active_dependencies": [],
                "filename": filename,
                "package": "demo",
                "tags": ["py3-none-any"],
                "version": "1.0",
            }
        ]
    }
    with pytest.raises(toolchain.VerificationError, match=message):
        toolchain._verify_wheel_metadata(tmp_path, policy)


def test_wheel_filename_and_active_requires_dist_must_match_policy(tmp_path: Path) -> None:
    wheels_dir = tmp_path / "wheels"
    wheels_dir.mkdir()
    filename = "demo-1.0-py3-none-any.whl"
    _write_wheel(wheels_dir / filename, requirement="foreign==1.0")
    policy = {
        "wheel_artifacts": [
            {
                "active_dependencies": [],
                "filename": filename,
                "package": "demo",
                "tags": ["py3-none-any"],
                "version": "1.0",
            }
        ]
    }
    with pytest.raises(toolchain.VerificationError, match="Requires-Dist"):
        toolchain._verify_wheel_metadata(tmp_path, policy)

    _write_wheel(
        wheels_dir / filename,
        requirement="foreign==1.0; extra != 'dev'",
    )
    with pytest.raises(toolchain.VerificationError, match="Requires-Dist"):
        toolchain._verify_wheel_metadata(tmp_path, policy)

    policy["wheel_artifacts"][0]["filename"] = "foreign-1.0-py3-none-any.whl"
    (wheels_dir / filename).rename(wheels_dir / policy["wheel_artifacts"][0]["filename"])
    with pytest.raises(toolchain.VerificationError, match="filename"):
        toolchain._verify_wheel_metadata(tmp_path, policy)


@pytest.mark.parametrize(
    "version",
    [
        "2.3.1evil",
        "2.3.1+local",
        "2.3.1a1",
        "2.3.1rc1",
        "2.3.1.dev1",
    ],
)
def test_noncanonical_policy_version_fails_closed(version: str) -> None:
    engine = toolchain.load_json(ENGINE_POLICY)
    inputs = toolchain.load_json(INPUT_POLICY)
    wheels = copy.deepcopy(toolchain.load_json(WHEEL_POLICY))
    cargo = toolchain.load_json(CARGO_POLICY)
    wheels["wheel_artifacts"][0]["version"] = version
    with pytest.raises(toolchain.VerificationError, match="version"):
        toolchain._verify_policies(engine, inputs, wheels, cargo)


@pytest.mark.parametrize(
    "version",
    [
        "2.3.1evil",
        "2.3.1+local",
        "2.3.1a1",
        "2.3.1rc1",
        "2.3.1.dev1",
    ],
)
def test_record_valid_wheel_with_noncanonical_version_fails_closed(
    tmp_path: Path, version: str
) -> None:
    wheels_dir = tmp_path / "wheels"
    wheels_dir.mkdir()
    filename = f"demo-{version}-py3-none-any.whl"
    _write_wheel(wheels_dir / filename, version=version)
    policy = {
        "wheel_artifacts": [
            {
                "active_dependencies": [],
                "filename": filename,
                "package": "demo",
                "tags": ["py3-none-any"],
                "version": version,
            }
        ]
    }
    with pytest.raises(toolchain.VerificationError, match="version"):
        toolchain._verify_wheel_metadata(tmp_path, policy)


def test_strict_numeric_version_comparison_preserves_sealed_constraints() -> None:
    assert toolchain._satisfies("2.4.0", "~=2.4")
    assert toolchain._satisfies("2.9.0.post0", ">=2.8.2")
    assert toolchain._satisfies("1.17.0", ">=1.5")
    assert toolchain._satisfies("2.3.1", "==2.3.1")
    assert not toolchain._satisfies("2.3.1.0", "==2.3.1")
    for version in (
        "2.3.1evil",
        "2.3.1+local",
        "2.3.1a1",
        "2.3.1rc1",
        "2.3.1.dev1",
    ):
        with pytest.raises(toolchain.VerificationError, match="unsupported version"):
            toolchain._satisfies(version, "==2.3.1")


@pytest.mark.parametrize("specifier", (",>=1", ">=1,,<2", ">=1,"))
def test_strict_constraint_parser_rejects_empty_comma_clauses(
    specifier: str,
) -> None:
    with pytest.raises(toolchain.VerificationError, match="version constraint"):
        toolchain._satisfies("1.5", specifier)


@pytest.mark.parametrize(
    "requirement",
    (
        "foreign (>=1,,<2); extra == 'dev'",
        "foreign (>=1); extra == 'dev' trailing",
    ),
)
def test_inactive_extra_requirement_is_fully_validated(
    tmp_path: Path, requirement: str,
) -> None:
    wheels_dir = tmp_path / "wheels"
    wheels_dir.mkdir()
    filename = "demo-1.0-py3-none-any.whl"
    _write_wheel(
        wheels_dir / filename,
        requirement=requirement,
    )
    policy = {
        "wheel_artifacts": [
            {
                "active_dependencies": [],
                "filename": filename,
                "package": "demo",
                "tags": ["py3-none-any"],
                "version": "1.0",
            }
        ]
    }

    with pytest.raises(
        toolchain.VerificationError,
        match="version constraint|unsupported active wheel marker",
    ):
        toolchain._verify_wheel_metadata(tmp_path, policy)


def test_parenthesized_pep508_constraint_is_normalized_without_losing_context() -> None:
    assert toolchain._parse_requirement(
        'click[plugins, fast-mode] (>=8.4.1,<9.0.0) ; python_version < "3.14"'
    ) == (
        "click",
        ">=8.4.1,<9.0.0",
        ' python_version < "3.14"',
    )


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
def test_inactive_wheel_requirement_operands_are_strictly_parsed(
    tmp_path: Path,
    requirement: str,
) -> None:
    with pytest.raises(toolchain.VerificationError, match="requirement|constraint"):
        toolchain._parse_requirement(requirement)

    with pytest.raises(toolchain.VerificationError, match="requirement|constraint"):
        _verify_wheel_requirement(tmp_path, requirement, [])


def test_canonical_prerelease_operand_is_admitted_only_for_inactive_metadata(
    tmp_path: Path,
) -> None:
    requirement = 'aiohttp!=4.0.0a0,!=4.0.0a1; extra == "full"'
    assert toolchain._parse_requirement(requirement) == (
        "aiohttp",
        "!=4.0.0a0,!=4.0.0a1",
        ' extra == "full"',
    )
    assert not toolchain._metadata_marker_active(' extra == "full"')

    _verify_wheel_requirement(tmp_path, requirement, [])

    with pytest.raises(toolchain.VerificationError, match="unsupported version"):
        _verify_wheel_requirement(
            tmp_path,
            "aiohttp!=4.0.0a0",
            [{"package": "aiohttp", "version": "3.13.3"}],
        )


def test_canonical_prefix_wildcard_is_admitted_only_for_inactive_metadata(
    tmp_path: Path,
) -> None:
    requirement = 'pytest!=8.1.*,>=6; extra == "test"'
    assert toolchain._parse_requirement(requirement) == (
        "pytest",
        "!=8.1.*,>=6",
        ' extra == "test"',
    )

    _verify_wheel_requirement(tmp_path, requirement, [])

    with pytest.raises(toolchain.VerificationError, match="unsupported version"):
        _verify_wheel_requirement(
            tmp_path,
            "pytest!=8.1.*",
            [{"package": "pytest", "version": "8.0.0"}],
        )


@pytest.mark.parametrize(
    "requirement",
    (
        "click[bad extra] (>=8.4.1,<9.0.0)",
        'click (>=8.4.1,<9.0.0) ; sys_platform != "win32\'',
    ),
    ids=("invalid-extra", "mismatched-marker-quotes"),
)
def test_wheel_requirement_grammar_rejects_malformed_extras_and_quotes(
    tmp_path: Path,
    requirement: str,
) -> None:
    wheels_dir = tmp_path / "wheels"
    wheels_dir.mkdir()
    filename = "demo-1.0-py3-none-any.whl"
    _write_wheel(wheels_dir / filename, requirement=requirement)
    policy = {
        "wheel_artifacts": [
            {
                "active_dependencies": [{"package": "click", "version": "8.4.2"}],
                "filename": filename,
                "package": "demo",
                "tags": ["py3-none-any"],
                "version": "1.0",
            }
        ]
    }

    with pytest.raises(toolchain.VerificationError):
        toolchain._verify_wheel_metadata(tmp_path, policy)


@pytest.mark.parametrize(
    "marker",
    (
        'sys_platform != "win32\'',
        "sys_platform != 'win32\"",
    ),
)
def test_runtime_marker_grammar_preserves_quote_delimiters(marker: str) -> None:
    with pytest.raises(toolchain.VerificationError, match="marker"):
        toolchain._marker_active(marker)
    with pytest.raises(toolchain.VerificationError, match="marker"):
        toolchain._metadata_marker_active(marker)


@pytest.mark.parametrize(
    "requirement",
    (
        "click (>=8.4.1,<9.0.0",
        "click >=8.4.1,<9.0.0)",
        "click ()",
        "click ((>=8.4.1,<9.0.0))",
        "click (>=8.4.1,<9.0.0) trailing",
        "-click (>=8.4.1)",
        "click- (>=8.4.1)",
        "click[bad extra] (>=8.4.1)",
        "click[,plugins] (>=8.4.1)",
        "click[plugins,] (>=8.4.1)",
        "click[plugins,,fast] (>=8.4.1)",
    ),
)
def test_invalid_parenthesized_pep508_constraint_fails_closed(
    requirement: str,
) -> None:
    with pytest.raises(toolchain.VerificationError, match="source requirement"):
        toolchain._parse_requirement(requirement)


def test_wheel_sys_platform_not_win32_marker_is_exactly_allowlisted() -> None:
    assert toolchain._metadata_marker_active('sys_platform != "win32"')
    with pytest.raises(toolchain.VerificationError, match="unsupported active wheel marker"):
        toolchain._metadata_marker_active("sys_platform != 'linux'")


def _cargo_fixture(tmp_path: Path, attack: str | None = None) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    cache = tmp_path / "cargo-registry"
    cache.mkdir()
    raw = b"exact crate bytes"
    digest = _sha256(raw)
    filename = "demo-1.0.0.crate"
    (cache / filename).write_bytes(raw)
    package = {
        "checksum": digest,
        "filename": filename,
        "mode": "0400",
        "name": "demo",
        "sha256": digest,
        "size": len(raw),
        "source": "registry+https://github.com/rust-lang/crates.io-index",
        "version": "1.0.0",
    }
    cargo_policy = {"cache_directory": "cargo-registry", "package_count": 1, "packages": [package]}
    cargo_lock = {
        "package": [
            {
                "checksum": digest,
                "name": "demo",
                "source": package["source"],
                "version": "1.0.0",
            }
        ]
    }
    if attack == "missing":
        (cache / filename).unlink()
    elif attack == "extra":
        (cache / "extra-1.0.0.crate").write_bytes(b"extra")
    elif attack == "altered":
        (cache / filename).write_bytes(b"altered")
    elif attack == "lock":
        cargo_lock["package"][0]["version"] = "2.0.0"
    for path in cache.iterdir():
        path.chmod(0o400)
    cache.chmod(0o500)
    return cache, cargo_lock, cargo_policy


@pytest.mark.parametrize("attack", ["missing", "extra", "altered", "lock"])
def test_cargo_registry_missing_extra_altered_or_lock_drift_fails_closed(
    tmp_path: Path, attack: str
) -> None:
    cache, cargo_lock, cargo_policy = _cargo_fixture(tmp_path, attack)
    try:
        with pytest.raises(toolchain.VerificationError, match="Cargo"):
            toolchain._verify_cargo_registry(cache, cargo_lock, cargo_policy)
    finally:
        cache.chmod(0o700)


def test_supplied_incomplete_evidence_is_fail_not_deferred(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    cache.mkdir(mode=0o500)
    try:
        result = subprocess.run(
            [sys.executable, str(GENERATOR), "--evidence-cache", str(cache)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        cache.chmod(0o700)
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.startswith("NAUTILUS_TOOLCHAIN_INPUTS=FAIL reason=")
    assert "DEFERRED" not in result.stderr
