"""Reviewed output samples from the Phase 4B research-only backend."""

from __future__ import annotations


BACKEND_COMMIT = "41f055b48033714c660f44cc20498b7545366e75"
JOB_ID = "job_0123456789abcdef0123456789abcdef"
ATTEMPT_ID = "attempt_fedcba9876543210fedcba9876543210"
SESSION_ID = "Session_2026-07-12"
SEMANTIC_INPUT_FINGERPRINT = "a" * 64

REPORT_SAMPLE = {
    "timestamp": "2026-07-12T12:00:01+00:00",
    "assets": [{"symbol": "BTC", "suggestion": "BUY", "confidence": 0.8}],
    "job_id": JOB_ID,
    "attempt_id": ATTEMPT_ID,
    "research_only": True,
    "backend_commit": BACKEND_COMMIT,
    "semantic_input_fingerprint": SEMANTIC_INPUT_FINGERPRINT,
}

REPLAY_SIDECAR_SAMPLE = {
    "job_id": JOB_ID,
    "attempt_id": ATTEMPT_ID,
    "backend_commit": BACKEND_COMMIT,
    "session_id": SESSION_ID,
    "event_count": 2,
    "events": [
        {"type": "init", "timestamp": "2026-07-12T12:00:00+00:00", "size_bytes": 128},
        {
            "type": "tool_result",
            "timestamp": "2026-07-12T12:00:01+00:00",
            "status": "success",
            "size_bytes": 256,
        },
    ],
}
