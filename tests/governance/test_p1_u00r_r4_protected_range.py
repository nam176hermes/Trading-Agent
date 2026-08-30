"""R4 must not alter reviewed Task 1 or runtime-policy bytes."""

from __future__ import annotations

from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]
FIX7_R3 = "a33c3a2dbe4432da6eeec672067db6ffe065747e"
ACCEPTED_A = "f15b1985215ef4d018f48c712221920502379a48"
OBSERVABILITY_DEPENDENCY_BASELINE = "bb176622567d10543454caddae271693a4216aa2"
REVIEWED_S = "8dbaa153276a0d44c2e3b0a6b0c3de4055133630"
P1_16_ACCEPTED = "01611dd69d763511078d2deb4013a3def6f80581"
P1_24_ACCEPTED = "fe2b5c6d7a19ebbd21ce983c0d3f47933a889cc0"
HISTORICAL_EXTRACTOR_BLOB = "c6fe75618e522ba924c1aa0088ff44e5e1a6bd4c"
CURRENT_EXTRACTOR_BLOB = "83d750d475af788b664e3ca4c2e266f75df58eeb"
TASK1_REPAIR_PATHS = (
    "scripts/nautilus_pin_inventory/git_source.py",
    "tests/governance/nautilus_pin_inventory/test_git_source.py",
)
TASK1_ACCEPTED_PATHS = ("tests/governance/nautilus_pin_inventory/test_source_io.py",)
OVERLAY_PATH = "docs/implementation/p1-real-nautilus/upgrade/p1-u00r-pragmatic-rebaseline.md"
ACCEPTED_A_PATHS = (
    "engines/nautilus/README.md",
    "engines/nautilus/engine-build-policy.json",
    "engines/nautilus/input-cache-policy.json",
    "engines/nautilus/llvm-toolchain-policy.json",
    "engines/nautilus/paper-compatibility-runtime-closure-policy.json",
    "engines/nautilus/runtime-closure-policy.json",
    "engines/nautilus/sealed-uv-exec-policy.json",
    "engines/nautilus/wheel-cache-policy.json",
    "engines/nautilus/toolchain-inputs.json",
    "scripts/prepare_nautilus_input_cache.py",
    "scripts/verify_nautilus_provenance.py",
    "services/job_worker/nautilus_closure.py",
)


def _entry(commit: str, path: str) -> str:
    result = subprocess.run(["git", "ls-tree", commit, "--", path], cwd=ROOT, text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _working_blob(path: str) -> str:
    result = subprocess.run(
        ["git", "hash-object", "--", path],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


def test_r4_retains_exact_protected_blob_modes() -> None:
    """Break caught: R4 changes reviewed Task 1 or runtime/input authority bytes."""
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True, check=True).stdout.strip()
    for path in TASK1_REPAIR_PATHS:
        assert _entry(head, path) == _entry(FIX7_R3, path)
    for path in TASK1_ACCEPTED_PATHS:
        assert _entry(head, path) == _entry(ACCEPTED_A, path)
    assert _entry(head, OVERLAY_PATH) == _entry(REVIEWED_S, OVERLAY_PATH)
    for path in ACCEPTED_A_PATHS:
        assert _entry(head, path) == _entry(ACCEPTED_A, path)
    assert _entry(head, "Makefile") == _entry(P1_24_ACCEPTED, "Makefile")
    assert _entry(head, "pyproject.toml") == _entry(P1_16_ACCEPTED, "pyproject.toml")
    assert _entry(head, "uv.lock") == _entry(OBSERVABILITY_DEPENDENCY_BASELINE, "uv.lock")
    assert _entry(ACCEPTED_A, "scripts/build_nautilus_engine.py") == (
        "100644 blob 193c20272ef8eff4ccc9660069b9f523c4105f54\t"
        "scripts/build_nautilus_engine.py"
    )
    assert _entry(FIX7_R3, "scripts/materialize_nautilus_runtime_closure.py") == (
        "100644 blob 62dc37dcf76c520c8ae24cf47526ff93842267a3\t"
        "scripts/materialize_nautilus_runtime_closure.py"
    )
    assert _entry(FIX7_R3, "scripts/nautilus_pin_inventory/python_extractor.py") == (
        f"100644 blob {HISTORICAL_EXTRACTOR_BLOB}\t"
        "scripts/nautilus_pin_inventory/python_extractor.py"
    )
    assert _working_blob("scripts/nautilus_pin_inventory/python_extractor.py") == (
        CURRENT_EXTRACTOR_BLOB
    )
