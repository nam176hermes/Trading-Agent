"""Release projection reads only inventory-selected Git blobs after source admission."""
import json
from pathlib import PurePosixPath

import pytest

from packages.pre_p3_provenance import ProvenanceError
from tests.p3.test_job_api import _alpha_request


@pytest.mark.parametrize('fault', [None, 'dirty', 'source', 'symlink', 'missing', 'duplicate', 'inventory-only'])
def test_projection_does_not_reread_unselected_blobs(monkeypatch, fault):
    from services.job_worker import p3_spawn as module

    source = _alpha_request().payload.expected_source
    inventory = 'docs/implementation/p3/p3-driver-files-v1.json'
    selected = [inventory, 'scripts/driver.py']
    if fault == 'inventory-only':
        selected = [inventory]
    elif fault == 'missing':
        selected.append('unavailable.py')
    elif fault == 'duplicate':
        selected.append('scripts/driver.py')
    blobs = {
        inventory: json.dumps(dict(schema_version='p3-driver-source-files-v1', paths=selected)).encode(),
        'scripts/driver.py': b'print("synthetic")\n',
        'unrelated.bin': b'unselected bytes',
    }
    entries = [('100755' if p.endswith('.py') else '100644', str(i)*40, p.encode())
               for i, p in enumerate(sorted(blobs), 1)]
    if fault == 'symlink':
        entries[-1] = ('120000', entries[-1][1], entries[-1][2])
    tree = b''.join(f'{mode} blob {oid}\t'.encode()+p+b'\0' for mode, oid, p in entries)
    calls = []
    current = source.model_dump(mode='json')
    if fault == 'source':
        current['commit_sha'] = '9'*40
    monkeypatch.setattr(module, 'canonical_source_identity', lambda root: current)
    monkeypatch.setattr(module, '_git', lambda root, *args:
        (b' M changed.py' if fault == 'dirty' else b'') if args == ('status', '--porcelain') else tree)

    def read(root, chosen):
        calls.extend(p.decode() for _, _, p in chosen)
        return [blobs[p.decode()] for _, _, p in chosen]

    monkeypatch.setattr(module, '_read_blobs', read)
    if fault not in (None, 'inventory-only'):
        with pytest.raises((ValueError, KeyError, ProvenanceError)):
            module._release_files(source)
        return
    result = module._release_files(source)
    assert result == {PurePosixPath('/p3/release')/p: (mode, blobs[p])
                      for mode, _, raw_path in entries if (p := raw_path.decode()) in selected}
    assert sorted(calls) == selected
