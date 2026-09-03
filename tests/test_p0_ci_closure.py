from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from scripts import check_p0_ci_closure as closure


ROOT = Path(__file__).resolve().parents[1]
MATRIX_RELATIVE = "docs/implementation/p0-ci-closure-matrix.json"
HEAD = "9" * 40
TREE = "a" * 64


def _canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _git(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *arguments], cwd=root, check=True, text=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


def _write(root: Path, relative: str, raw: bytes) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


@pytest.fixture(scope="module")
def source_context() -> closure._ValidationContext:
    return closure._production_context()


@pytest.fixture
def context(tmp_path: Path, source_context: closure._ValidationContext) -> closure._ValidationContext:
    """Small real Git repository with injectable immutable authority facts."""
    matrix = json.loads((ROOT / MATRIX_RELATIVE).read_text(encoding="utf-8"))
    bound = {
        MATRIX_RELATIVE,
        "Makefile",
        ".github/workflows/foundation.yml",
        ".github/workflows/host-authority.yml",
        "ops/consolidation/p0-canonical-baseline.json",
        "tests/fixtures/t-g03a-hosted-failure-inventory.tsv",
        "docs/implementation/foundation-portable-defect-closure.tsv",
    }
    for entry in matrix["requirements"]:
        bound.update(entry["implementation_paths"])
        bound.update(entry["evidence_paths"])
        bound.add(entry["workflow"])
        bound.update(node.split("::", 1)[0] for node in entry["test_node_ids"])
    for relative in sorted(bound):
        _write(tmp_path, relative, (ROOT / relative).read_bytes())
    _write(tmp_path, "tracked-donor.txt", b"donor\n")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "--", ".")
    _git(
        tmp_path, "-c", "user.name=P0 test", "-c",
        "user.email=p0@example.invalid", "commit", "-qm", "fixture",
    )
    return replace(
        source_context,
        root=tmp_path,
        head_sha=HEAD,
        source_tree_sha256=TREE,
        collected_node_ids=frozenset(
            node for entry in matrix["requirements"] for node in entry["test_node_ids"]
        ),
        receipt_relatives=(),
        review_relative=None,
    )


def _document(context: closure._ValidationContext) -> dict[str, object]:
    return json.loads((context.root / MATRIX_RELATIVE).read_text(encoding="utf-8"))


def _set_document(context: closure._ValidationContext, document: dict[str, object], *, canonical: bool = True) -> None:
    raw = _canonical(document) if canonical else b" " + _canonical(document)
    (context.root / MATRIX_RELATIVE).write_bytes(raw)


def _validate(context: closure._ValidationContext) -> str:
    return closure._validate(context, require_complete=False)


def _error(context: closure._ValidationContext, code: str, *, complete: bool = False) -> None:
    with pytest.raises(closure.ClosureError, match=f"^{code}$"):
        closure._validate(context, require_complete=complete)


def test_cli_accepts_only_canonical_repository_matrix() -> None:
    """Break caught: public CLI becomes an arbitrary-root validation bypass."""
    result = subprocess.run(
        [sys.executable, "-m", "scripts.check_p0_ci_closure", "--matrix", MATRIX_RELATIVE],
        cwd=ROOT, text=True, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "QUALIFICATION_PENDING"
    bypass = subprocess.run(
        [sys.executable, "-m", "scripts.check_p0_ci_closure", "--matrix", "/tmp/matrix.json"],
        cwd=ROOT, text=True, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert bypass.returncode == 2
    assert bypass.stderr.strip() == "P0_CLOSURE_MATRIX_PATH_INVALID"


def test_pending_source_matrix_is_an_executable_closed_contract(context: closure._ValidationContext) -> None:
    """Break caught: the checked-in source matrix fabricates qualification."""
    assert _validate(context) == "QUALIFICATION_PENDING"
    completion_contract = (ROOT / "docs/implementation/p0-ci-closure.md").read_text(
        encoding="utf-8",
    )
    assert "317 active authority nodes: 58 native and 259 external" in completion_contract
    assert "49 portable-defect closure nodes" in completion_contract
    assert "366 governed nodes and 6,131 portable remainder nodes" in completion_contract
    assert "24 native plus 6 external nodes" not in completion_contract


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (lambda value: value.__setitem__("unknown", True), "P0_CLOSURE_SCHEMA_INVALID"),
        (lambda value: value.pop("state"), "P0_CLOSURE_SCHEMA_INVALID"),
        (lambda value: value["requirements"].append(value["requirements"][0]), "P0_CLOSURE_REQUIREMENT_SET_DRIFT"),
        (lambda value: value["requirements"][1].__setitem__("requirement_id", "P0-I01"), "P0_CLOSURE_REQUIREMENT_SET_DRIFT"),
        (lambda value: value["requirements"][0].__setitem__("unknown", True), "P0_CLOSURE_ENTRY_SCHEMA_INVALID"),
        (lambda value: value["requirements"][0]["implementation_paths"].append(value["requirements"][0]["implementation_paths"][0]), "P0_CLOSURE_IMPLEMENTATION_PATHS_NONCANONICAL"),
        (lambda value: value["requirements"][0].__setitem__("required_status", "PENDING"), "P0_CLOSURE_REQUIRED_STATUS_INVALID"),
        (lambda value: value["requirements"][0].__setitem__("required_status", "DEFERRED"), "P0_CLOSURE_REQUIRED_STATUS_INVALID"),
        (lambda value: value["requirements"][0].__setitem__("required_status", "UNAVAILABLE"), "P0_CLOSURE_REQUIRED_STATUS_INVALID"),
        (lambda value: value["requirements"][16].__setitem__("required_status", "PASS"), "P0_CLOSURE_REQUIRED_STATUS_INVALID"),
    ],
    ids=["unknown-top", "missing-top", "extra-entry", "duplicate-id", "unknown-entry", "duplicate-array", "source-pending", "deferred-pass", "unavailable-pass", "future-pass"],
)
def test_matrix_schema_and_exact_status_attacks_fail_closed(context: closure._ValidationContext, mutation, code: str) -> None:
    """Break caught: schema or state semantics can drift without a rejection."""
    document = _document(context)
    mutation(document)
    _set_document(context, document)
    _error(context, code)


