from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta

import pytest

from packages.job_contracts import JobType, ReplayPayload, SnapshotPayload
from services.job_worker.results import ResultValidationError, ResultValidator
from tests.jobs.backend_contract_fixtures import (
    ATTEMPT_ID,
    BACKEND_COMMIT,
    JOB_ID,
    REPLAY_SIDECAR_SAMPLE,
    REPORT_SAMPLE,
    SEMANTIC_INPUT_FINGERPRINT,
    SESSION_ID,
)


START = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)


class Job:
    job_id = JOB_ID
    attempt_id = ATTEMPT_ID
    job_type = JobType.SNAPSHOT
    payload = SnapshotPayload(scope="default", requested_as_of=None)


def _report(
    path, *, job_id=JOB_ID, attempt_id=ATTEMPT_ID,
    as_of=START + timedelta(seconds=1),
    semantic_input_fingerprint=SEMANTIC_INPUT_FINGERPRINT,
):
    document = {
        "job_id": job_id,
        "attempt_id": attempt_id,
        "timestamp": as_of.isoformat(),
        "assets": [{"symbol": "BTC", "current_price": 100.0, "suggestion": "BUY"}],
        "research_only": True,
        "backend_commit": BACKEND_COMMIT,
    }
    if semantic_input_fingerprint is not None:
        document["semantic_input_fingerprint"] = semantic_input_fingerprint
    path.write_text(json.dumps(document), encoding="utf-8")
    timestamp = as_of.timestamp()
    os.utime(path, (timestamp, timestamp))


def test_reviewed_backend_report_sample_requires_exact_lineage(tmp_path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    source = reports / f"report_{ATTEMPT_ID}.json"
    source.write_text(json.dumps(REPORT_SAMPLE), encoding="utf-8")
    os.utime(source, (START.timestamp() + 1, START.timestamp() + 1))

    result = ResultValidator(reports, tmp_path / "replays").validate(
        "legacy-report-v1", Job(), exit_code=0, attempt_started_at=START,
        backend_commit=BACKEND_COMMIT,
        semantic_input_fingerprint=SEMANTIC_INPUT_FINGERPRINT,
    )

    assert result.validation_metadata["backend_commit"] == BACKEND_COMMIT
    assert result.validation_metadata["research_only"] is True
    assert (
        result.validation_metadata["semantic_input_fingerprint"]
        == SEMANTIC_INPUT_FINGERPRINT
    )


@pytest.mark.parametrize("reported_fingerprint", [None, "b" * 64])
def test_report_rejects_missing_or_mismatched_spawn_bound_semantic_fingerprint(
    tmp_path, reported_fingerprint,
) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    _report(
        reports / "report_new.json",
        semantic_input_fingerprint=reported_fingerprint,
    )

    with pytest.raises(ResultValidationError, match="semantic input fingerprint"):
        ResultValidator(reports, tmp_path / "replays").validate(
            "legacy-report-v1", Job(), exit_code=0, attempt_started_at=START,
            backend_commit=BACKEND_COMMIT,
            semantic_input_fingerprint=SEMANTIC_INPUT_FINGERPRINT,
        )


def test_report_requires_a_valid_spawn_bound_semantic_fingerprint(tmp_path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    _report(reports / "report_new.json")

    for fingerprint in (None, "A" * 64, "a" * 63):
        with pytest.raises(ResultValidationError, match="spawn-bound semantic input fingerprint"):
            ResultValidator(reports, tmp_path / "replays").validate(
                "legacy-report-v1", Job(), exit_code=0, attempt_started_at=START,
                backend_commit=BACKEND_COMMIT,
                semantic_input_fingerprint=fingerprint,
            )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("job_id", "job_ffffffffffffffffffffffffffffffff"),
        ("attempt_id", "attempt_00000000000000000000000000000000"),
        ("backend_commit", "f" * 40),
        ("research_only", False),
        ("research_only", "true"),
    ],
)
def test_report_rejects_any_inexact_reviewed_backend_lineage(tmp_path, field, value) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    document = {**REPORT_SAMPLE, field: value}
    source = reports / f"report_{ATTEMPT_ID}.json"
    source.write_text(json.dumps(document), encoding="utf-8")
    os.utime(source, (START.timestamp() + 1, START.timestamp() + 1))

    with pytest.raises(ResultValidationError, match="attributable"):
        ResultValidator(reports, tmp_path / "replays").validate(
            "legacy-report-v1", Job(), exit_code=0, attempt_started_at=START,
            backend_commit=BACKEND_COMMIT,
            semantic_input_fingerprint=SEMANTIC_INPUT_FINGERPRINT,
        )


