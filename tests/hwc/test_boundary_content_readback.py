"""Repeated admission reuses pure parsing while reading current boundary bytes."""
import os

import pytest

from scripts import check_hwc_boundaries as boundary
from tests.hwc.fixtures.boundary_repo import make_boundary_repo


def test_repeated_complete_scan_does_not_reparse_unchanged_imports(tmp_path, monkeypatch):
    root = make_boundary_repo(tmp_path)
    target = root / 'packages/domain/current.py'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('import decimal\n', encoding='utf-8')
    parse = boundary.ast.parse
    calls = []

    def observe(source, filename='<unknown>', mode='exec', **kwargs):
        if filename == str(target):
            calls.append(source)
        return parse(source, filename=filename, mode=mode, **kwargs)

    monkeypatch.setattr(boundary.ast, 'parse', observe)
    for _ in range(3):
        assert boundary.evaluate_hwc_boundaries(root).passed
    assert calls == ['import decimal\n']


@pytest.mark.parametrize('changed', ['import apps.xx\n', 'import broken(\n'])
def test_same_length_same_mtime_changed_imports_still_fail(tmp_path, changed):
    root = make_boundary_repo(tmp_path)
    target = root / 'packages/domain/current.py'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('import decimal\n', encoding='utf-8')
    assert boundary.evaluate_hwc_boundaries(root).passed
    before = target.stat()
    target.write_text(changed, encoding='utf-8')
    os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert target.stat().st_size == before.st_size
    assert 'HWC_E_CORE_IMPORTS_INTERFACE' in {
        v.code for v in boundary.evaluate_hwc_boundaries(root).violations
    }


def test_unavailable_current_source_does_not_use_previous_imports(tmp_path):
    target = tmp_path / 'current.py'
    target.write_text('import decimal\n', encoding='utf-8')
    assert tuple(boundary._imports(target)) == ('decimal',)
    target.unlink()
    with pytest.raises(FileNotFoundError):
        tuple(boundary._imports(target))
