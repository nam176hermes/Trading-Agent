from __future__ import annotations

import os

import pytest

from control_api.repositories import _legacy_files
from control_api.repositories._legacy_files import LegacyFileError, iter_jsonl, read_text


def test_bounded_reader_rejects_file_replaced_between_lstat_and_open(tmp_path, monkeypatch) -> None:
    source = tmp_path / "evidence.json"
    replacement = tmp_path / "replacement.json"
    source.write_text("trusted", encoding="utf-8")
    replacement.write_text("swapped", encoding="utf-8")
    real_open = os.open
    swapped = False

    def swapping_open(path, flags, *args, **kwargs):
        nonlocal swapped
        if not swapped and path == source.name:
            swapped = True
            source.unlink()
            replacement.replace(source)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(_legacy_files.os, "open", swapping_open)

    with pytest.raises(LegacyFileError, match="changed"):
        read_text(source, max_bytes=64)


@pytest.mark.parametrize("reader", ["text", "jsonl"])
def test_bounded_readers_translate_os_read_failures(tmp_path, monkeypatch, reader) -> None:
    source = tmp_path / "evidence.jsonl"
    source.write_text('{"ok":true}\n', encoding="utf-8")

    def failing_read(_descriptor, _size):
        raise OSError("simulated read failure")

    monkeypatch.setattr(_legacy_files.os, "read", failing_read)

    with pytest.raises(LegacyFileError, match="cannot be read"):
        if reader == "text":
            read_text(source, max_bytes=64)
        else:
            list(iter_jsonl(source, max_bytes=64, max_line_bytes=64, max_records=2))


def test_bounded_reader_rejects_symlinked_parent(tmp_path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "evidence.json").write_text("trusted", encoding="utf-8")
    linked_parent = tmp_path / "linked"
    linked_parent.symlink_to(outside, target_is_directory=True)

    with pytest.raises(LegacyFileError):
        read_text(linked_parent / "evidence.json", max_bytes=64)
