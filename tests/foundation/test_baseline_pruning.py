from scripts.prune_python_baselines import retained_diagnostics


def test_pruning_preserves_duplicate_sites_but_never_accepts_new_debt():
    a = {"code": "reportAny", "range": {"startColumn": 1, "endColumn": 3, "lineCount": 1}}
    b = {**a, "code": "reportUnknownMemberType"}
    old = {"files": {"a.py": [a, a, a, b], "removed.py": [a]}}
    actual = {"files": {"a.py": [a, a, b, b], "new.py": [b]}}
    assert retained_diagnostics(old, actual) == {"files": {"a.py": [a, a, b]}}
    assert len(old["files"]["a.py"]) == 4
