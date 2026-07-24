from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def record_key(
    domain: str, source_hash: str, source_record_index: int, normalization_version: str
) -> str:
    material = "\0".join(
        (domain, source_hash, str(source_record_index), normalization_version)
    )
    return sha256_bytes(material.encode("utf-8"))


def chunk_ranges(total: int, chunk_size: int = 500) -> Iterator[tuple[int, int]]:
    if total < 0 or chunk_size < 1:
        raise ValueError("invalid chunk bounds")
    for first in range(1, total + 1, chunk_size):
        yield first, min(total, first + chunk_size - 1)
