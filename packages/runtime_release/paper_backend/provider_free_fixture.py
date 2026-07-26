"""Spawn-bound, candidate-bound deterministic market fixture authority."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
from types import MappingProxyType
from typing import Mapping, NoReturn


PACKAGE6_FIXTURE_AUTHORITY_PATH_ENV = "TRADING_PACKAGE6_FIXTURE_AUTHORITY_PATH"
PACKAGE6_APPROVAL_SHA256_ENV = "TRADING_PACKAGE6_APPROVAL_SHA256"
_AUTHORITY_FIELDS = {
    "schema_version",
    "classification",
    "package6_approval_sha256",
    "backend_commit",
    "generated_at",
    "expires_at",
    "fixture_path",
    "fixture_sha256",
}
_FIXTURE_FIELDS = {"schema_version", "provenance", "as_of", "assets"}
_ASSET_FIELDS = {
    "current_price",
    "market_cap",
    "total_volume",
    "price_change_percentage_24h",
}
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SYMBOL = re.compile(r"[A-Z0-9]{2,10}\Z")


class FixtureAuthorityError(RuntimeError):
    """The fixed Package 6 fixture authority is absent, stale, or invalid."""


@dataclass(frozen=True, slots=True)
class ProviderFreeFixture:
    market: Mapping[str, Mapping[str, object]]
    provenance: str
    sha256: str
    as_of: str


def _fail(message: str) -> NoReturn:
    raise FixtureAuthorityError(message)


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        _fail("fixture timestamp is invalid")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        _fail("fixture timestamp is invalid")
    return parsed


def _read_regular(path: Path, *, maximum: int, trusted_uid: int | None) -> bytes:
    if not path.is_absolute() or ".." in path.parts or str(path) != os.path.normpath(path):
        _fail("fixture authority path is not canonical")
    try:
        info = path.lstat()
    except OSError:
        _fail("fixture authority file is unavailable")
    expected_uid = os.geteuid() if trusted_uid is None else trusted_uid
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != expected_uid
        or stat.S_IMODE(info.st_mode) != 0o444
        or info.st_size > maximum
    ):
        _fail("fixture authority file policy is invalid")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        try:
            observed = os.fstat(descriptor)
            if (observed.st_dev, observed.st_ino) != (info.st_dev, info.st_ino):
                _fail("fixture authority identity changed")
            raw = os.read(descriptor, maximum + 1)
        finally:
            os.close(descriptor)
    except OSError:
        _fail("fixture authority file cannot be read safely")
    if len(raw) > maximum:
        _fail("fixture authority file is oversized")
    return raw


def load_provider_free_fixture(
    authority_path: Path,
    *,
    expected_backend_commit: str,
    expected_package6_approval_sha256: str,
    now: datetime | None = None,
    trusted_uid: int | None = None,
) -> ProviderFreeFixture:
    if not isinstance(expected_backend_commit, str) or _COMMIT.fullmatch(
        expected_backend_commit
    ) is None:
        _fail("expected backend candidate is invalid")
    if (
        not isinstance(expected_package6_approval_sha256, str)
        or _SHA256.fullmatch(expected_package6_approval_sha256) is None
    ):
        _fail("expected Package 6 approval is invalid")
    current = datetime.now(UTC) if now is None else now
    if current.tzinfo is None:
        _fail("fixture validation clock is invalid")
    try:
        authority = json.loads(
            _read_regular(authority_path, maximum=64 * 1024, trusted_uid=trusted_uid)
        )
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("fixture authority JSON is invalid")
    if not isinstance(authority, dict) or set(authority) != _AUTHORITY_FIELDS:
        _fail("fixture authority schema is invalid")
    generated = _timestamp(authority["generated_at"])
    expires = _timestamp(authority["expires_at"])
    if (
        authority["schema_version"] != 1
        or authority["classification"] != "PACKAGE6_PROVIDER_FREE_FIXTURE"
        or authority["backend_commit"] != expected_backend_commit
        or authority["package6_approval_sha256"]
        != expected_package6_approval_sha256
        or expires <= generated
        or expires - generated > timedelta(minutes=30)
        or not generated <= current <= expires
        or not isinstance(authority["fixture_sha256"], str)
        or _SHA256.fullmatch(authority["fixture_sha256"]) is None
    ):
        _fail("fixture authority policy or candidate binding is invalid")
    fixture_path_value = authority["fixture_path"]
    if not isinstance(fixture_path_value, str):
        _fail("fixture path is invalid")
    fixture_path = Path(fixture_path_value)
    if fixture_path.parent != authority_path.parent:
        _fail("fixture must share the private authority directory")
    raw = _read_regular(fixture_path, maximum=1024 * 1024, trusted_uid=trusted_uid)
    digest = hashlib.sha256(raw).hexdigest()
    if digest != authority["fixture_sha256"]:
        _fail("fixture digest does not match")
    try:
        fixture = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("fixture JSON is invalid")
    if not isinstance(fixture, dict) or set(fixture) != _FIXTURE_FIELDS:
        _fail("fixture schema is invalid")
    _timestamp(fixture["as_of"])
    assets = fixture["assets"]
    if (
        fixture["schema_version"] != 1
        or fixture["provenance"] != "DETERMINISTIC_PROVIDER_FREE_V1"
        or not isinstance(assets, dict)
        or not 1 <= len(assets) <= 32
    ):
        _fail("fixture provenance or asset set is invalid")
    market: dict[str, Mapping[str, object]] = {}
    for symbol, value in assets.items():
        if (
            not isinstance(symbol, str)
            or _SYMBOL.fullmatch(symbol) is None
            or not isinstance(value, dict)
            or set(value) != _ASSET_FIELDS
            or any(
                item is not None
                and (
                    isinstance(item, bool)
                    or not isinstance(item, (int, float))
                    or not math.isfinite(float(item))
                )
                for item in value.values()
            )
            or any(
                value[key] is not None and value[key] < 0
                for key in ("current_price", "market_cap", "total_volume")
            )
        ):
            _fail("fixture asset is invalid")
        market[symbol] = MappingProxyType(dict(value))
    return ProviderFreeFixture(
        market=MappingProxyType(market),
        provenance="DETERMINISTIC_PROVIDER_FREE_V1",
        sha256=digest,
        as_of=fixture["as_of"],
    )


__all__ = [
    "FixtureAuthorityError",
    "PACKAGE6_APPROVAL_SHA256_ENV",
    "PACKAGE6_FIXTURE_AUTHORITY_PATH_ENV",
    "ProviderFreeFixture",
    "load_provider_free_fixture",
]