def test_noncanonical_matrix_bytes_fail_closed(context: closure._ValidationContext) -> None:
    """Break caught: byte-equivalent but noncanonical source gains authority."""
    _set_document(context, _document(context), canonical=False)
    _error(context, "P0_CLOSURE_JSON_NONCANONICAL")


def test_portable_ci_cannot_bypass_hwc_status(context: closure._ValidationContext) -> None:
    """Break caught: portable qualification omits the canonical HWC projection."""
    makefile = context.root / "Makefile"
    raw = makefile.read_bytes()
    makefile.write_bytes(
        b"\n".join(
            line.replace(b" check-hwc-status", b"")
            if line.startswith(b"check-contracts:")
            else line
            for line in raw.split(b"\n")
        )
    )

    _error(context, "P0_CLOSURE_HWC_STATUS_UNREACHABLE")


def test_end_state_ids_cannot_exchange_truthful_but_wrong_proofs(context: closure._ValidationContext) -> None:
    """Break caught: E01 ancestry silently points at E02 date-authority proof."""
    document = _document(context)
    document["requirements"][6]["implementation_paths"] = document["requirements"][7]["implementation_paths"]
    document["requirements"][6]["test_node_ids"] = document["requirements"][7]["test_node_ids"]
    document["requirements"][6]["make_target"] = document["requirements"][7]["make_target"]
    document["requirements"][6]["evidence_paths"] = document["requirements"][7]["evidence_paths"]
    _set_document(context, document)
    _error(context, "P0_CLOSURE_END_STATE_BINDING_INVALID")


@pytest.mark.parametrize(
    ("relative", "raw", "code"),
    [
        ("Makefile", (ROOT / "Makefile").read_bytes().replace(b"$(MAKE) ci-portable-private", b"@true", 1), "P0_CLOSURE_MAKE_TARGET_UNREACHABLE"),
        ("Makefile", (ROOT / "Makefile").read_bytes().replace(b"$(MAKE) ci-portable-private", b"# $(MAKE) ci-portable-private", 1), "P0_CLOSURE_MAKE_TARGET_UNREACHABLE"),
        ("Makefile", (ROOT / "Makefile").read_bytes().replace(b"$(MAKE) ci-portable-private", b"if false; then $(MAKE) ci-portable-private; fi", 1), "P0_CLOSURE_MAKE_TARGET_UNREACHABLE"),
        ("Makefile", (ROOT / "Makefile").read_bytes().replace(b"$(MAKE) ci-portable-private", b"run_private() { $(MAKE) ci-portable-private; }; run_private", 1), "P0_CLOSURE_MAKE_TARGET_UNREACHABLE"),
        ("Makefile", (ROOT / "Makefile").read_bytes().replace(b'TEST_EVIDENCE_DIR="$$raw_evidence_root" $(MAKE) ci-portable-private', b'exit 0; TEST_EVIDENCE_DIR="$$raw_evidence_root" $(MAKE) ci-portable-private', 1), "P0_CLOSURE_MAKE_TARGET_UNREACHABLE"),
        ("Makefile", (ROOT / "Makefile").read_bytes().replace(b'TEST_EVIDENCE_DIR="$$raw_evidence_root" $(MAKE) ci-portable-private', b'trap "exit 0" DEBUG; TEST_EVIDENCE_DIR="$$raw_evidence_root" $(MAKE) ci-portable-private', 1), "P0_CLOSURE_MAKE_TARGET_UNREACHABLE"),
        (".github/workflows/foundation.yml", b"name: Foundation\n# run: make ci-portable NONINTERACTIVE=1\non:\n  push:\n  pull_request:\n  workflow_dispatch:\npermissions:\n  contents: read\njobs:\n  verify:\n    runs-on: ubuntu-24.04\n    env:\n      CI: \"true\"\n      LIVE_EXECUTION_ENABLED: \"false\"\n      LIVE_TRADING_APPROVED: \"false\"\n    steps:\n      - run: true\n# include-hidden-files: true\n", "P0_CLOSURE_FOUNDATION_WORKFLOW_INVALID"),
        (".github/workflows/foundation.yml", (ROOT / ".github/workflows/foundation.yml").read_bytes() + b"spoof:\n  run: make ci-portable NONINTERACTIVE=1\n", "P0_CLOSURE_FOUNDATION_WORKFLOW_INVALID"),
    ],
    ids=["recursive-disconnect", "comment-make-spoof", "conditional-make-spoof", "function-make-spoof", "exit-prefix-make-spoof", "debug-trap-make-spoof", "workflow-comment-spoof", "workflow-token-outside-jobs"],
)
def test_route_spoofs_fail_closed(context: closure._ValidationContext, relative: str, raw: bytes, code: str) -> None:
    """Break caught: comments or disconnected Make nodes masquerade as execution."""
    (context.root / relative).write_bytes(raw)
    _error(context, code)


_CANONICAL_PRIVATE_ROUTE = (
    "ci-common-private ci-portable-topology check-portable-defect-closure "
    "check-p0-baseline check-p0-maintainability check-test-governance-topology check-p0-ci-closure "
    "artifact-firewall-check audit-delivery-contract"
)


