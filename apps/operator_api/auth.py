"""Descriptor-safe two-principal bearer authentication."""

from __future__ import annotations

import hmac
import os
import stat
from pathlib import Path

from starlette.types import Scope

from packages.operator_control.contracts import OperatorActorV1

from .config import OperatorApiConfigurationError, OperatorApiSettings
from .errors import OperatorApiError


_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_TOKEN_FLAGS = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC
_MAX_TOKEN_BYTES = 4096


def _directory_identity(info: os.stat_result) -> tuple[int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_mode, info.st_uid


def _file_identity(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _require_safe_directory(info: os.stat_result) -> None:
    mode = stat.S_IMODE(info.st_mode)
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid not in {0, os.geteuid()}
        or (mode & 0o022 and not (info.st_uid == 0 and mode & stat.S_ISVTX))
    ):
        raise OperatorApiConfigurationError(
            "operator credential authority is unavailable"
        )


def _open_parent(path: Path) -> tuple[int, tuple[int, int, int, int]]:
    descriptor = os.open("/", _DIRECTORY_FLAGS)
    try:
        for part in path.parent.parts[1:]:
            child = os.open(part, _DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
            _require_safe_directory(os.fstat(descriptor))
        info = os.fstat(descriptor)
        _require_safe_directory(info)
        named = os.stat(path.parent, follow_symlinks=False)
        if _directory_identity(info) != _directory_identity(named):
            raise OperatorApiConfigurationError(
                "operator credential authority is unavailable"
            )
        return descriptor, _directory_identity(info)
    except Exception:
        os.close(descriptor)
        raise


def _canonical_path(path: Path) -> Path:
    raw = os.fspath(path)
    candidate = Path(raw)
    if (
        not candidate.is_absolute()
        or candidate.anchor != "/"
        or candidate == Path("/")
        or os.path.normpath(raw) != raw
        or candidate.as_posix() != raw
        or any(part in {"", ".", ".."} for part in candidate.parts[1:])
    ):
        raise OperatorApiConfigurationError(
            "operator credential authority is unavailable"
        )
    return candidate


def load_private_token(path: Path) -> bytes:
    parent = file_descriptor = -1
    try:
        target = _canonical_path(path)
        parent, parent_identity = _open_parent(target)
        file_descriptor = os.open(target.name, _TOKEN_FLAGS, dir_fd=parent)
        before = os.fstat(file_descriptor)
        mode = stat.S_IMODE(before.st_mode)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or mode & ~0o600
            or before.st_size > _MAX_TOKEN_BYTES + 1
        ):
            raise OperatorApiConfigurationError(
                "operator credential authority is unavailable"
            )
        chunks: list[bytes] = []
        observed = 0
        while observed <= _MAX_TOKEN_BYTES + 1:
            block = os.read(
                file_descriptor, min(4096, _MAX_TOKEN_BYTES + 2 - observed)
            )
            if not block:
                break
            chunks.append(block)
            observed += len(block)
        if observed > _MAX_TOKEN_BYTES + 1:
            raise OperatorApiConfigurationError(
                "operator credential authority is unavailable"
            )
        after = os.fstat(file_descriptor)
        named = os.stat(target.name, dir_fd=parent, follow_symlinks=False)
        if (
            _file_identity(before) != _file_identity(after)
            or _file_identity(before) != _file_identity(named)
        ):
            raise OperatorApiConfigurationError(
                "operator credential authority is unavailable"
            )
        reopened, reopened_identity = _open_parent(target)
        os.close(reopened)
        if reopened_identity != parent_identity:
            raise OperatorApiConfigurationError(
                "operator credential authority is unavailable"
            )
        raw = b"".join(chunks)
        token = raw[:-1] if raw.endswith(b"\n") else raw
        if (
            not (32 <= len(token) <= _MAX_TOKEN_BYTES)
            or b"\n" in token
            or any(byte < 0x21 or byte > 0x7E for byte in token)
        ):
            raise OperatorApiConfigurationError(
                "operator credential authority is unavailable"
            )
        return token
    except OperatorApiConfigurationError:
        raise
    except (OSError, TypeError, ValueError):
        raise OperatorApiConfigurationError(
            "operator credential authority is unavailable"
        ) from None
    finally:
        if file_descriptor >= 0:
            os.close(file_descriptor)
        if parent >= 0:
            os.close(parent)


class OperatorAuthenticator:
    def __init__(self, settings: OperatorApiSettings) -> None:
        self._web_token = load_private_token(settings.web_token_file)
        self._cli_token = load_private_token(settings.cli_token_file)
        if hmac.compare_digest(self._web_token, self._cli_token):
            raise OperatorApiConfigurationError(
                "operator credential authority is unavailable"
            )
        self._web_principal = settings.web_principal_id
        self._cli_principal = settings.cli_principal_id

    def authenticate(self, scope: Scope) -> OperatorActorV1:
        values = [
            value
            for key, value in scope.get("headers", ())
            if key.lower() == b"authorization"
        ]
        valid = len(values) == 1
        raw = values[0] if valid else b""
        scheme, separator, credential = raw.partition(b" ")
        valid = bool(
            valid
            and separator
            and scheme.lower() == b"bearer"
            and credential
            and b" " not in credential
        )
        candidate = credential if valid else b""
        web = hmac.compare_digest(candidate, self._web_token)
        cli = hmac.compare_digest(candidate, self._cli_token)
        if not valid or web == cli:
            raise OperatorApiError(
                401,
                "AUTHENTICATION_REQUIRED",
                "Valid bearer authentication is required.",
            )
        return OperatorActorV1(
            schema_version="operator-actor-v1",
            principal_id=self._web_principal if web else self._cli_principal,
            interface="WEB" if web else "CLI",
        )


__all__ = ["OperatorAuthenticator", "load_private_token"]
