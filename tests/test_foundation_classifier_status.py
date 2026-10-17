"""The P1-H classifier step must see the classifier's own exit status (Codex audit A08)."""
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _classifier_branch() -> str:
    text = (ROOT / '.github/workflows/foundation.yml').read_text()
    run = next(line for line in text.splitlines() if 'classify_p1_h_impact.py' in line)
    start = run.index('if PYTHONPATH=')
    end = run.index('test "$status" -eq 2;') + len('test "$status" -eq 2;')
    branch = run[start:end]
    # Replace the classifier invocation with a stub exit code; keep the workflow's own branching verbatim.
    return re.sub(r'if PYTHONPATH=.*?> "\$decision_path"; then', 'if (exit "$STUB") > "$decision_path"; then', branch)


@pytest.mark.parametrize(('stub', 'expected'), [(0, 0), (2, 0), (3, 1)])
def test_classifier_exit_status_reaches_the_class_d_check(tmp_path, stub, expected):
    script = 'set -euo pipefail; decision_path="$1"; ' + _classifier_branch() + ' echo REACHED_CLASS_D_CHECK'
    result = subprocess.run(['bash', '-c', script, 'bash', str(tmp_path / 'decision.json')],
                            env={'STUB': str(stub), 'PATH': '/usr/bin:/bin'}, capture_output=True, text=True, check=False)
    assert result.returncode == expected
    # Exit 2 must continue to the Class D decision/label checks; exit 0 leaves early; anything else stops.
    assert ('REACHED_CLASS_D_CHECK' in result.stdout) == (stub == 2)
