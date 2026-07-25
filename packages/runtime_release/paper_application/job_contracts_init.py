"""Canonical paper-only job contract surface."""

from .api import ActorIdentity, ArtifactMetadata, AttemptMetadata, EnqueueJobBody, EnqueueJobRequest, EventMetadata, JobDetail, JobMetadata
from .enums import ACTIVE_JOB_STATES, ActorType, JobState, JobType, TERMINAL_JOB_STATES
from .fingerprint import canonical_payload_json, payload_fingerprint
from .payloads import JobPayload, SnapshotPayload, parse_payload
from .transitions import InvalidTransition, validate_transition

__all__ = [
    "ACTIVE_JOB_STATES", "ActorIdentity", "ActorType", "ArtifactMetadata",
    "AttemptMetadata", "EnqueueJobBody", "EnqueueJobRequest", "EventMetadata",
    "InvalidTransition", "JobDetail", "JobMetadata", "JobPayload", "JobState",
    "JobType", "SnapshotPayload", "TERMINAL_JOB_STATES",
    "canonical_payload_json", "parse_payload", "payload_fingerprint",
    "validate_transition",
]