@pytest.mark.parametrize(
    "invocation",
    [
        f"$(MAKE) -n {_CANONICAL_PRIVATE_ROUTE}",
        f"$(MAKE) -t {_CANONICAL_PRIVATE_ROUTE}",
        f"$(MAKE) -q {_CANONICAL_PRIVATE_ROUTE}",
        f"$(MAKE) -f attacker.mk {_CANONICAL_PRIVATE_ROUTE}",
        f"$(MAKE) -C alternate {_CANONICAL_PRIVATE_ROUTE}",
        f"$(MAKE) -sn {_CANONICAL_PRIVATE_ROUTE}",
        f"$(MAKE) --dry-run {_CANONICAL_PRIVATE_ROUTE}",
        f"$(MAKE) --touch {_CANONICAL_PRIVATE_ROUTE}",
        f"$(MAKE) --question {_CANONICAL_PRIVATE_ROUTE}",
        f"$(MAKE) --file attacker.mk {_CANONICAL_PRIVATE_ROUTE}",
        f"$(MAKE) --makefile attacker.mk {_CANONICAL_PRIVATE_ROUTE}",
        f"$(MAKE) --directory alternate {_CANONICAL_PRIVATE_ROUTE}",
        f"$(MAKE) --eval harmless=value {_CANONICAL_PRIVATE_ROUTE}",
        f"$(MAKE) -- {_CANONICAL_PRIVATE_ROUTE}",
        f"$(MAKE) MAKEFLAGS=-n {_CANONICAL_PRIVATE_ROUTE}",
        f"$(MAKE) GNUMAKEFLAGS=-n {_CANONICAL_PRIVATE_ROUTE}",
        f"$(MAKE) MFLAGS=-n {_CANONICAL_PRIVATE_ROUTE}",
        f"$(MAKE) MAKEOVERRIDES= {_CANONICAL_PRIVATE_ROUTE}",
        f"$(MAKE) SHELL=true {_CANONICAL_PRIVATE_ROUTE}",
        f"$(MAKE) .SHELLFLAGS=-c {_CANONICAL_PRIVATE_ROUTE}",
        f"$(MAKE) PATH=/nonexistent {_CANONICAL_PRIVATE_ROUTE}",
        f"$(MAKE) MAKE=true {_CANONICAL_PRIVATE_ROUTE}",
        f"$(MAKE) {_CANONICAL_PRIVATE_ROUTE} -n",
        f"$(MAKE) {_CANONICAL_PRIVATE_ROUTE} MAKEFLAGS=-n",
    ],
    ids=[
        "dry-run-short", "touch-short", "question-short", "file-short",
        "directory-short", "combined-short", "dry-run-long", "touch-long",
        "question-long", "file-long", "makefile-long", "directory-long",
        "eval-long", "option-terminator", "makeflags", "gnumakeflags",
        "mflags", "makeoverrides", "shell", "shellflags", "path", "make",
        "option-after-target", "assignment-after-target",
    ],
)
def test_recursive_make_execution_arguments_create_no_authority_edge(
    context: closure._ValidationContext, invocation: str,
) -> None:
    """Break caught: an execution override is mistaken for route authority."""
    makefile = context.root / "Makefile"
    raw = makefile.read_bytes()
    original = f"\t$(MAKE) {_CANONICAL_PRIVATE_ROUTE}\n".encode()
    assert raw.count(original) == 1
    makefile.write_bytes(raw.replace(original, f"\t{invocation}\n".encode(), 1))
    _error(context, "P0_CLOSURE_MAKE_TARGET_UNREACHABLE")


def _run_make_route(root: Path, raw: bytes) -> subprocess.CompletedProcess[str]:
    (root / "Makefile").write_bytes(raw)
    environment = {**os.environ, "MAKEFLAGS": "", "GNUMAKEFLAGS": ""}
    return subprocess.run(
        ["make", "--no-print-directory", "route"], cwd=root, env=environment,
        text=True, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10,
    )


def _recursive_make_route(invocation: str) -> bytes:
    return (
        ".PHONY: route private governed\n"
        "route:\n"
        f"\t{invocation}\n"
        "private:\n"
        "\t$(MAKE) governed\n"
        "governed:\n"
        "\t@touch sentinel\n"
    ).encode()


def test_accepted_make_route_executes_real_private_sentinel(tmp_path: Path) -> None:
    """Break caught: the accepted graph edge is not executable by real Make."""
    raw = b".PHONY: route private\nroute:\n\t$(MAKE) private\nprivate:\n\t@touch sentinel\n"
    assert closure._reachable(closure._make_graph(raw), "route", "private")
    result = _run_make_route(tmp_path, raw)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "sentinel").is_file()


def test_accepted_multi_target_make_route_executes_both_real_sentinels(
    tmp_path: Path,
) -> None:
    """Break caught: closed argument parsing drops a canonical plain target."""
    raw = (
        b".PHONY: route alpha beta\nroute:\n\t$(MAKE) alpha beta\n"
        b"alpha:\n\t@touch alpha-sentinel\nbeta:\n\t@touch beta-sentinel\n"
    )
    assert closure._recursive_make_targets("$(MAKE) alpha beta") == {"alpha", "beta"}
    result = _run_make_route(tmp_path, raw)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "alpha-sentinel").is_file()
    assert (tmp_path / "beta-sentinel").is_file()


@pytest.mark.parametrize(
    "invocation",
    [
        "-$( MAKE ) private",
        "-$(\tMAKE) private",
        "-$(MAKE\t) private",
        "-$(\N{NO-BREAK SPACE}MAKE) private",
        "-$(MAKE)\N{NO-BREAK SPACE}private",
        "-$(MAKE)\N{EM SPACE}private",
        "-\N{NO-BREAK SPACE}$(MAKE) private",
        "-$(MAKE) private\N{NO-BREAK SPACE}",
        "-$(MAKE) private\N{NARROW NO-BREAK SPACE}governed",
        "-$(MAKE) private\N{NO-BREAK SPACE}\\\n\t",
    ],
    ids=[
        "internal-ascii-spaces", "internal-tab-before", "internal-tab-after",
        "internal-nbsp", "nbsp-separator", "em-space-separator",
        "leading-nbsp", "trailing-nbsp", "between-targets-narrow-nbsp",
        "nbsp-before-continuation",
    ],
)
def test_noncanonical_make_whitespace_is_not_graph_authority_when_real_make_skips_sentinel(
    tmp_path: Path, invocation: str,
) -> None:
    """Break caught: Python whitespace parsing invents a non-executed edge."""
    raw = _recursive_make_route(invocation)
    result = _run_make_route(tmp_path, raw)
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "sentinel").exists()
    assert not closure._reachable(closure._make_graph(raw), "route", "governed")


