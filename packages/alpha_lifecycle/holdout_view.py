"""Bounded immutable calculation transport; never disclosure or custody authority."""
from __future__ import annotations

import hashlib
import os
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
from packages.engine_contracts.serialization import canonical_json_bytes


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
        self._owner_pid = os.getpid()
        self._closed = False
        self._lifetime: Callable[[], None] | None = None
        if not isinstance(raw,bytes) or not 0<len(raw)<=MAX_VIEW_BYTES:
            raise ValueError('holdout calculation view exceeds its transport bound')
        records=TypeAdapter(dict[str, str]).validate_json(raw, strict=True)
        if (not isinstance(records,dict) or canonical_json_bytes(records)!=raw
            or any(not isinstance(value,str) for value in records.values())):
            raise ValueError('holdout calculation view is not canonical')
        self._records=MappingProxyType({name:value.encode('utf-8') for name,value in records.items()})
        if build_holdout_calculation_view(manifest_ref,spec_ref,self)!=raw:
            raise ValueError('holdout calculation view contains unexpected artifacts')
        self._raw=raw

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
        if raw is None or len(raw)!=ref.size_bytes or hashlib.sha256(raw).hexdigest()!=ref.content_sha256:
            raise ValueError('holdout calculation artifact is missing or differs')
        return raw

    def put_bytes(self, value: bytes, *, media_type: str) -> ArtifactRefV1:
        raise ValueError('holdout calculation view is immutable')
