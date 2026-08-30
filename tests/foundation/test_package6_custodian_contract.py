from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
NATIVE_ROOT = ROOT / "native/package6_custodian"

EXPECTED_EVIDENCE = {
    "canonical_header": (
        "503643410001000100000000000102030405060708090a0b0c0d0e0f"
        "00000003352441c2"
    ),
    "constant.error_message_type": "0xffff",
    "constant.magic": "0x50364341",
    "constant.operation_id_bytes": "16",
    "constant.protocol_version": "1",
    "constant.request_id_bytes": "16",
    "constant.response_bit": "0x8000",
    "constant.sha256_bytes": "32",
    "constant.v1_flags": "0",
    "header_size": "36",
    "limit.max_argv_count": "128",
    "limit.max_environment_count": "128",
    "limit.max_frame_bytes": "1048612",
    "limit.max_payload_bytes": "1048576",
    "limit.max_public_code_bytes": "64",
    "limit.max_string_bytes": "4096",
    "malformed.bad_magic": "rejected",
    "malformed.nonzero_flags": "rejected",
    "malformed.oversize_payload": "rejected",
    "malformed.response_request": "rejected",
    "malformed.truncated_header": "rejected",
    "malformed.unknown_request": "rejected",
    "malformed.unsupported_version": "rejected",
    "offset.flags": "8",
    "offset.magic": "0",
    "offset.message_type": "6",
    "offset.payload_crc32": "32",
    "offset.payload_length": "28",
    "offset.request_id": "12",
    "offset.version": "4",
    "operation_state.ABSENT": "0",
    "operation_state.ACKNOWLEDGED": "14",
    "operation_state.CGROUP_CREATED": "3",
    "operation_state.CGROUP_EMPTY": "9",
    "operation_state.CGROUP_KILLED": "8",
    "operation_state.CHILD_CLONED": "4",
    "operation_state.CHILD_EXIT_OBSERVED": "10",
    "operation_state.CHILD_REAPED": "11",
    "operation_state.EXECUTABLE_PINNED": "2",
    "operation_state.EXEC_CONFIRMED": "5",
    "operation_state.RECOVERY_REQUIRED": "15",
    "operation_state.RESERVED": "1",
    "operation_state.RESULT_RETAINED": "13",
    "operation_state.RUNNING": "6",
    "operation_state.STOP_REQUESTED": "7",
    "operation_state.TRANSCRIPTS_FINAL": "12",
    "public_status.CONFLICT": "6",
    "public_status.INTERNAL": "10",
    "public_status.INVALID_FRAME": "1",
    "public_status.INVALID_REQUEST": "4",
    "public_status.LIMIT_EXCEEDED": "7",
    "public_status.NOT_FOUND": "5",
    "public_status.OK": "0",
    "public_status.RECOVERY_REQUIRED": "9",
    "public_status.TIMEOUT": "8",
    "public_status.UNAUTHORIZED": "3",
    "public_status.UNSUPPORTED_VERSION": "2",
    "request.ACK": "8",
    "request.HELLO": "1",
    "request.PUBLISH_BUNDLE": "7",
    "request.READ_TRANSCRIPT": "6",
    "request.RECOVER": "9",
    "request.RUN_ONCE": "5",
    "request.START": "2",
    "request.STATUS": "3",
    "request.STOP": "4",
    "descriptor_reuse": "PASS",
    "boundary_cgroup_empty": "PASS",
    "boundary_cgroup_killed": "PASS",
    "boundary_child_cloned": "PASS",
    "boundary_child_exit_observed": "PASS",
    "boundary_child_reaped": "PASS",
    "boundary_exec_confirmed": "PASS",
    "boundary_result_retained": "PASS",
    "boundary_running": "PASS",
    "boundary_stop_requested": "PASS",
    "boundary_transcripts_final": "PASS",
    "cgroup_fake_files": "PASS",
    "cgroup_remove_substitution_window": "PASS",
    "clone3_errno_classification": "PASS",
    "credential_authority_revalidated_before_clone": "PASS",
    "exec_marker_bytes": "PASS",
    "exec_marker_error": "PASS",
    "exec_marker_partial": "PASS",
    "exec_marker_quick_exit": "PASS",
    "exec_marker_timeout": "PASS",
    "executable_authority": "PASS",
    "executable_replacement_during_hash": "PASS",
    "journal_chain": "PASS",
    "journal_corrupt_transition": "PASS",
    "journal_fsync_failure": "PASS",
    "journal_impossible_transition": "PASS",
    "journal_payload_digest": "PASS",
    "journal_prior_digest": "PASS",
    "journal_sequence_duplicate": "PASS",
    "journal_sequence_gap": "PASS",
    "journal_v1_rejected": "PASS",
    "journal_torn_tail": "PASS",
    "journal_unknown_record": "PASS",
    "journal_unsafe_objects": "PASS",
    "owned_close_once": "PASS",
    "partial_pair": "PASS",
    "pipe_acquisition_failure_matrix": "PASS",
    "peer_and_replay": "PASS",
    "pipe_end_blocking_flags": "PASS",
    "production_blocking_pipe_drain": "PASS",
    "production_disconnect_real_child": "PASS",
    "production_peer_credentials": "PASS",
    "production_pidfd_observe_reap": "PASS",
    "production_socket_seqpacket": "PASS",
    "process_repeated_stop": "PASS",
    "process_success_stop_ack": "PASS",
    "post_clone_child_journal_failure_cleanup": "PASS",
    "post_clone_pidfd_acquire_failure": "PASS",
    "post_clone_status_writer_close_failure": "PASS",
    "post_clone_stderr_writer_close_failure": "PASS",
    "post_clone_stdout_writer_close_failure": "PASS",
    "operation_acquisition_failures": "PASS",
    "publication_collision_preserves_foreign": "PASS",
    "publication_concurrent_commit": "PASS",
    "publication_file_fsync": "PASS",
    "publication_manifest": "PASS",
    "publication_partial_write": "PASS",
    "publication_post_commit_fsync": "PASS",
    "publication_post_commit_verify": "PASS",
    "publication_rename": "PASS",
    "publication_success": "PASS",
    "service_ack_rejection_matrix": "PASS",
    "service_ancillary_rejection_matrix": "PASS",
    "service_tombstone_exact_exhaustion": "PASS",
    "service_tombstone_startup_over_capacity": "PASS",
    "service_peer_mismatch_matrix": "PASS",
    "service_publish_repeat_conflict": "PASS",
    "service_read_rejection_matrix": "PASS",
    "service_recover_ordered_tombstone_torn": "PASS",
    "service_registry_capacity": "PASS",
    "service_response_failures_recover": "PASS",
    "service_replay_capacity_before_dispatch": "PASS",
    "service_replay_changed_payload_collision": "PASS",
    "service_replay_identical_request": "PASS",
    "service_replay_malformed_does_not_consume": "PASS",
    "service_restart_retained_transcript": "PASS",
    "valid_rebuilt_untruncated_digest_conflicts": "PASS",
    "service_run_once_read_ack": "PASS",
    "service_socketpair_hello": "PASS",
    "service_start_dispatches": "PASS",
    "prechild_created_append_failure_cleanup": "PASS",
    "service_start_replay": "PASS",
    "service_start_status_stop": "PASS",
    "service_startup_malformed_journal_name": "PASS",
    "service_startup_populated_cgroup": "PASS",
    "service_startup_recover_enumerates": "PASS",
    "sha256_vectors": "PASS",
    "stop_freeze_error": "PASS",
    "stop_grace_error": "PASS",
    "stop_kill_error": "PASS",
    "stop_observe_error": "PASS",
    "stop_populated_timeout": "PASS",
    "stop_reap_error": "PASS",
    "stop_remove_error": "PASS",
    "stop_signal_error": "PASS",
    "stop_transcript_error": "PASS",
    "test_protocol": "PASS",
    "transcript_faults": "PASS",
    "transcript_recovery_both_zero_streams": "PASS",
    "transcript_truncation": "PASS",
    "truncated_retained_prefix_tamper": "PASS",
    "untruncated_digest_contradiction": "PASS",
    "retained_digest_record_rebind": "PASS",
    "full_stream_digest_independent_tamper": "PASS",
    "legacy_truncated_record_rejected": "PASS",
    "removal_intent_failure_prevents_remove": "PASS",
    "result_append_failure_after_remove": "PASS",
    "restart_removal_intent_absent": "PASS",
    "restart_removal_intent_populated_rejected": "PASS",
    "restart_removal_intent_present_empty": "PASS",
    "restart_removal_intent_replacement_rejected": "PASS",
    "restart_cgroup_before_mkdir": "PASS",
    "restart_cgroup_after_mkdir": "PASS",
    "restart_cgroup_after_created_append": "PASS",
    "disconnect_after_running": "PASS",
    "disconnect_cleanup_failure_matrix": "PASS",
    "disconnect_cleanup_retry_all_stages": "PASS",
    "disconnect_cleanup_retries_past_legacy_cap": "PASS",
    "disconnect_cleanup_held_authority": "PASS",
    "disconnect_during_transcript_output": "PASS",
    "disconnect_immediately_after_child_custody": "PASS",
    "disconnect_receive_failure_active": "PASS",
    "disconnect_send_failure_after_start": "PASS",
    "duplicate_tombstone_fails_closed": "PASS",
    "service_replay_restart_duplicate": "PASS",
    "service_replay_restart_uid_mismatch": "PASS",
    "service_replay_ledger_hardening": "PASS",
}