@pytest.mark.parametrize(
    "invocation",
    [
        "$(MAKE)\tprivate",
        "$(MAKE)   private",
        "   $(MAKE) private   ",
        "-$(MAKE) private",
        "@-$(MAKE)\tprivate",
    ],
    ids=["tab-separator", "space-separator", "ascii-trim", "ignore-prefix", "silent-ignore-prefix"],
)
def test_canonical_make_ascii_whitespace_executes_real_sentinel_and_remains_authority(
    tmp_path: Path, invocation: str,
) -> None:
    """Break caught: closing broad whitespace also rejects executable routes."""
    raw = _recursive_make_route(invocation)
    result = _run_make_route(tmp_path, raw)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "sentinel").is_file()
    assert closure._reachable(closure._make_graph(raw), "route", "governed")


def test_canonical_ci_portable_wrapper_remains_the_exact_approved_edge() -> None:
    """Break caught: ASCII grammar hardening drops the custody wrapper edge."""
    assert closure._recursive_make_targets(closure._CI_PORTABLE_WRAPPER_RECIPE) == {
        "ci-portable-private",
    }


@pytest.mark.parametrize(
    ("invocation", "returncode"),
    [
        ("$(MAKE) -n private", 0),
        ("$(MAKE) -t private", 0),
        ("$(MAKE) -f attacker.mk private", 0),
        ("$(MAKE) MAKEFLAGS=-n private", 0),
        ("$(MAKE) SHELL=true private", 0),
        ("$(MAKE) MAKE=true private", 0),
        ("$(MAKE) --dry-run private", 0),
        ("$(MAKE) --touch private", 0),
        ("$(MAKE) -sn private", 0),
        ("$(MAKE) private --dry-run", 0),
        ("$(MAKE) private MAKEFLAGS=-n", 0),
    ],
    ids=[
        "dry-run-short", "touch-short", "alternate-makefile", "makeflags",
        "shell", "make", "dry-run-long", "touch-long", "combined-short",
        "option-after-target", "assignment-after-target",
    ],
)
def test_recursive_make_execution_override_skips_real_sentinel_and_is_not_reachable(
    tmp_path: Path, invocation: str, returncode: int,
) -> None:
    """Break caught: textual reachability disagrees with real GNU Make."""
    raw = _recursive_make_route(invocation)
    (tmp_path / "attacker.mk").write_text(
        ".PHONY: private\nprivate:\n\t@:\n", encoding="utf-8",
    )
    result = _run_make_route(tmp_path, raw)
    assert result.returncode == returncode, result.stderr
    assert not (tmp_path / "sentinel").exists()
    assert not closure._reachable(closure._make_graph(raw), "route", "governed")


@pytest.mark.parametrize(
    "raw",
    [
        b".ONESHELL:\n.PHONY: route private\nroute:\n\texit 0\n\t$(MAKE) private\nprivate:\n\t@touch sentinel\n",
        b"SHELL := /bin/true\n.PHONY: route private\nroute:\n\t$(MAKE) private\nprivate:\n\t@touch sentinel\n",
    ],
    ids=["oneshell-exit", "shell-noop"],
)
def test_make_execution_override_without_sentinel_is_rejected_before_graph(
    tmp_path: Path, raw: bytes,
) -> None:
    """Break caught: textual reachability survives while real Make skips private."""
    result = _run_make_route(tmp_path, raw)
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "sentinel").exists()
    with pytest.raises(closure.ClosureError, match="^P0_CLOSURE_MAKEFILE_INVALID$"):
        closure._make_graph(raw)


@pytest.mark.parametrize(
    "prefix",
    [
        b".ONESHELL:\n",
        b".POSIX:\n",
        b".SECONDEXPANSION:\n",
        b".NOTPARALLEL:\n",
        b"SHELL := /bin/true\n",
        b".SHELLFLAGS := -c\n",
        b".RECIPEPREFIX := >\n",
        b"MAKE := /bin/true\n",
        b"MAKEFLAGS := --silent\n",
        b"PATH := /nonexistent\n",
        b"override SHELL := /bin/true\n",
        b"export SHELL := /bin/true\n",
        b"unexport SHELL\n",
        b"ci-portable: SHELL := /bin/true\n",
        b"include attacker.mk\n",
        b"-include attacker.mk\n",
        b"sinclude attacker.mk\n",
        b"define SHELL\n/bin/true\nendef\n",
        b"$(eval SHELL := /bin/true)\n",
        b"ifeq (1,1)\nendif\n",
    ],
    ids=[
        "oneshell", "posix", "secondary-expansion", "notparallel", "shell",
        "shellflags", "recipeprefix", "make", "makeflags", "path",
        "override", "export", "unexport", "target-specific", "include",
        "optional-include", "sinclude", "define", "eval", "conditional",
    ],
)
def test_make_execution_semantic_mutations_fail_closed(
    context: closure._ValidationContext, prefix: bytes,
) -> None:
    """Break caught: Make directives alter execution behind a valid text graph."""
    makefile = context.root / "Makefile"
    makefile.write_bytes(prefix + makefile.read_bytes())
    _error(context, "P0_CLOSURE_MAKEFILE_INVALID")


def test_foundation_timeout_covers_portable_gate_and_hwc_evidence() -> None:
    """The protected-main HWC proof must fit after the full portable gate."""
    workflow = (ROOT / ".github/workflows/foundation.yml").read_text(encoding="utf-8")
    assert "    timeout-minutes: 60\n" in workflow


