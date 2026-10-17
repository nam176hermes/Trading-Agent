"""Check fixed native prerequisites before hosted CI spends time on the full suite."""

from __future__ import annotations

import json
import subprocess

from scripts import t_g03_capability_topology as topology


def main() -> int:
    """Diagnose fixed native probes before the expensive gate; issue no receipt."""
    def diagnostic_runner(command, **kwargs):
        try:
            result = subprocess.run(command, **kwargs)
        except (OSError, subprocess.SubprocessError) as exc:
            print(json.dumps({"native_probe_error": type(exc).__name__, "detail": str(exc)[:4096]}), flush=True)
            raise
        print(json.dumps({
            "native_probe": command[0], "exit_code": result.returncode,
            "stdout": result.stdout[:4096].decode("utf-8", errors="replace"),
            "stderr": result.stderr[:4096].decode("utf-8", errors="replace"),
        }), flush=True)
        return result

    broken = False
    for code in ("NATIVE-BWRAP-OS-SANDBOX", "NATIVE-USERNS-ROOT-PROVISION"):
        with topology._retained_native_probe(code, runner=diagnostic_runner) as session:
            topology._postcheck_native_probe(session)
            print(json.dumps({"code": code, "state": session.state, "fact": session.fact, "probe": session.probe}), flush=True)
            broken |= session.state not in {"AVAILABLE", "UNAVAILABLE"}
    return 2 if broken else 0


if __name__ == "__main__":
    raise SystemExit(main())
