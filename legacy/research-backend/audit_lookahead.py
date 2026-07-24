"""
audit_lookahead.py — Look-ahead bias detector (TradeSmart methodology).

Scans feature engineering code for .shift(-N) calls that would leak
future prices into training features, producing artificially high
backtest Sharpe ratios.

Allowed exceptions: target columns (target_*, chikou_span) intentionally
look ahead by design.

Usage:
    python audit_lookahead.py
"""

import re
from pathlib import Path

TARGET_FILES = [
    "ml_predictor.py",
    "ta_engine.py",
    "ta_shim.py",
    "assembly.py",
    "backtest_engine.py",
    "backtest_runner.py",
    "dl_predictor.py",
    "ensemble_ml.py",
]

# Column names that are intentionally forward-looking (targets / Ichimoku chikou)
ALLOWED_PREFIXES = ("target", "chikou", "label", "future")


def _is_allowed(line: str) -> bool:
    stripped = line.strip().lower()
    return (
        any(stripped.startswith(p) or f'["{p}' in stripped or f"['{p}" in stripped
            for p in ALLOWED_PREFIXES)
        or "# lookahead-ok" in stripped
    )


def check_file(path: Path) -> list[dict]:
    findings = []
    try:
        source = path.read_text()
    except Exception:
        return findings

    pattern = re.compile(r'\.shift\(\s*-\s*(\d+)\s*\)')
    for i, line in enumerate(source.splitlines(), 1):
        m = pattern.search(line)
        if not m:
            continue
        risk = "ALLOWED" if _is_allowed(line) else "LOOK-AHEAD RISK"
        findings.append({
            "file":    path.name,
            "line":    i,
            "code":    line.strip()[:120],
            "risk":    risk,
            "shift_n": int(m.group(1)),
        })
    return findings


def run_audit() -> int:
    """Run audit, print results, return count of risky findings."""
    base = Path(__file__).parent
    all_findings = []

    print(f"{'File':<30} {'Status'}")
    print("-" * 55)

    for fname in TARGET_FILES:
        p = base / fname
        if not p.exists():
            print(f"  {'SKIP':<6}  {fname} (not found)")
            continue
        findings = check_file(p)
        risks = [f for f in findings if f["risk"] != "ALLOWED"]
        status = f"CLEAN" if not risks else f"{len(risks)} RISK(S)"
        print(f"  {status:<8} {fname}")
        all_findings.extend(findings)

    risks = [f for f in all_findings if f["risk"] != "ALLOWED"]

    if risks:
        print(f"\n{'='*55}")
        print(f"Look-ahead risks found: {len(risks)}")
        print(f"{'='*55}")
        for f in risks:
            print(f"\n  {f['file']}:{f['line']}  [shift(-{f['shift_n']})]")
            print(f"    {f['code']}")
        print(f"\n  Fix: ensure feature uses only past data, or add "
              f"# lookahead-ok if intentional.")
    else:
        print(f"\n  All {len(TARGET_FILES)} files clean — no look-ahead bias detected.")

    allowed = [f for f in all_findings if f["risk"] == "ALLOWED"]
    if allowed:
        print(f"\n  Allowed intentional look-aheads (targets/chikou): {len(allowed)}")
        for f in allowed:
            print(f"    {f['file']}:{f['line']}  {f['code'][:80]}")

    return len(risks)


if __name__ == "__main__":
    print("NovaTrade Look-Ahead Bias Audit")
    print("=" * 55)
    n_risks = run_audit()
    raise SystemExit(0 if n_risks == 0 else 1)