@pytest.mark.parametrize(
    ("workflow", "old", "new", "code"),
    [
        ("foundation.yml", b'LIVE_TRADING_APPROVED: "false"', b'LIVE_TRADING_APPROVED: "true"', "P0_CLOSURE_FOUNDATION_WORKFLOW_INVALID"),
        ("foundation.yml", b"runs-on: ubuntu-24.04", b"runs-on: self-hosted", "P0_CLOSURE_FOUNDATION_WORKFLOW_INVALID"),
        ("foundation.yml", b'name: verify-${{ github.event_name }}', b'name: verify', "P0_CLOSURE_FOUNDATION_WORKFLOW_INVALID"),
        ("foundation.yml", b"contents: read", b"contents: write", "P0_CLOSURE_FOUNDATION_WORKFLOW_INVALID"),
        ("foundation.yml", b"    timeout-minutes: 60", b"    timeout-minutes: 60\n    permissions:\n      contents: write", "P0_CLOSURE_FOUNDATION_WORKFLOW_INVALID"),
        ("foundation.yml", b"      attestations: read", b"      attestations: write", "P0_CLOSURE_FOUNDATION_WORKFLOW_INVALID"),
        ("foundation.yml", b"          GH_TOKEN: ${{ github.token }}", b"          GH_TOKEN: untrusted", "P0_CLOSURE_FOUNDATION_WORKFLOW_INVALID"),
        ("foundation.yml", b"  pull_request:\n", b"  schedule:\n", "P0_CLOSURE_FOUNDATION_WORKFLOW_INVALID"),
        ("foundation.yml", b"include-hidden-files: true", b"include-hidden-files: false", "P0_CLOSURE_FOUNDATION_WORKFLOW_INVALID"),
        ("foundation.yml", b'git archive "$base_sha"', b'git archive "$GITHUB_SHA"', "P0_CLOSURE_FOUNDATION_WORKFLOW_INVALID"),
        ("foundation.yml", b"if: github.event_name == 'pull_request'", b"if: always()", "P0_CLOSURE_FOUNDATION_WORKFLOW_INVALID"),
        ("foundation.yml", b"p1-class-d-approved", b"p1-class-d-unreviewed", "P0_CLOSURE_FOUNDATION_WORKFLOW_INVALID"),
        ("foundation.yml", b"types: [opened, synchronize, reopened, labeled, unlabeled]", b"types: [opened, synchronize, reopened, closed]", "P0_CLOSURE_FOUNDATION_WORKFLOW_INVALID"),
        (
            "foundation.yml",
            b"path: ${{ runner.temp }}/trading-agent-ci-portable-publication.${{ github.run_id }}.${{ github.run_attempt }}/artifact/**",
            b"path: ${{ runner.temp }}/trading-agent-ci-portable-publication/artifact/**",
            "P0_CLOSURE_FOUNDATION_WORKFLOW_INVALID",
        ),
        ("host-authority.yml", b"  workflow_dispatch:\n", b"  push:\n", "P0_CLOSURE_HOST_WORKFLOW_INVALID"),
        ("host-authority.yml", b"runs-on: [self-hosted, linux, x64, trading-authority]", b"runs-on: ubuntu-24.04", "P0_CLOSURE_HOST_WORKFLOW_INVALID"),
        ("host-authority.yml", b"make ci-pre-p3-host-authority NONINTERACTIVE=1", b"make ci-portable NONINTERACTIVE=1", "P0_CLOSURE_HOST_WORKFLOW_INVALID"),
        ("host-authority.yml", b"          - p2-security-master-runtime-green-v1", b"          - unapproved-operation", "P0_CLOSURE_HOST_WORKFLOW_INVALID"),
        ("host-authority.yml", b"timeout-minutes: 90", b"timeout-minutes: 900", "P0_CLOSURE_HOST_WORKFLOW_INVALID"),
        ("host-authority.yml", b"qualify_pre_p3.py p2-runtime-v2", b"qualify_pre_p3.py p2-source-v2", "P0_CLOSURE_HOST_WORKFLOW_INVALID"),
        (
            "host-authority.yml",
            b"${{ runner.temp }}/pre-p3-qualification.${{ github.run_id }}.${{ github.run_attempt }}/*.json",
            b"${{ runner.temp }}/**/*.json",
            "P0_CLOSURE_HOST_WORKFLOW_INVALID",
        ),
    ],
    ids=["live-true", "portable-runner", "check-context", "permission", "job-permission", "attestation-read", "gate-token", "trigger", "hidden", "classifier-base", "classifier-event", "classifier-label", "pull-request-events", "upload-path", "host-trigger", "host-runner", "host-route", "host-operation", "host-timeout", "host-runtime", "host-artifact-path"],
)
def test_structural_workflow_contract_attacks_fail_closed(context: closure._ValidationContext, workflow: str, old: bytes, new: bytes, code: str) -> None:
    """Break caught: workflow authority, route, runner or artifact custody drifts."""
    path = context.root / ".github/workflows" / workflow
    raw = path.read_bytes()
    assert old in raw
    path.write_bytes(raw.replace(old, new, 1))
    _error(context, code)


