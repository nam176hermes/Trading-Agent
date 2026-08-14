from __future__ import annotations

from pathlib import Path
import re
from collections import Counter

ROOT = Path(__file__).resolve().parents[1]


def _make_targets() -> dict[str, tuple[tuple[str, ...], str]]:
    source = (ROOT / "Makefile").read_text(encoding="utf-8")
    matches = list(re.finditer(r"^([A-Za-z0-9_-]+):([^\n]*)\n((?:\t.*\n)*)", source, re.MULTILINE))
    return {
        match.group(1): (tuple(match.group(2).split()), match.group(3))
        for match in matches
    }


def _reachable(targets: dict[str, tuple[tuple[str, ...], str]], start: str) -> set[str]:
    seen: set[str] = set()
    pending = [start]
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        prerequisites, recipe = targets[current]
        pending.extend(prerequisites)
        for command in re.findall(r"\$\(MAKE\)\s+([^\\\n]+)", recipe):
            pending.extend(token for token in command.split() if token in targets)
    return seen


def _route_multiplicity(
    targets: dict[str, tuple[tuple[str, ...], str]], start: str,
) -> Counter[str]:
    """Count every target reached by the concrete recursive-Make target graph."""
    counts: Counter[str] = Counter()
    pending = [start]
    while pending:
        current = pending.pop()
        counts[current] += 1
        prerequisites, recipe = targets[current]
        children = list(prerequisites)
        for command in re.findall(r"\$\(MAKE\)\s+([^\\\n]+)", recipe):
            children.extend(token for token in command.split() if token in targets)
        pending.extend(children)
    return counts


def test_ci_routes_only_to_the_portable_gate_and_never_host_authority() -> None:
    """Break caught: the default CI route can execute host authority qualification."""
    targets = _make_targets()

    assert targets["ci"][0] == ("ci-portable",)
    assert "ci-host-authority" not in _reachable(targets, "ci")
    assert "ci-host-authority" not in _reachable(targets, "ci-portable")


def test_portable_and_host_routes_have_distinct_required_semantics() -> None:
    """Break caught: portable duplicates source suites or host accepts deferred authority."""
    targets = _make_targets()
    portable = _reachable(targets, "ci-portable")
    host = _reachable(targets, "ci-host-authority")

    assert {"ci-portable-topology", "check-test-governance-topology", "artifact-firewall-check", "audit-delivery-contract"} <= portable
    assert "ci-common-private" in portable
    assert "ci-common-private" not in host
    host_recipe = "\n".join(targets[target][1] for target in host)
    assert "validate-native" in host_recipe and "--require-pass" in host_recipe
    assert "validate-external" in host_recipe and "--require-pass" in host_recipe
    assert "check-p0-baseline" in host


def test_portable_route_uses_topology_once_without_repeating_its_root_universe() -> None:
    """Break caught: the common source route reruns portable root nodes beside topology."""
    counts = _route_multiplicity(_make_targets(), "ci-portable")

    assert counts["ci-common-private"] == 1
    assert counts["ci-portable-topology"] == 1
    assert counts["test"] == 0
    assert counts["test-portable-embedded-proof"] == 0
    assert counts["test-all-portable-private"] == 0


def test_portable_route_uses_private_raw_evidence_then_one_final_publisher() -> None:
    targets = _make_targets()
    outer = targets["ci-portable"][1]
    firewall = targets["artifact-firewall-check"][1]

    assert "raw_evidence_root=" in outer
    assert 'chmod 0700 "$$raw_evidence_root"' in outer
    assert 'TEST_EVIDENCE_DIR="$$raw_evidence_root" $(MAKE) ci-portable-private' in outer
    assert "scripts.check_artifact_firewall publish" in firewall
    assert '--raw-root "$(TEST_EVIDENCE_DIR)"' in firewall
    assert '--destination "$(CURDIR)/runtime/state/ci-portable"' in firewall
    assert "check-portable-defect-closure" not in firewall


def test_workflows_are_partitioned_into_portable_and_dispatch_only_host_authority() -> None:
    """Break caught: hosted or privileged workflow routing enters the portable default."""
    foundation = (ROOT / ".github/workflows/foundation.yml").read_text(encoding="utf-8")
    host = (ROOT / ".github/workflows/host-authority.yml").read_text(encoding="utf-8")

    # Workflow text is the executed security boundary; these assertions intentionally
    # test its literal privileges, triggers, runner class, and invocation contract.
    assert re.search(r"^permissions:\n  contents: read$", foundation, re.MULTILINE)
    assert re.search(r"^    runs-on: ubuntu-(latest|24\.04)$", foundation, re.MULTILINE)
    assert "run: make ci-portable NONINTERACTIVE=1" in foundation
    assert "if: always()" in foundation
    assert "path: runtime/state/ci-portable/**" in foundation
    assert "retention-days: 14" in foundation
    assert "/tmp/trading-agent-test-evidence" not in foundation
    assert re.search(r"^  push:$", foundation, re.MULTILINE)
    assert re.search(r"^  pull_request:$", foundation, re.MULTILINE)
    assert "pull_request_target" not in foundation
    assert "self-hosted" not in foundation
    assert "environment:" not in foundation
    assert "secrets." not in foundation

    assert re.search(r"^  workflow_dispatch:$", host, re.MULTILINE)
    assert "push:" not in host and "pull_request" not in host
    assert re.search(r"^permissions:\n  contents: read$", host, re.MULTILINE)
    assert "environment: trading-authority" in host
    assert "runs-on: [self-hosted, linux, x64, trading-authority]" in host
    assert "run: make ci-host-authority NONINTERACTIVE=1" in host
