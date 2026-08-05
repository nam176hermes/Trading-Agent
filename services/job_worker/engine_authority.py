"""Worker-owned derivation of engine command authority after a fenced claim."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Callable
from uuid import NAMESPACE_URL, UUID, uuid5

from packages.engine_contracts import (
    CURRENT_SCHEMA_VERSION,
    EngineCommandEnvelope,
    RunBacktest,
    payload_digest,
)
from packages.job_contracts import EngineBacktestPayload, JobType
from services.job_store.worker_repository import ClaimedJob


ENGINE_COMMAND_PRODUCER_IDENTITY = "trading-job-worker"
_SOURCE_COMMIT = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$", re.ASCII)
_JOB_ID = re.compile(r"^job_[0-9a-f]{32}$", re.ASCII)
_ATTEMPT_ID = re.compile(r"^attempt_[0-9a-f]{32}$", re.ASCII)
_WORKER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$", re.ASCII)
_LEASE_TOKEN = re.compile(r"^[A-Za-z0-9_-]{32,128}$", re.ASCII)
_UUID_NAME_PREFIX = "trading-agent:engine-command:v1"


def _attempt_uuid(claimed: ClaimedJob, purpose: str) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        f"{_UUID_NAME_PREFIX}:{claimed.job_id}:{claimed.attempt_id}:{purpose}",
    )


def _canonical_worker_time(value: object, *, error: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
    ):
        raise ValueError(error)
    return value.astimezone(UTC)


class BacktestEngineAuthorityFactory:
    def __init__(self, *, code_commit: str, clock: Callable[[], datetime]) -> None:
        if (
            not isinstance(code_commit, str)
            or _SOURCE_COMMIT.fullmatch(code_commit) is None
        ):
            raise ValueError("worker code commit is invalid")
        if not callable(clock):
            raise TypeError("worker clock must be callable")
        self._code_commit = code_commit
        self._clock = clock

    def from_claim(self, claimed: ClaimedJob) -> EngineCommandEnvelope:
        if not isinstance(claimed, ClaimedJob):
            raise TypeError("engine authority requires a ClaimedJob")
        if claimed.job_type is not JobType.BACKTEST:
            raise ValueError("claimed BACKTEST job is required")
        if type(claimed.payload) is not EngineBacktestPayload:
            raise ValueError("engine backtest authority input is required")
        if (
            not isinstance(claimed.job_id, str)
            or _JOB_ID.fullmatch(claimed.job_id) is None
            or not isinstance(claimed.attempt_id, str)
            or _ATTEMPT_ID.fullmatch(claimed.attempt_id) is None
            or not isinstance(claimed.worker_id, str)
            or _WORKER_ID.fullmatch(claimed.worker_id) is None
        ):
            raise ValueError("claim identity is invalid")
        if (
            not isinstance(claimed.lease_token, str)
            or _LEASE_TOKEN.fullmatch(claimed.lease_token) is None
        ):
            raise ValueError("claim fence is invalid")
        if (
            isinstance(claimed.attempt_number, bool)
            or not isinstance(claimed.attempt_number, int)
            or isinstance(claimed.max_attempts, bool)
            or not isinstance(claimed.max_attempts, int)
            or not 1 <= claimed.attempt_number <= claimed.max_attempts
        ):
            raise ValueError("claim attempt boundary is invalid")

        now = _canonical_worker_time(
            self._clock(), error="worker clock returned an invalid time"
        )
        lease_expires_at = _canonical_worker_time(
            claimed.lease_expires_at, error="claim lease time is invalid"
        )
        if lease_expires_at <= now:
            raise ValueError("claim lease is expired")

        engine_input = claimed.payload.engine_backtest
        command = RunBacktest(
            command_type="RunBacktest",
            engine_configuration=engine_input.engine_configuration,
            instrument_catalog=engine_input.instrument_catalog,
            strategy_configuration=engine_input.strategy_configuration,
            market_data=engine_input.market_data,
            start_time=engine_input.start_time,
            end_time=engine_input.end_time,
        )
        command_digest = payload_digest(command)
        return EngineCommandEnvelope(
            message_id=_attempt_uuid(claimed, "message"),
            correlation_id=_attempt_uuid(claimed, "correlation"),
            causation_id=_attempt_uuid(claimed, "causation"),
            engine_run_id=_attempt_uuid(claimed, "engine-run"),
            stream_sequence=1,
            event_time=now,
            initialization_time=now,
            schema_version=CURRENT_SCHEMA_VERSION,
            producer_identity=ENGINE_COMMAND_PRODUCER_IDENTITY,
            source_commit=self._code_commit,
            config_digest=command_digest,
            payload_digest=command_digest,
            payload=command,
        )