@pytest.mark.parametrize("workflow", ["foundation.yml", "host-authority.yml"])
def test_workflow_unknown_command_step_is_not_approved(context: closure._ValidationContext, workflow: str) -> None:
    """Break caught: an extra network/production command coexists with approved steps."""
    path = context.root / ".github/workflows" / workflow
    raw = path.read_bytes()
    marker = b"      - name: Run canonical local and CI gate" if workflow == "foundation.yml" else b"      - name: Run host authority qualification"
    assert marker in raw
    extra = b"      - name: Unapproved extra command\n        run: echo unapproved\n\n"
    path.write_bytes(raw.replace(marker, extra + marker, 1))
    _error(
        context,
        "P0_CLOSURE_FOUNDATION_WORKFLOW_INVALID" if workflow == "foundation.yml" else "P0_CLOSURE_HOST_WORKFLOW_INVALID",
    )


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (lambda ctx: replace(ctx, active_rows=ctx.active_rows[:-1]), "P0_CLOSURE_TOPOLOGY_COUNT_INVALID"),
        (lambda ctx: replace(ctx, closed_rows=ctx.closed_rows[:-1]), "P0_CLOSURE_TOPOLOGY_COUNT_INVALID"),
        (lambda ctx: replace(ctx, active_rows=(replace(ctx.active_rows[0], classification="PORTABLE_SOURCE_DEFECT"), *ctx.active_rows[1:])), "P0_CLOSURE_TOPOLOGY_ACTIVE_SOURCE_DEFECT"),
        (lambda ctx: replace(ctx, active_rows=(replace(ctx.active_rows[0], code="SRC-OPEN"), *ctx.active_rows[1:])), "P0_CLOSURE_TOPOLOGY_ACTIVE_SOURCE_DEFECT"),
        (lambda ctx: replace(ctx, active_rows=(replace(ctx.active_rows[0], classification="NATIVE_CAPABILITY_REQUIRED"), *ctx.active_rows[1:])), "P0_CLOSURE_TOPOLOGY_CLASSIFICATION_INVALID"),
        (lambda ctx: replace(ctx, closed_rows=(replace(ctx.closed_rows[0], proof_command="PYTEST_BROAD_SKIP_V1"), *ctx.closed_rows[1:])), "P0_CLOSURE_TOPOLOGY_BROAD_SKIP"),
        (lambda ctx: replace(ctx, active_rows=(replace(ctx.active_rows[0], node_id=ctx.closed_rows[0].node_id), *ctx.active_rows[1:])), "P0_CLOSURE_TOPOLOGY_OVERLAP"),
    ],
    ids=["active-count", "closed-count", "source-defect", "src-code", "classification", "broad-skip", "overlap"],
)
def test_topology_attack_matrix_fails_closed(context: closure._ValidationContext, mutation, code: str) -> None:
    """Break caught: active/closed topology facts are recategorized as PASS."""
    _error(mutation(context), code)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("qualified_sha", "0" * 40),
        ("paper_only", False),
        ("live_execution_authorized", True),
        ("promotion_mode", "merge"),
        ("candidate_start_sha", "not-a-sha"),
    ],
)
def test_baseline_lineage_and_authority_tampering_fails_closed(context: closure._ValidationContext, field: str, value: object) -> None:
    """Break caught: mutable lineage or live authority enters source baseline."""
    path = context.root / "ops/consolidation/p0-canonical-baseline.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document[field] = value
    path.write_text(json.dumps(document), encoding="utf-8")
    _error(context, "P0_CLOSURE_BASELINE_AUTHORITY_INVALID" if field in {"qualified_sha", "paper_only", "live_execution_authorized"} else "P0_CLOSURE_BASELINE_SCHEMA_INVALID")


@pytest.mark.parametrize(
    ("attack", "code"),
    [
        ("hardlink", "P0_CLOSURE_IMPLEMENTATION_PATH_UNSAFE"),
        ("symlink", "P0_CLOSURE_IMPLEMENTATION_PATH_UNSAFE"),
        ("fifo", "P0_CLOSURE_IMPLEMENTATION_PATH_UNSAFE"),
        ("untracked", "P0_CLOSURE_IMPLEMENTATION_PATH_UNTRACKED"),
    ],
)
def test_bound_path_custody_attack_matrix_fails_closed(context: closure._ValidationContext, attack: str, code: str) -> None:
    """Break caught: untracked, hardlinked, symlinked or special evidence is trusted."""
    path = context.root / "scripts/audit_canonical_repo.py"
    if attack == "untracked":
        untracked = context.root / "untracked.txt"
        untracked.write_text("x", encoding="utf-8")
        document = _document(context)
        document["requirements"][0]["implementation_paths"] = ["untracked.txt"]
        _set_document(context, document)
    else:
        path.unlink()
        if attack == "hardlink":
            path.hardlink_to(context.root / "tracked-donor.txt")
        elif attack == "symlink":
            path.symlink_to(context.root / "tracked-donor.txt")
        else:
            os.mkfifo(path)
    _error(context, code)


def test_nonregular_custody_rejection_does_not_leak_descriptors(context: closure._ValidationContext) -> None:
    """Break caught: repeated FIFO rejection leaves retained descriptors."""
    fifo = context.root / "probe.fifo"
    os.mkfifo(fifo)
    before = len(tuple(Path("/proc/self/fd").iterdir()))
    for _ in range(8):
        with pytest.raises(closure.ClosureError, match="^P0_CLOSURE_BOUND_UNSAFE$"):
            closure._safe_read(context, "probe.fifo", label="BOUND")
    assert len(tuple(Path("/proc/self/fd").iterdir())) == before


def test_matrix_and_parent_path_attacks_fail_closed(context: closure._ValidationContext) -> None:
    """Break caught: the matrix or a parent component is replaced after context creation."""
    matrix = context.root / MATRIX_RELATIVE
    saved = matrix.read_bytes()
    matrix.unlink()
    matrix.symlink_to(context.root / "Makefile")
    _error(context, "P0_CLOSURE_MATRIX_UNSAFE")
    matrix.unlink()
    matrix.write_bytes(saved)

    document = _document(context)
    document["requirements"][0]["implementation_paths"] = ["alias/README.md"]
    _set_document(context, document)
    (context.root / "real").mkdir()
    shutil.copy2(ROOT / "README.md", context.root / "real/README.md")
    (context.root / "alias").symlink_to(context.root / "real", target_is_directory=True)
    _error(context, "P0_CLOSURE_IMPLEMENTATION_PATH_UNSAFE")


