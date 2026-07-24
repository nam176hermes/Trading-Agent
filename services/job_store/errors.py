from __future__ import annotations


class JobStoreError(RuntimeError):
    code = "JOB_STORE_ERROR"


class IdempotencyConflict(JobStoreError):
    code = "IDEMPOTENCY_CONFLICT"

    def __init__(self, job_type: str, idempotency_key: str) -> None:
        self.job_type = job_type
        self.idempotency_key = idempotency_key
        super().__init__(
            "idempotency identity already belongs to a different request"
        )


class JobNotFound(JobStoreError):
    code = "JOB_NOT_FOUND"


class StaleTransition(JobStoreError):
    code = "STALE_TRANSITION"


class InvalidTraceId(JobStoreError):
    code = "INVALID_TRACE_ID"


class InvalidJobFilters(JobStoreError, ValueError):
    code = "INVALID_JOB_FILTERS"