def test_reviewed_backend_replay_sidecar_exact_schema_is_accepted(tmp_path) -> None:
    replays = tmp_path / "replays"
    replays.mkdir()
    source = replays / f"replay_{ATTEMPT_ID}.json"
    source.write_text(json.dumps(REPLAY_SIDECAR_SAMPLE), encoding="utf-8")
    os.utime(source, (START.timestamp() + 1, START.timestamp() + 1))
    job = Job()
    job.job_type = JobType.REPLAY
    job.payload = ReplayPayload(session_id=SESSION_ID)

    result = ResultValidator(tmp_path / "reports", replays).validate(
        "legacy-replay-v1", job, exit_code=0, attempt_started_at=START,
        backend_commit=BACKEND_COMMIT,
    )

    assert result.validation_metadata["event_count"] == 2


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(extra="raw secret"),
        lambda value: value["events"][0].update(query="raw secret"),
        lambda value: value.update(event_count=3),
        lambda value: value.update(backend_commit="f" * 40),
        lambda value: value.update(events=[]),
    ],
)
def test_replay_sidecar_rejects_unbounded_unsanitized_or_inexact_schema(
    tmp_path, mutation,
) -> None:
    replays = tmp_path / "replays"
    replays.mkdir()
    document = json.loads(json.dumps(REPLAY_SIDECAR_SAMPLE))
    mutation(document)
    source = replays / f"replay_{ATTEMPT_ID}.json"
    source.write_text(json.dumps(document), encoding="utf-8")
    os.utime(source, (START.timestamp() + 1, START.timestamp() + 1))
    job = Job()
    job.job_type = JobType.REPLAY
    job.payload = ReplayPayload(session_id=SESSION_ID)

    with pytest.raises(ResultValidationError):
        ResultValidator(tmp_path / "reports", replays).validate(
            "legacy-replay-v1", job, exit_code=0, attempt_started_at=START,
            backend_commit=BACKEND_COMMIT,
        )