def test_missing_file_node_unknown_target_and_host_mapping_fail_closed(context: closure._ValidationContext) -> None:
    """Break caught: an unexecutable matrix binding is accepted."""
    document = _document(context)
    document["requirements"][0]["implementation_paths"] = ["missing.py"]
    _set_document(context, document)
    _error(context, "P0_CLOSURE_IMPLEMENTATION_PATH_MISSING")

    context = replace(context, collected_node_ids=frozenset())
    _set_document(context, json.loads((ROOT / MATRIX_RELATIVE).read_text(encoding="utf-8")))
    _error(context, "P0_CLOSURE_TEST_NODE_UNCOLLECTED")

    context = replace(context, collected_node_ids=source_context_nodes())
    document = _document(context)
    document["requirements"][0]["make_target"] = "missing-target"
    _set_document(context, document)
    _error(context, "P0_CLOSURE_MAKE_TARGET_UNKNOWN")

    document = json.loads((ROOT / MATRIX_RELATIVE).read_text(encoding="utf-8"))
    document["requirements"][0]["workflow"] = ".github/workflows/host-authority.yml"
    _set_document(context, document)
    _error(context, "P0_CLOSURE_PORTABLE_WORKFLOW_INVALID")


def source_context_nodes() -> frozenset[str]:
    matrix = json.loads((ROOT / MATRIX_RELATIVE).read_text(encoding="utf-8"))
    return frozenset(node for entry in matrix["requirements"] for node in entry["test_node_ids"])


RUN_1 = "runtime/state/p0-qualification/run-1/manifest.json"
RUN_2 = "runtime/state/p0-qualification/run-2/manifest.json"
FINAL_REVIEW = "runtime/state/p0-qualification/final-review.json"


def _publish_run(
    tmp_path: Path, destination: Path, *, run_id: str,
    selected_test: bool = False, governed_error: bool = False,
    head_sha: str = HEAD, source_tree_sha256: str = TREE,
) -> dict[str, object]:
    from scripts import check_artifact_firewall as firewall
    from tests.test_artifact_firewall import (
        _run_metadata, _semantic, _staging, _write_leaf,
    )

    staging = _staging(tmp_path)
    semantic = _semantic()
    if selected_test:
        summary_path = staging / "test-governance/summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["tests"] = [{
            "test_node_id": "tests/example.py::test_selected",
            "component": "root", "phase": "call", "outcome": "passed",
        }]
        _write_leaf(staging, "test-governance/summary.json", summary)
        semantic["selected_tests"] = [{
            "component": "root", "node_id": "tests/example.py::test_selected",
            "outcome": "passed", "phase": "call",
        }]
    if governed_error:
        (staging / "test-governance/summary.json").unlink()
        error_semantic = {"error_code": "SUITE_FAILURE", "suite_exit_codes": {"root": 1}}
        _write_leaf(staging, "test-governance/error.json", {
            "schema_version": "test-governance-final-error/v1",
            "status": "error", "generated_at_utc": "2026-08-13T12:00:00+00:00",
            **error_semantic,
        })
        semantic.pop("selected_tests")
        semantic["governance_error"] = error_semantic
    return firewall.publish_evidence_set(
        staging_root=staging, destination=destination, head_sha=head_sha,
        source_tree_sha256=source_tree_sha256, semantic_projection=semantic,
        run_metadata=_run_metadata(run_id=run_id),
    )


def _write_final_review(
    context: closure._ValidationContext,
    manifests: tuple[dict[str, object], dict[str, object]],
    *, tamper: str | None = None,
) -> None:
    from scripts import check_artifact_firewall as firewall

    entries = []
    for relative, manifest in zip((RUN_1, RUN_2), manifests, strict=True):
        entries.append({
            "path": relative,
            "manifest_sha256": hashlib.sha256(firewall._canonical_json_bytes(manifest)).hexdigest(),
            "semantic_result_sha256": manifest["semantic_result_sha256"],
            "run_id": manifest["run_metadata"]["run_id"],
            "run_attempt": manifest["run_metadata"]["attempt"],
        })
    review: dict[str, object] = {
        "schema_version": "p0-final-adversarial-review/v1",
        "verdict": "APPROVED",
        "head_sha": context.head_sha,
        "source_tree_sha256": context.source_tree_sha256,
        "receipts": entries,
        "review_receipt_sha256": "",
    }
    review["review_receipt_sha256"] = hashlib.sha256(_canonical(review)).hexdigest()
    if tamper == "verdict":
        review["verdict"] = "REJECTED"
    elif tamper == "binding":
        review["receipts"][0]["semantic_result_sha256"] = "0" * 64
    elif tamper == "self-hash":
        review["review_receipt_sha256"] = "0" * 64
    path = context.root / FINAL_REVIEW
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_bytes(_canonical(review))
    path.chmod(0o400)
    path.parent.chmod(0o500)


def _qualification_context(
    context: closure._ValidationContext, tmp_path: Path, *,
    same_identity: bool = False, different_semantic: bool = False,
    governed_error: bool = False, review_tamper: str | None = None,
) -> closure._ValidationContext:
    first = _publish_run(
        tmp_path / "first", context.root / RUN_1.removesuffix("/manifest.json"),
        run_id="1001",
    )
    second = _publish_run(
        tmp_path / "second", context.root / RUN_2.removesuffix("/manifest.json"),
        run_id="1001" if same_identity else "1002",
        selected_test=different_semantic,
        governed_error=governed_error,
    )
    complete = replace(
        context, receipt_relatives=(RUN_1, RUN_2), review_relative=FINAL_REVIEW,
        head_sha=HEAD, source_tree_sha256=TREE,
    )
    _write_final_review(complete, (first, second), tamper=review_tamper)
    return complete


def test_completion_rejects_wrong_path_stale_tree_and_partial_receipt(
    context: closure._ValidationContext, tmp_path: Path,
) -> None:
    """Break caught: path-adjacent, stale, missing, duplicate or partial evidence earns completion."""
    _error(context, "P0_CLOSURE_RECEIPT_COUNT_INVALID", complete=True)
    one = replace(context, receipt_relatives=(RUN_1,))
    _error(one, "P0_CLOSURE_RECEIPT_COUNT_INVALID", complete=True)
    duplicate = replace(context, receipt_relatives=(RUN_1, RUN_1))
    _error(duplicate, "P0_CLOSURE_RECEIPT_SET_INVALID", complete=True)
    wrong = replace(context, receipt_relatives=(RUN_1, "runtime/state/other/manifest.json"))
    _error(wrong, "P0_CLOSURE_RECEIPT_PATH_INVALID", complete=True)

    complete = _qualification_context(context, tmp_path)
    _error(replace(complete, source_tree_sha256="b" * 64), "P0_CLOSURE_RECEIPT_INVALID", complete=True)
    _error(replace(complete, head_sha="8" * 40), "P0_CLOSURE_RECEIPT_INVALID", complete=True)


