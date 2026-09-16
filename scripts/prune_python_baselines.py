"""Remove obsolete baseline entries without accepting any new diagnostics."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess
import sys
from tempfile import NamedTemporaryFile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.dev import BASEDPYRIGHT_VERSION
from scripts.trusted_test_tmp import prepare_trusted_test_tmp


Baseline = dict[str, dict[str, list[dict[str, object]]]]


def retained_diagnostics(previous: Baseline, current: Baseline) -> Baseline:
    """Intersect multisets: identical entries can represent distinct source sites."""
    retained = {}
    for path, entries in previous["files"].items():
        remaining = Counter(json.dumps(entry, sort_keys=True) for entry in current["files"].get(path, []))
        keep = []
        for entry in entries:
            key = json.dumps(entry, sort_keys=True)
            if remaining[key]:
                keep.append(entry)
                remaining[key] -= 1
        if keep:
            retained[path] = keep
    return {"files": retained}


def prune(component: str) -> None:
    legacy = component == "legacy"
    baseline = ROOT / ".basedpyright" / ("legacy-baseline.json" if legacy else "baseline.json")
    project = ROOT / ("pyrightconfig.legacy.json" if legacy else "pyrightconfig.json")
    python = ROOT / ("legacy/research-backend/.venv/bin/python" if legacy else ".venv/bin/python")
    original = baseline.read_bytes()
    previous = json.loads(original)
    session = prepare_trusted_test_tmp("baseline-prune")
    try:
        candidate = session.path / "baseline.json"
        candidate.write_bytes(original)
        # Basedpyright keys diagnostics relative to the configuration's directory.
        with NamedTemporaryFile(mode="w", prefix=".baseline-prune-", suffix=".json", dir=ROOT) as config:
            json.dump({"extends": str(project), "baselineFile": str(candidate)}, config)
            config.flush()
            subprocess.run(
                ["uvx", "--from", BASEDPYRIGHT_VERSION, "basedpyright", "--project", config.name,
                 "--pythonpath", str(python), "--writebaseline", "--level", "error"],
                cwd=ROOT, check=True,
            )
        current = json.loads(candidate.read_bytes())
        if previous["files"] and not (previous["files"].keys() & current["files"].keys()):
            raise RuntimeError("no matching baseline paths; review project scope before pruning")
        retained = retained_diagnostics(previous, current)
        if baseline.read_bytes() != original:
            raise RuntimeError("baseline changed during pruning; refusing to overwrite")
        before = sum(map(len, previous["files"].values()))
        after = sum(map(len, retained["files"].values()))
        if retained != previous:
            baseline.write_text(json.dumps(retained, separators=(",", ":")) + "\n")
        print(f"{component}: {before} -> {after}; {before - after} obsolete entries removed; no additions")
    finally:
        session.cleanup()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("component", choices=("core", "legacy"))
    prune(parser.parse_args().component)


if __name__ == "__main__":
    main()
