"""Bounded immutable calculation transport; never disclosure or custody authority."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
from collections.abc import Callable
from pydantic import TypeAdapter
from types import MappingProxyType

from packages.alpha_lifecycle.contracts.execution import HoldoutManifest, InstrumentSpec
from packages.alpha_lifecycle.evaluation import _holdout_regime
from packages.alpha_lifecycle.executable_reference import _execution_inputs
from packages.alpha_lifecycle.pit_evidence import _ReadBudget, _reference
from packages.alpha_lifecycle.replica_store import ArtifactStore, _read
from packages.alpha_lifecycle.sandbox_policy import MAX_VIEW_BYTES
from packages.data_contracts import ArtifactRefV1
from packages.data_catalog.artifact_store import ArtifactIntegrityError
from packages.engine_contracts.serialization import canonical_json_bytes


def read_calculation_view(path: Path) -> bytes:
    """Read a private snapshot, including Bubblewrap's anonymous read-only mount.

    The sandbox owns this path. This is not a reader for operator state files.
    Named source-test snapshots retain the single-link/private-file requirement.
    """
    def identity(info: os.stat_result) -> tuple[int, ...]:
        return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        parent = os.fstat(directory)
        if parent.st_uid != os.geteuid() or stat.S_IMODE(parent.st_mode) != 0o700:
            raise ValueError('calculation view directory is not private')
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=directory)
        try:
            before = os.fstat(fd)
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid()
                or before.st_nlink not in (0, 1) or stat.S_IMODE(before.st_mode) != 0o600
                or not 0 < before.st_size <= MAX_VIEW_BYTES
                or (before.st_nlink == 0 and not os.fstatvfs(fd).f_flag & os.ST_RDONLY)):
                raise ValueError('calculation view metadata is unsafe')
            raw = os.pread(fd, before.st_size + 1, 0)
            if (len(raw) != before.st_size or identity(os.fstat(fd)) != identity(before)
                or identity(os.stat(path.name, dir_fd=directory, follow_symlinks=False)) != identity(before)
                or identity(os.stat(path.parent, follow_symlinks=False)) != identity(parent)):
                raise ValueError('calculation view changed during read')
            return raw
        finally:
            os.close(fd)
    finally:
        os.close(directory)


def build_holdout_calculation_view(
    manifest_ref: ArtifactRefV1, spec_ref: ArtifactRefV1, store: ArtifactStore,
) -> bytes:
    """Copy only calculator reads after the caller has authorized plaintext access.

    Record validation reads, not evaluation outputs or transitive provenance.
    One transport descriptor preserves the fixed 256-descriptor child limit.
    """
    records: dict[str,str] = {}
    class Reader(_ReadBudget):
        def read_bytes(self, ref: ArtifactRefV1) -> bytes:
            raw=super().read_bytes(ref)
            records[ref.locator]=raw.decode('utf-8')
            return raw
    reader=Reader(store)
    _reference(manifest_ref,65536)
    _reference(spec_ref,65536)
    manifest=_read(reader,manifest_ref,HoldoutManifest)
    spec=_read(reader,spec_ref,InstrumentSpec)
    _execution_inputs(manifest,spec,reader)
    _holdout_regime(manifest,reader)
    raw=canonical_json_bytes(records)
    if len(records)!=681 or len(raw)>MAX_VIEW_BYTES:
        raise ValueError('holdout calculation view exceeds its transport bound')
    return raw


class HoldoutCalculationView:
    """Exact immutable input bytes with the existing ArtifactStore read interface."""

    def __init__(self, raw: bytes, manifest_ref: ArtifactRefV1, spec_ref: ArtifactRefV1) -> None:
        self._owner_pid: int = os.getpid()
        self._closed: bool = False
        self._lifetime: Callable[[], None] | None = None
        if not isinstance(raw,bytes) or not 0<len(raw)<=MAX_VIEW_BYTES:
            raise ValueError('holdout calculation view exceeds its transport bound')
        records=TypeAdapter(dict[str, str]).validate_json(raw, strict=True)
        if (not isinstance(records,dict) or canonical_json_bytes(records)!=raw
            or any(not isinstance(value,str) for value in records.values())):
            raise ValueError('holdout calculation view is not canonical')
        self._records: MappingProxyType[str, bytes] = MappingProxyType({name:value.encode('utf-8') for name,value in records.items()})
        if build_holdout_calculation_view(manifest_ref,spec_ref,self)!=raw:
            raise ValueError('holdout calculation view contains unexpected artifacts')
        self._raw: bytes = raw

    @property
    def raw(self) -> bytes:
        self._check_open()
        return self._raw

    def _check_open(self) -> None:
        if self._closed or os.getpid() != self._owner_pid:
            raise ValueError('holdout calculation view is closed or belongs to another process')
        if self._lifetime is not None:
            try:
                self._lifetime()
            except BaseException:
                self.close()
                raise

    def bind_lifetime(self, check: Callable[[], None]) -> None:
        self._check_open()
        if self._lifetime is not None or not callable(check):
            raise ValueError('holdout lifetime cannot be replaced')
        self._lifetime = check

    def close(self) -> None:
        """Revoke this reader and drop its bytes; this is not memory zeroization.

        Previously copied bytes and already launched children need their own
        bounded lifetime. The parent must stop children before closing a view.
        """
        self._closed = True
        self._records = MappingProxyType({})
        self._raw = b''
        self._lifetime = None

    def read_bytes(self, ref: ArtifactRefV1) -> bytes:
        self._check_open()
        ref=ArtifactRefV1.model_validate(ref)
        _reference(ref,MAX_VIEW_BYTES)
        raw=self._records.get(ref.locator)
        if raw is None:
            raise ArtifactIntegrityError('holdout calculation artifact is missing') from FileNotFoundError(ref.locator)
        if len(raw)!=ref.size_bytes or hashlib.sha256(raw).hexdigest()!=ref.content_sha256:
            raise ValueError('holdout calculation artifact is missing or differs')
        return raw

    def put_bytes(self, value: bytes, *, media_type: str) -> ArtifactRefV1:
        raise ValueError('holdout calculation view is immutable')