def test_source_matrix_cannot_self_declare_completion(context: closure._ValidationContext) -> None:
    """Break caught: committed JSON promotes E11 without external qualification evidence."""
    document = _document(context)
    document["state"] = "P0_SOURCE_COMPLETE"
    document["requirements"][16]["required_status"] = "PASS"
    _set_document(context, document)
    _error(context, "P0_CLOSURE_COMPLETION_MODE_INVALID")


def test_valid_strict_p0_10_fixture_exercises_completion_mode(
    context: closure._ValidationContext, tmp_path: Path,
) -> None:
    """Break caught: two exact runs plus final review cannot exercise public completion."""
    complete = _qualification_context(context, tmp_path)
    assert closure._validate(complete, require_complete=True) == "P0_SOURCE_COMPLETE"


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ({"same_identity": True}, "P0_CLOSURE_RUN_IDENTITY_INVALID"),
        ({"different_semantic": True}, "P0_CLOSURE_SEMANTIC_MISMATCH"),
        ({"governed_error": True}, "P0_CLOSURE_RECEIPT_INVALID"),
        ({"review_tamper": "verdict"}, "P0_CLOSURE_REVIEW_INVALID"),
        ({"review_tamper": "binding"}, "P0_CLOSURE_REVIEW_INVALID"),
        ({"review_tamper": "self-hash"}, "P0_CLOSURE_REVIEW_INVALID"),
    ],
)
def test_completion_evidence_attack_matrix_fails_closed(
    context: closure._ValidationContext, tmp_path: Path,
    mutation: dict[str, object], code: str,
) -> None:
    """Break caught: duplicate/different/error/tampered qualification facts earn PASS."""
    complete = _qualification_context(context, tmp_path, **mutation)
    _error(complete, code, complete=True)


def test_completion_requires_exact_final_review_receipt(
    context: closure._ValidationContext, tmp_path: Path,
) -> None:
    """Break caught: two matching runs earn completion without independent review."""
    complete = _qualification_context(context, tmp_path)
    review = context.root / FINAL_REVIEW
    review.parent.chmod(0o700)
    review.unlink()
    review.parent.chmod(0o500)
    _error(complete, "P0_CLOSURE_REVIEW_MISSING", complete=True)
    _error(
        replace(complete, review_relative="runtime/state/p0-qualification/other-review.json"),
        "P0_CLOSURE_REVIEW_PATH_INVALID", complete=True,
    )


def test_partial_p0_10_manifest_cannot_earn_completion(
    context: closure._ValidationContext,
) -> None:
    """Break caught: manifest-shaped JSON substitutes for a complete evidence tree."""
    manifest = context.root / RUN_1
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_bytes(_canonical({"head_sha": HEAD, "source_tree_sha256": TREE}))
    partial = replace(
        context, receipt_relatives=(RUN_1, RUN_2), review_relative=FINAL_REVIEW,
        head_sha=HEAD, source_tree_sha256=TREE,
    )
    _error(partial, "P0_CLOSURE_RECEIPT_INVALID", complete=True)


def test_public_completion_cli_uses_canonical_temp_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: the reviewed completion path exists only as an internal API."""
    from scripts import check_artifact_firewall as firewall
    from scripts import t_g03_capability_topology as topology
    from tests import test_artifact_firewall as fixture

    clone = tmp_path / "canonical-repository"
    subprocess.run(["git", "clone", "-q", "--shared", str(ROOT), str(clone)], check=True)
    for relative in (
            MATRIX_RELATIVE, "scripts/check_p0_ci_closure.py", "tests/test_p0_ci_closure.py",
            "scripts/check_artifact_firewall.py", "scripts/t_g03_capability_topology.py",
        "tests/fixtures/t-g03a-hosted-failure-inventory.tsv",
        "docs/implementation/p0-ci-closure.md", "Makefile",
        ".github/workflows/foundation.yml", ".github/workflows/host-authority.yml",
    ):
        shutil.copy2(ROOT / relative, clone / relative)
    _git(clone, "add", "--", ".")
    if _git(clone, "status", "--porcelain").stdout:
        _git(
            clone, "-c", "user.name=P0 test", "-c", "user.email=p0@example.invalid",
            "commit", "-qm", "completion fixture",
        )
    head = _git(clone, "rev-parse", "HEAD").stdout.strip()
    tree = firewall._source_tree_identity(clone, head)
    monkeypatch.setattr(fixture, "HEAD", head)
    monkeypatch.setattr(fixture, "TREE", tree)
    monkeypatch.setattr(topology, "ROOT", ROOT)
    first = _publish_run(
        tmp_path / "public-first", clone / RUN_1.removesuffix("/manifest.json"),
        run_id="2001", head_sha=head, source_tree_sha256=tree,
    )
    second = _publish_run(
        tmp_path / "public-second", clone / RUN_2.removesuffix("/manifest.json"),
        run_id="2002", head_sha=head, source_tree_sha256=tree,
    )
    public_context = replace(
        closure._production_context(), root=clone, head_sha=head,
        source_tree_sha256=tree, receipt_relatives=(RUN_1, RUN_2),
        review_relative=FINAL_REVIEW,
    )
    _write_final_review(public_context, (first, second))
    result = subprocess.run(
        [
            sys.executable, "scripts/check_p0_ci_closure.py", "--matrix", MATRIX_RELATIVE,
            "--qualification-receipt", RUN_1, "--qualification-receipt", RUN_2,
            "--final-review-receipt", FINAL_REVIEW, "--require-complete",
        ],
        cwd=clone, text=True, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "P0_SOURCE_COMPLETE"