def test_report_requires_exit_zero_and_one_fresh_attributable_schema_valid_file(tmp_path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    _report(reports / "report_new.json")

    sealed = tmp_path / "sealed"
    result = ResultValidator(reports, tmp_path / "replays", sealed).validate(
        "legacy-report-v1", Job(), exit_code=0, attempt_started_at=START,
        backend_commit=BACKEND_COMMIT,
        semantic_input_fingerprint=SEMANTIC_INPUT_FINGERPRINT,
    )

    assert result.relative_ref.startswith(
        "results/job_0123456789abcdef0123456789abcdef/"
        "attempt_fedcba9876543210fedcba9876543210/"
    )
    copy = sealed / result.relative_ref
    assert copy.read_bytes() == (reports / "report_new.json").read_bytes()
    # The implementation fchmods 0600; drvfs-backed pytest temp directories
    # report synthetic 0777 modes, so content/placement are asserted here.
    assert result.size_bytes > 0
    assert len(result.sha256) == 64
    assert result.validation_metadata == {
        "job_id": JOB_ID,
        "attempt_id": ATTEMPT_ID,
        "backend_commit": BACKEND_COMMIT,
        "research_only": True,
        "semantic_input_fingerprint": SEMANTIC_INPUT_FINGERPRINT,
        "validator_id": "legacy-report-v1",
    }

    (reports / "report_new.json").write_text("replaced", encoding="utf-8")
    assert copy.read_bytes() != b"replaced"


def test_report_rejects_wrong_worker_owned_job_attribution(tmp_path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    _report(reports / "report_new.json", job_id="client-chosen")

    with pytest.raises(ResultValidationError, match="attributable"):
        ResultValidator(reports, tmp_path / "replays", tmp_path / "sealed").validate(
            "legacy-report-v1", Job(), exit_code=0, attempt_started_at=START,
            backend_commit=BACKEND_COMMIT,
            semantic_input_fingerprint=SEMANTIC_INPUT_FINGERPRINT,
        )


def test_result_scan_rejects_ambiguity_without_candidate_materialization(tmp_path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    for number in range(65):
        _report(reports / f"report_{number}.json", as_of=START + timedelta(seconds=number + 1))

    with pytest.raises(ResultValidationError, match="multiple"):
        ResultValidator(reports, tmp_path / "replays", tmp_path / "sealed").validate(
            "legacy-report-v1", Job(), exit_code=0, attempt_started_at=START,
            backend_commit=BACKEND_COMMIT,
            semantic_input_fingerprint=SEMANTIC_INPUT_FINGERPRINT,
        )


def test_result_scan_caps_every_examined_entry_and_reports_progress(monkeypatch, tmp_path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    for number in range(6):
        (reports / f"unrelated_{number}.txt").write_text("ignored", encoding="utf-8")
    monkeypatch.setattr("services.job_worker.results.MAX_RESULT_ENTRIES", 5)
    progress = []

    with pytest.raises(ResultValidationError, match="too many directory entries"):
        ResultValidator(reports, tmp_path / "replays").validate(
            "legacy-report-v1", Job(), exit_code=0, attempt_started_at=START,
            backend_commit=BACKEND_COMMIT,
            semantic_input_fingerprint=SEMANTIC_INPUT_FINGERPRINT,
            progress=lambda: progress.append("tick"),
        )

    assert len(progress) >= 5


def test_candidate_cap_counts_only_fresh_attributable_matches(monkeypatch, tmp_path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    for number in range(10):
        _report(reports / f"report_stale_{number}.json", attempt_id="other")
    _report(reports / "report_valid.json")
    monkeypatch.setattr("services.job_worker.results.MAX_RESULT_CANDIDATES", 1)

    result = ResultValidator(reports, tmp_path / "replays").validate(
        "legacy-report-v1", Job(), exit_code=0, attempt_started_at=START,
        backend_commit=BACKEND_COMMIT,
        semantic_input_fingerprint=SEMANTIC_INPUT_FINGERPRINT,
        progress=lambda: None,
    )

    assert result.sha256


def test_candidate_cap_bounds_fresh_attributable_invalid_files(monkeypatch, tmp_path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    for number in range(3):
        path = reports / f"report_invalid_{number}.json"
        path.write_text(json.dumps({
            "job_id": JOB_ID,
            "attempt_id": ATTEMPT_ID,
            "backend_commit": BACKEND_COMMIT,
            "research_only": True,
            "semantic_input_fingerprint": SEMANTIC_INPUT_FINGERPRINT,
        }), encoding="utf-8")
        os.utime(path, (START.timestamp() + 1, START.timestamp() + 1))
    monkeypatch.setattr("services.job_worker.results.MAX_RESULT_CANDIDATES", 2)

    with pytest.raises(ResultValidationError, match="too many fresh attributable"):
        ResultValidator(reports, tmp_path / "replays").validate(
            "legacy-report-v1", Job(), exit_code=0, attempt_started_at=START,
            backend_commit=BACKEND_COMMIT,
            semantic_input_fingerprint=SEMANTIC_INPUT_FINGERPRINT,
        )


def test_seal_short_writes_then_atomically_publishes_and_fsyncs_directory(monkeypatch, tmp_path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    _report(reports / "report_new.json")
    real_write = os.write
    real_fsync = os.fsync
    writes = []
    fsyncs = []

    def short_write(fd, data):
        writes.append(len(data))
        return real_write(fd, data[: max(1, len(data) // 2)])

    monkeypatch.setattr(os, "write", short_write)
    monkeypatch.setattr(os, "fsync", lambda fd: fsyncs.append(fd) or real_fsync(fd))
    result = ResultValidator(reports, tmp_path / "replays", tmp_path / "sealed").validate(
        "legacy-report-v1", Job(), exit_code=0, attempt_started_at=START,
        backend_commit=BACKEND_COMMIT,
        semantic_input_fingerprint=SEMANTIC_INPUT_FINGERPRINT,
    )

    assert len(writes) > 1
    assert len(fsyncs) >= 2
    assert (tmp_path / "sealed" / result.relative_ref).read_bytes() == (reports / "report_new.json").read_bytes()
    assert not (tmp_path / "sealed" / JOB_ID).exists()
    assert not list((tmp_path / "sealed").rglob("*.tmp"))


def test_seal_failure_unlinks_temp_and_leaks_no_descriptors(monkeypatch, tmp_path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    _report(reports / "report_new.json")
    validator = ResultValidator(reports, tmp_path / "replays", tmp_path / "sealed")
    before = len(os.listdir("/proc/self/fd"))
    monkeypatch.setattr(os, "rename", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("crash")))

    with pytest.raises(ResultValidationError, match="could not be sealed"):
        validator.validate(
            "legacy-report-v1", Job(), exit_code=0, attempt_started_at=START,
            backend_commit=BACKEND_COMMIT,
            semantic_input_fingerprint=SEMANTIC_INPUT_FINGERPRINT,
        )

    assert len(os.listdir("/proc/self/fd")) == before
    assert not list((tmp_path / "sealed").rglob("*.tmp"))


def test_existing_seal_is_verified_with_full_short_read_loop(monkeypatch, tmp_path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    source = reports / "report_new.json"
    _report(source)
    validator = ResultValidator(reports, tmp_path / "replays", tmp_path / "sealed")
    first = validator.validate(
        "legacy-report-v1", Job(), exit_code=0, attempt_started_at=START,
        backend_commit=BACKEND_COMMIT,
        semantic_input_fingerprint=SEMANTIC_INPUT_FINGERPRINT,
    )
    raw = source.read_bytes()
    real_read = os.read
    reads = []

    def short_read(fd, size):
        reads.append(size)
        return real_read(fd, min(size, 3))

    monkeypatch.setattr(os, "read", short_read)
    second = validator._seal(Job(), raw, "legacy-report-v1", {"job_id": "job_0123456789abcdef0123456789abcdef", "attempt_id": "attempt_fedcba9876543210fedcba9876543210"})

    assert second.sha256 == first.sha256
    assert len(reads) > 1


@pytest.mark.parametrize("failure", ["fstat", "fchmod"])
def test_seal_closes_new_child_fd_when_validation_fails(monkeypatch, tmp_path, failure) -> None:
    validator = ResultValidator(tmp_path / "reports", tmp_path / "replays", tmp_path / "sealed")
    raw = b"{}"
    real_open = os.open
    real_fstat = os.fstat
    real_fchmod = os.fchmod
    child_fds = []

    def tracking_open(path, *args, **kwargs):
        fd = real_open(path, *args, **kwargs)
        if path == "job_0123456789abcdef0123456789abcdef": child_fds.append(fd)
        return fd

    def failing_fchmod(fd, mode):
        if failure == "fchmod" and fd in child_fds: raise OSError("fchmod failed")
        return real_fchmod(fd, mode)

    def failing_fstat(fd):
        if failure == "fstat" and fd in child_fds: raise OSError("fstat failed")
        return real_fstat(fd)

    monkeypatch.setattr(os, "open", tracking_open)
    monkeypatch.setattr(os, "fstat", failing_fstat)
    monkeypatch.setattr(os, "fchmod", failing_fchmod)
    with pytest.raises(ResultValidationError):
        validator._seal(Job(), raw, "legacy-report-v1", {})
    assert child_fds
    with pytest.raises(OSError): real_fstat(child_fds[0])


def test_seal_fsyncs_each_parent_immediately_after_mkdir_and_leaf_after_rename(monkeypatch, tmp_path) -> None:
    validator = ResultValidator(tmp_path / "reports", tmp_path / "replays", tmp_path / "sealed")
    events = []
    real_mkdir = os.mkdir
    real_fsync = os.fsync
    real_rename = os.rename

    def mkdir(path, *args, **kwargs):
        result = real_mkdir(path, *args, **kwargs)
        if path in {"job_0123456789abcdef0123456789abcdef", "attempt_fedcba9876543210fedcba9876543210"}: events.append(("mkdir", path))
        return result

    monkeypatch.setattr(os, "mkdir", mkdir)
    monkeypatch.setattr(os, "fsync", lambda fd: events.append(("fsync", fd)) or real_fsync(fd))
    monkeypatch.setattr(os, "rename", lambda *args, **kwargs: events.append(("rename", args[1])) or real_rename(*args, **kwargs))
    validator._seal(Job(), b"{}", "legacy-report-v1", {})

    job_mkdir = events.index(("mkdir", "job_0123456789abcdef0123456789abcdef"))
    attempt_mkdir = events.index(("mkdir", "attempt_fedcba9876543210fedcba9876543210"))
    rename = next(index for index, event in enumerate(events) if event[0] == "rename")
    assert events[job_mkdir + 1][0] == "fsync"
    assert events[attempt_mkdir + 1][0] == "fsync"
    assert events[rename + 1][0] == "fsync"


def test_first_use_sealed_root_creation_fsyncs_parent_and_failure_closes_fds(monkeypatch, tmp_path) -> None:
    validator = ResultValidator(tmp_path / "reports", tmp_path / "replays", tmp_path / "sealed")
    real_mkdir = os.mkdir
    real_fsync = os.fsync
    events = []
    failures = [1]
    before = len(os.listdir("/proc/self/fd"))

    def mkdir(path, *args, **kwargs):
        if path == "sealed": events.append("mkdir-root")
        return real_mkdir(path, *args, **kwargs)

    def fsync(fd):
        if events and events[-1] == "mkdir-root":
            events.append("fsync-parent")
            if failures.pop(0) if failures else 0:
                raise OSError("parent fsync failed")
        return real_fsync(fd)

    monkeypatch.setattr(os, "mkdir", mkdir)
    monkeypatch.setattr(os, "fsync", fsync)
    with pytest.raises(ResultValidationError):
        validator._seal(Job(), b"{}", "legacy-report-v1", {})

    assert events == ["mkdir-root", "fsync-parent"]
    assert len(os.listdir("/proc/self/fd")) == before

    validator._seal(Job(), b"{}", "legacy-report-v1", {})
    assert events[:4] == ["mkdir-root", "fsync-parent", "mkdir-root", "fsync-parent"]


@pytest.mark.parametrize("case", ["nonzero", "missing", "stale", "wrong-attempt", "invalid", "ambiguous"])
def test_report_validation_rejects_every_incomplete_success_case(tmp_path, case) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    exit_code = 1 if case == "nonzero" else 0
    if case not in {"missing", "nonzero"}:
        path = reports / "report_one.json"
        if case == "invalid":
            path.write_text('{"attempt_id":"attempt_fedcba9876543210fedcba9876543210","assets":"bad"}', encoding="utf-8")
        else:
            _report(
                path,
                attempt_id="other" if case == "wrong-attempt" else "attempt_fedcba9876543210fedcba9876543210",
                as_of=START - timedelta(seconds=1) if case == "stale" else START + timedelta(seconds=1),
            )
    if case == "ambiguous":
        _report(reports / "report_two.json", as_of=START + timedelta(seconds=2))

    with pytest.raises(ResultValidationError) as raised:
        ResultValidator(reports, tmp_path / "replays").validate(
            "legacy-report-v1", Job(), exit_code=exit_code,
            attempt_started_at=START, backend_commit=BACKEND_COMMIT,
            semantic_input_fingerprint=SEMANTIC_INPUT_FINGERPRINT,
        )

    assert raised.value.reason_code == "RESULT_VALIDATION_FAILED"
    assert raised.value.reconciliation_required is (case == "ambiguous")


def test_replay_must_be_nonempty_fresh_and_match_exact_session(tmp_path) -> None:
    replays = tmp_path / "replays"
    replays.mkdir()
    path = replays / "replay_session-7.json"
    path.write_text(json.dumps({
        "job_id": JOB_ID,
        "attempt_id": ATTEMPT_ID,
        "backend_commit": BACKEND_COMMIT,
        "session_id": "session-7",
        "event_count": 1,
        "events": [{"type": "init", "size_bytes": 1}],
    }))
    os.utime(path, (START.timestamp() + 1, START.timestamp() + 1))
    job = Job()
    job.job_type = JobType.REPLAY
    job.payload = ReplayPayload(session_id="session-7")

    accepted = ResultValidator(tmp_path / "reports", replays).validate(
        "legacy-replay-v1", job, exit_code=0, attempt_started_at=START,
        backend_commit=BACKEND_COMMIT,
    )
    assert accepted.validation_metadata["session_id"] == "session-7"

    job.payload = ReplayPayload(session_id="different")
    with pytest.raises(ResultValidationError):
        ResultValidator(tmp_path / "reports", replays).validate(
            "legacy-replay-v1", job, exit_code=0, attempt_started_at=START,
            backend_commit=BACKEND_COMMIT,
        )
