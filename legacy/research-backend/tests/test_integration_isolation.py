from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).parent.parent


def test_signal_modules_honor_isolated_data_root(tmp_path: Path) -> None:
    environment = os.environ.copy()
    environment["TRADING_DATA_ROOT"] = str(tmp_path)
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import adanos_signals, predscope_signals; "
                "print(predscope_signals.REPORTS_DIR); "
                "print(predscope_signals.OUTPUT_FILE); "
                "print(adanos_signals.REPORTS_DIR); "
                "print(adanos_signals.OUTPUT_FILE)"
            ),
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    paths = [Path(line) for line in completed.stdout.splitlines()]
    assert paths == [
        tmp_path / "reports",
        tmp_path / "signals" / "predscope_signals.json",
        tmp_path / "reports",
        tmp_path / "signals" / "adanos_signals.json",
    ]
