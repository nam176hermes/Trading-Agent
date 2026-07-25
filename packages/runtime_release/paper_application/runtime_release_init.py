"""Canonical paper application runtime authority surface."""

from .config import ProtectedAuthorityError
from .job_plane import ValidatedJobPlaneAuthority, validate_job_plane_authority

__all__ = ["ProtectedAuthorityError", "ValidatedJobPlaneAuthority", "validate_job_plane_authority"]
