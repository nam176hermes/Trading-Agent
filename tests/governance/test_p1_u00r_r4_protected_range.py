"""R4 must not alter reviewed Task 1 or runtime-policy bytes."""

from __future__ import annotations

from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]
FIX7_R3 = "a33c3a2dbe4432da6eeec672067db6ffe065747e"
ACCEPTED_A = "f15b1985215ef4d018f48c712221920502379a48"
TASK1_PATHS = (
    "scripts/nautilus_pin_inventory/git_source.py",
    "tests/governance/nautilus_pin_inventory/test_git_source.py",
    "docs/implementation/p1-real-nautilus/upgrade/p1-u00r-pragmatic-rebaseline.md",
)
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
    "pyproject.toml", "uv.lock", "Makefile",
    "scripts/build_nautilus_engine.py", "scripts/prepare_nautilus_input_cache.py",
    "scripts/verify_nautilus_provenance.py",
    "services/job_worker/nautilus_closure.py",
)


def _entry(commit: str, path: str) -> str:
    result = subprocess.run(["git", "ls-tree", commit, "--", path], cwd=ROOT, text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_r4_retains_exact_protected_blob_modes() -> None:
    """Break caught: R4 changes reviewed Task 1 or runtime/input authority bytes."""
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True, check=True).stdout.strip()
    for path in TASK1_PATHS:
        assert _entry(head, path) == _entry(FIX7_R3, path)
    for path in ACCEPTED_A_PATHS:
        assert _entry(head, path) == _entry(ACCEPTED_A, path)
    assert _entry(head, "scripts/materialize_nautilus_runtime_closure.py") == _entry(
        FIX7_R3, "scripts/materialize_nautilus_runtime_closure.py"
    )