def _parse_evidence(output: str) -> dict[str, str]:
    evidence: dict[str, str] = {}
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if not separator:
            continue
        assert key not in evidence, f"duplicate native evidence key: {key}"
        evidence[key] = value
    return evidence


def test_package6_protocol_v1_contract_is_executable_and_byte_exact() -> None:
    native_makefile = NATIVE_ROOT / "Makefile"
    assert native_makefile.is_file(), "missing Package 6 native build scaffold"

    with tempfile.TemporaryDirectory(
        prefix="package6-custodian-contract-", dir="/tmp"
    ) as temporary_build_dir:
        build_dir = Path(temporary_build_dir).resolve()
        assert not build_dir.is_relative_to(ROOT)

        completed = subprocess.run(
            [
                "make",
                "--no-print-directory",
                "-s",
                "-C",
                str(NATIVE_ROOT),
                f"BUILD_DIR={build_dir}",
                "build",
                "test",
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )

        assert completed.returncode == 0, (
            "native Package 6 contract command failed\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )
        assert completed.stderr == ""

        production_binary = (build_dir / "package6-custodian").resolve()
        test_binaries = [
            (build_dir / name).resolve()
            for name in (
                "test_protocol",
                "test_authority",
                "test_publication",
            )
        ]
        assert production_binary.is_file()
        assert not production_binary.is_relative_to(ROOT)
        for test_binary in test_binaries:
            assert test_binary.is_file()
            assert not test_binary.is_relative_to(ROOT)

        elf_header = subprocess.run(
            ["readelf", "-hW", str(production_binary)],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
        assert "Type:" in elf_header
        assert "DYN (Position-Independent Executable file)" in elf_header

        program_headers = subprocess.run(
            ["readelf", "-lW", str(production_binary)],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
        assert "GNU_RELRO" in program_headers
        stack_header = next(
            line for line in program_headers.splitlines() if "GNU_STACK" in line
        )
        assert " RW " in stack_header
        assert "RWE" not in stack_header

        dynamic_section = subprocess.run(
            ["readelf", "-dW", str(production_binary)],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
        assert "BIND_NOW" in dynamic_section
        assert "(RPATH)" not in dynamic_section
        assert "(RUNPATH)" not in dynamic_section
        needed_libraries = [
            line.partition("[")[2].partition("]")[0]
            for line in dynamic_section.splitlines()
            if "(NEEDED)" in line
        ]
        assert needed_libraries == ["libc.so.6"]

        symbol_table = subprocess.run(
            ["readelf", "-Ws", str(production_binary)],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
        for forbidden_symbol_fragment in (
            "P6C_TESTING",
            "p6c_test_failpoint",
            "p6c_test_peer_override",
            "p6c_test_recovery_token",
            "p6c_test_service_io",
            "p6c_test_service_process_adapter",
            "p6c_test_exec_replacement",
            "p6c_test_exec_hash_observe",
            "p6c_service_test_",
            "p6c_service_run",
            "p6c_cgroup_remove",
            "p6c_clone3_spawn",
            "p6c_service_verify_credentials",
            "p6c_publication_publish",
        ):
            assert forbidden_symbol_fragment not in symbol_table

        assert _parse_evidence(completed.stdout) == EXPECTED_EVIDENCE

        version = subprocess.run(
            [str(production_binary), "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert (version.returncode, version.stdout, version.stderr) == (
            0,
            "package6-custodian retired-v2-supervisor-required\n",
            "",
        )

        expected_invocation_error = (
            "package6-custodian: release authority v2 supervisor required\n"
        )
        for arguments in ([], ["--unknown"], ["--socket-fd=3"]):
            rejected = subprocess.run(
                [str(production_binary), *arguments],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
            assert (rejected.returncode, rejected.stdout, rejected.stderr) == (
                78,
                "",
                expected_invocation_error,
            )


def test_package6_native_extension_uses_runtime_python_include_path(
    tmp_path: Path,
) -> None:
    real_python = Path(sys.executable).resolve()
    wrapper = tmp_path / "relocated-python"
    missing_build_prefix = tmp_path / "missing-build-prefix"
    wrapper.write_text(
        "#!/usr/bin/env python3\n"
        "import os\n"
        "import sys\n"
        f"real_python = {str(real_python)!r}\n"
        f"missing_include = {str(missing_build_prefix / 'include')!r}\n"
        "code = sys.argv[2] if len(sys.argv) > 2 and sys.argv[1] == '-c' else ''\n"
        "if \"get_config_var('INCLUDEPY')\" in code:\n"
        "    print(missing_include)\n"
        "else:\n"
        "    os.execv(real_python, [real_python, *sys.argv[1:]])\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o700)
    build_dir = tmp_path / "build"

    completed = subprocess.run(
        [
            "make",
            "--no-print-directory",
            "-s",
            "-C",
            str(NATIVE_ROOT),
            f"BUILD_DIR={build_dir}",
            f"PYTHON={wrapper}",
            "build",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert completed.returncode == 0, completed.stderr
    assert len(tuple((build_dir / "python").glob("_package6_fd_custody*.so"))) == 1


def test_package6_native_build_rejects_symlinked_output_into_repository(
    tmp_path: Path,
) -> None:
    direct_build_link = tmp_path / "build-link"
    direct_build_link.symlink_to(ROOT, target_is_directory=True)
    parent_link = tmp_path / "repository-parent"
    parent_link.symlink_to(ROOT, target_is_directory=True)

    for build_dir in (direct_build_link, parent_link / "nested-output"):
        completed = subprocess.run(
            [
                "make",
                "--no-print-directory",
                "-n",
                "-C",
                str(NATIVE_ROOT),
                f"BUILD_DIR={build_dir}",
                "build",
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert completed.returncode != 0
        assert "BUILD_DIR must resolve outside the repository" in completed.stderr


def test_package6_native_build_rejects_final_output_aliases(tmp_path: Path) -> None:
    for output_name, target_name in (
        ("package6-custodian", "build"),
        ("test_protocol", "test"),
        ("test_authority", "test"),
        ("test_publication", "test"),
    ):
        for alias_kind in ("symlink", "hardlink"):
            for force_rebuild in (False, True):
                case_name = (
                    f"{output_name}-{alias_kind}-"
                    f"{'forced' if force_rebuild else 'normal'}"
                )
                copied_root = tmp_path / f"copied-root-{case_name}"
                copied_native_root = copied_root / "native/package6_custodian"
                copied_native_root.parent.mkdir(parents=True)
                shutil.copytree(NATIVE_ROOT, copied_native_root)

                external_build_dir = tmp_path / f"external-build-{case_name}"
                external_build_dir.mkdir()
                sentinel = copied_root / f"{case_name}.sentinel"
                sentinel_bytes = f"preserve-{case_name}\n".encode()
                sentinel.write_bytes(sentinel_bytes)
                output_path = external_build_dir / output_name
                if alias_kind == "symlink":
                    output_path.symlink_to(sentinel)
                    expected_error = "refusing symlinked native output"
                else:
                    output_path.hardlink_to(sentinel)
                    expected_error = "refusing multiply-linked native output"

                command = ["make", "--no-print-directory"]
                if force_rebuild:
                    command.append("-B")
                command.extend(
                    [
                        "-C",
                        str(copied_native_root),
                        f"BUILD_DIR={external_build_dir}",
                        target_name,
                    ]
                )
                completed = subprocess.run(
                    command,
                    cwd=copied_root,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )

                assert completed.returncode != 0
                assert expected_error in completed.stderr
                if alias_kind == "symlink":
                    assert output_path.is_symlink()
                else:
                    assert output_path.stat().st_ino == sentinel.stat().st_ino
                assert sentinel.read_bytes() == sentinel_bytes


def _custodian_source(relative_path: str) -> str:
    return (NATIVE_ROOT / relative_path).read_text(encoding="utf-8")


def test_f1_disconnect_cleanup_is_mandatory_before_registry_close() -> None:
    authority = _custodian_source("src/linux_authority.c")

    assert "p6c_service_cleanup_after_disconnect" in authority
    assert "P6C_DISCONNECT_RECEIVE_EOF" in authority
    assert "P6C_DISCONNECT_SEND_FAILURE" in authority
    assert "P6C_DISCONNECT_CLEANUP_ATTEMPTS" not in authority
    assert "p6c_service_degraded_backoff" in authority


def test_f2_physical_child_custody_is_distinct_from_durable_state() -> None:
    types = _custodian_source("include/p6c_types.h")
    process = _custodian_source("src/process.c")

    assert "enum p6c_child_custody" in types
    assert "physical_custody" in types
    assert "P6C_CHILD_CGROUP_ONLY" in types
    assert "P6C_CHILD_PID_WAITABLE" in types
    assert "child_pid" in types
    assert "p6c_child_pid_observe" in process
    assert "p6c_child_pid_reap" in process


def test_f3_only_parent_pipe_read_ends_are_nonblocking() -> None:
    authority = _custodian_source("src/linux_authority.c")

    assert "pipe2(descriptors, O_CLOEXEC)" in authority
    assert "p6c_owned_pipe_abort" in authority
    assert "pipe2(descriptors, O_CLOEXEC | O_NONBLOCK)" not in authority


def test_f4_cgroup_removal_has_durable_internal_intent() -> None:
    types = _custodian_source("include/p6c_types.h")
    journal = _custodian_source("src/journal.c")
    process = _custodian_source("src/process.c")

    assert "P6C_JOURNAL_CGROUP_REMOVAL_INTENT" in types
    assert "p6c_journal_append_cgroup_removal_intent" in journal
    assert process.index("p6c_journal_append_cgroup_removal_intent") < process.index(
        "adapter->remove_cgroup"
    )


def test_f5_retained_prefix_digests_are_persisted_and_verified() -> None:
    types = _custodian_source("include/p6c_types.h")
    transcript = _custodian_source("src/transcript.c")
    journal = _custodian_source("src/journal.c")

    assert "retained_digest" in types
    assert "p6c_journal_append_transcript_digests" in journal
    assert "expected_retained_digest" in transcript
    assert "memcmp(digest, expected_retained_digest" in transcript


def test_f6_acknowledged_tombstones_do_not_consume_active_slots() -> None:
    types = _custodian_source("include/p6c_types.h")
    authority = _custodian_source("src/linux_authority.c")

    assert "struct p6c_service_tombstone" in authority
    assert "p6c_service_archive_acknowledged" in authority
    assert "p6c_service_find_tombstone" in authority
    assert "P6C_TOMBSTONE_CAPACITY" in types
    assert "lookup_tombstone" not in authority
    assert "tombstone_total" not in authority


def test_f7_replay_check_precedes_service_dispatch() -> None:
    authority = _custodian_source("src/linux_authority.c")

    replay = authority.index("p6c_service_check_replay")
    dispatch = authority.index("frame.message_type == (uint16_t)P6C_REQUEST_HELLO")
    assert replay < dispatch
    assert "P6C_REPLAY_LEDGER_NAME" in authority
    assert "p6c_replay_ledger_reserve" in authority
    assert "struct p6c_replay_table replay;" not in authority


def test_f8_safe_production_parity_and_gated_cgroup_cases_exist() -> None:
    authority_tests = _custodian_source("tests/test_authority.c")

    for case_name in (
        "production_peer_credentials",
        "production_socket_seqpacket",
        "production_blocking_pipe_drain",
        "production_pidfd_observe_reap",
        "production_disconnect_real_child",
    ):
        assert f'"{case_name}"' in authority_tests
    assert "case_opt_in_delegated_cgroup_disconnect" in authority_tests
    assert "P6C_DELEGATED_CGROUP_TEST_ROOT" in authority_tests
    delegated_case = authority_tests[
        authority_tests.index("case_opt_in_delegated_cgroup_disconnect"):
    ]
    assert "p6c_service_run(&configuration)" in delegated_case
    assert "AUTHORED_NOT_EXECUTED" not in authority_tests


def test_f3b_cgroup_name_and_inode_are_durable_before_child_creation() -> None:
    types = _custodian_source("include/p6c_types.h")
    authority = _custodian_source("src/linux_authority.c")
    journal = _custodian_source("src/journal.c")

    assert "P6C_JOURNAL_CGROUP_ALLOCATION_INTENT" in types
    assert "p6c_journal_append_cgroup_allocation_intent" in journal
    assert '#define P6C_JOURNAL_VERSION UINT16_C(2)' in journal
    assert "P6CJNL1" not in journal
    assert "p6c_service_cleanup_prechild_cgroup" in authority
    allocation = authority.index("p6c_journal_append_cgroup_allocation_intent")
    create = authority.index("p6c_cgroup_create", allocation)
    created = authority.index("P6C_OPERATION_CGROUP_CREATED", create)
    clone = authority.index("p6c_operation_start", created)
    assert allocation < create < created < clone


def test_production_native_binary_is_fail_closed_v2_supervisor_stub() -> None:
    with tempfile.TemporaryDirectory(
        prefix="package6-custodian-v2-stub-", dir="/tmp"
    ) as temporary_build_dir:
        build_dir = Path(temporary_build_dir).resolve()
        completed = subprocess.run(
            [
                "make",
                "--no-print-directory",
                "-s",
                "-C",
                str(NATIVE_ROOT),
                f"BUILD_DIR={build_dir}",
                "build",
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert completed.returncode == 0, completed.stderr
        binary = build_dir / "package6-custodian"
        symbols = subprocess.run(
            ["nm", "-a", str(binary)],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        ).stdout
        for forbidden in (
            "p6c_service_run",
            "p6c_cgroup_remove",
            "p6c_clone3_spawn",
            "p6c_service_verify_credentials",
            "p6c_publication_publish",
        ):
            assert forbidden not in symbols
        version = subprocess.run(
            [str(binary), "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert (version.returncode, version.stdout, version.stderr) == (
            0,
            "package6-custodian retired-v2-supervisor-required\n",
            "",
        )
        rejected = subprocess.run(
            [str(binary)],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert (rejected.returncode, rejected.stdout, rejected.stderr) == (
            78,
            "",
            "package6-custodian: release authority v2 supervisor required\n",
        )


def test_native_build_repairs_stale_python_extension_mode() -> None:
    with tempfile.TemporaryDirectory(
        prefix="package6-custodian-stale-extension-", dir="/tmp"
    ) as temporary_build_dir:
        build_dir = Path(temporary_build_dir).resolve()
        environment = dict(os.environ)
        environment.update(
            {
                "BUILD_DIR": str(build_dir),
                "NATIVE_ROOT": str(NATIVE_ROOT),
            }
        )
        command = (
            'umask 0002; exec make --no-print-directory -s -C "$NATIVE_ROOT" '
            '"BUILD_DIR=$BUILD_DIR" build'
        )

        initial = subprocess.run(
            ["/bin/sh", "-c", command],
            cwd=ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert initial.returncode == 0, initial.stderr
        extensions = tuple(
            (build_dir / "python").glob("_package6_fd_custody*.so")
        )
        assert len(extensions) == 1
        extension = extensions[0]
        extension.chmod(0o755)
        assert extension.stat().st_mode & 0o777 == 0o755

        rebuilt = subprocess.run(
            ["/bin/sh", "-c", command],
            cwd=ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )

        assert rebuilt.returncode == 0, rebuilt.stderr
        assert extension.stat().st_mode & 0o777 == 0o600


def _write_executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o700)


def _run_root_make_with_stubs(
    tmp_path: Path,
    target: str,
) -> tuple[subprocess.CompletedProcess[str], list[str], list[str]]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(parents=True)
    make_log = tmp_path / "make.log"
    pytest_log = tmp_path / "pytest.log"
    fake_make = fake_bin / "make"
    _write_executable(
        fake_make,
        """#!/bin/sh
set -eu
build_dir=
for argument in "$@"; do
    case "$argument" in
        BUILD_DIR=*) build_dir=${argument#BUILD_DIR=} ;;
    esac
done
test -n "$build_dir"
printf '%s|%s\n' "$build_dir" "$*" >> "$PACKAGE6_TEST_MAKE_LOG"
case " $* " in
    *" build "*)
        install -d -m 0700 "$build_dir/python"
        umask 0177
        printf '%s\n' 'isolated candidate extension' \
            > "$build_dir/python/_package6_fd_custody.test.so"
        ;;
esac
""",
    )
    fake_uv = fake_bin / "uv"
    _write_executable(
        fake_uv,
        """#!/bin/sh
set -eu
test "$1" = run
case "$2" in
    pytest|python) ;;
    *) exit 98 ;;
esac
printf '%s|%s\n' \
    "$PACKAGE6_FD_CUSTODY_EXTENSION_PATH" \
    "$PACKAGE6_FD_CUSTODY_EXTENSION_SHA256" \
    >> "$PACKAGE6_TEST_PYTEST_LOG"
""",
    )
    environment = dict(os.environ)
    environment.update(
        {
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "PACKAGE6_TEST_MAKE_LOG": str(make_log),
            "PACKAGE6_TEST_PYTEST_LOG": str(pytest_log),
        }
    )
    completed = subprocess.run(
        [
            "/usr/bin/make",
            "--no-print-directory",
            "-s",
            f"MAKE={fake_make}",
            target,
        ],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    make_records = make_log.read_text(encoding="utf-8").splitlines() \
        if make_log.exists() else []
    pytest_records = pytest_log.read_text(encoding="utf-8").splitlines() \
        if pytest_log.exists() else []
    return completed, make_records, pytest_records


def test_root_python_test_target_uses_unique_build_and_exact_digest(
    tmp_path: Path,
) -> None:
    observed_builds: list[Path] = []
    observed_pytest: list[str] = []
    for invocation in range(2):
        completed, make_records, pytest_records = _run_root_make_with_stubs(
            tmp_path / f"test-{invocation}",
            "test",
        )
        assert completed.returncode == 0, completed.stderr
        assert len(make_records) == 1
        build_value, separator, arguments = make_records[0].partition("|")
        assert separator
        assert arguments.endswith(" build")
        build_dir = Path(build_value)
        assert build_dir.is_absolute()
        assert not build_dir.is_relative_to(ROOT)
        assert build_dir.stat().st_mode & 0o777 == 0o700
        observed_builds.append(build_dir)
        assert len(pytest_records) == 1
        observed_pytest.extend(pytest_records)

    assert observed_builds[0] != observed_builds[1]
    for record, build_dir in zip(observed_pytest, observed_builds, strict=True):
        extension_value, separator, expected_digest = record.partition("|")
        assert separator
        extension = Path(extension_value)
        assert extension.parent == build_dir / "python"
        assert expected_digest == hashlib.sha256(extension.read_bytes()).hexdigest()


def test_root_test_governance_target_uses_unique_build_and_exact_digest(
    tmp_path: Path,
) -> None:
    observed_builds: list[Path] = []
    observed_governance: list[str] = []
    for invocation in range(2):
        completed, make_records, governance_records = _run_root_make_with_stubs(
            tmp_path / f"governance-{invocation}",
            "check-test-skips",
        )
        assert completed.returncode == 0, completed.stderr
        assert len(make_records) == 1
        build_value, separator, arguments = make_records[0].partition("|")
        assert separator
        assert arguments.endswith(" build")
        build_dir = Path(build_value)
        assert build_dir.is_absolute()
        assert not build_dir.is_relative_to(ROOT)
        assert build_dir.stat().st_mode & 0o777 == 0o700
        observed_builds.append(build_dir)
        assert len(governance_records) == 1
        observed_governance.extend(governance_records)

    assert observed_builds[0] != observed_builds[1]
    for record, build_dir in zip(
        observed_governance,
        observed_builds,
        strict=True,
    ):
        extension_value, separator, expected_digest = record.partition("|")
        assert separator
        extension = Path(extension_value)
        assert extension.parent == build_dir / "python"
        assert expected_digest == hashlib.sha256(extension.read_bytes()).hexdigest()


def test_root_native_test_target_uses_unique_private_builds(tmp_path: Path) -> None:
    observed_builds: list[Path] = []
    for invocation in range(2):
        completed, make_records, pytest_records = _run_root_make_with_stubs(
            tmp_path / f"native-{invocation}",
            "test-package6-custodian-native",
        )
        assert completed.returncode == 0, completed.stderr
        assert pytest_records == []
        assert len(make_records) == 1
        build_value, separator, arguments = make_records[0].partition("|")
        assert separator
        assert arguments.endswith(" test")
        build_dir = Path(build_value)
        assert build_dir.is_absolute()
        assert not build_dir.is_relative_to(ROOT)
        assert build_dir.stat().st_mode & 0o777 == 0o700
        observed_builds.append(build_dir)

    assert observed_builds[0] != observed_builds[1]


def test_native_build_replaces_all_warm_outputs() -> None:
    with tempfile.TemporaryDirectory(
        prefix="package6-custodian-warm-build-", dir="/tmp"
    ) as temporary_build_dir:
        build_dir = Path(temporary_build_dir).resolve()
        python = ROOT / ".venv/bin/python"
        targets = [
            "build",
            "test-service",
            *(str(build_dir / name) for name in (
                "test_protocol",
                "test_authority",
                "test_publication",
            )),
        ]
        command = [
            "make",
            "--no-print-directory",
            "-s",
            "-C",
            str(NATIVE_ROOT),
            f"BUILD_DIR={build_dir}",
            f"PYTHON={python}",
            *targets,
        ]
        initial = subprocess.run(
            command,
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert initial.returncode == 0, initial.stderr
        extensions = tuple(
            (build_dir / "python").glob("_package6_fd_custody*.so")
        )
        assert len(extensions) == 1
        outputs = [
            build_dir / "package6-custodian",
            build_dir / "test-package6-custodian",
            build_dir / "test_protocol",
            build_dir / "test_authority",
            build_dir / "test_publication",
            extensions[0],
        ]
        for output in outputs:
            output.write_bytes(b"stale warm output\n")
            output.chmod(0o600 if output == extensions[0] else 0o700)

        rebuilt = subprocess.run(
            command,
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )

        assert rebuilt.returncode == 0, rebuilt.stderr
        for output in outputs:
            assert output.read_bytes().startswith(b"\x7fELF"), output


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    (
        ("different-inode", "publication provenance changed"),
        ("nonregular", "regular non-symlink"),
        ("writable-parent", "publication parent policy changed"),
    ),
)
def test_native_extension_publication_rejects_final_provenance_change(
    tmp_path: Path,
    mutation: str,
    expected_error: str,
) -> None:
    build_dir = tmp_path / mutation
    fake_bin = tmp_path / f"bin-{mutation}"
    fake_bin.mkdir()
    fake_mv = fake_bin / "mv"
    _write_executable(
        fake_mv,
        """#!/bin/sh
set -eu
while test "$#" -gt 2; do shift; done
source=$1
target=$2
case "$PACKAGE6_TEST_PUBLICATION_MUTATION" in
    different-inode)
        cp -- "$source" "$target"
        ;;
    nonregular)
        mkfifo -- "$target"
        ;;
    writable-parent)
        /usr/bin/mv -Tf -- "$source" "$target"
        chmod 0777 "${target%/*}"
        ;;
    *)
        exit 97
        ;;
esac
""",
    )
    suffix = sysconfig.get_config_var("EXT_SUFFIX")
    assert isinstance(suffix, str) and suffix
    extension = build_dir / "python" / f"_package6_fd_custody{suffix}"
    environment = dict(os.environ)
    environment.update(
        {
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "PACKAGE6_TEST_PUBLICATION_MUTATION": mutation,
        }
    )

    completed = subprocess.run(
        [
            "make",
            "--no-print-directory",
            "-s",
            "-C",
            str(NATIVE_ROOT),
            f"BUILD_DIR={build_dir}",
            f"PYTHON={ROOT / '.venv/bin/python'}",
            str(extension),
        ],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert completed.returncode != 0
    assert expected_error in completed.stderr
