# P1-U04 X4 Build-Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the candidate Build A and Build B into receipt-bound, independently invocable process boundaries without running a native build.

**Architecture:** Keep all authority paths internally pinned by the existing candidate policies. Add exact X4 receipt validation, sealed policy-derived `build-a`/`build-b` records, and two mutually exclusive CLI actions; Build B consumes Build A and publishes final artifacts only after reproducibility comparison.

**Tech Stack:** Python 3.11 orchestration, CPython 3.12 candidate authority, pytest, Linux `/proc`, Git, Bubblewrap policy validation.

**Spec:** `docs/superpowers/specs/2026-08-25-p1-u04-x4-build-boundary-design.md`

## Global Constraints

- Maximum two implementation/review rounds for this architecture packet.
- Do not run `--build-candidate-a`, `--build-candidate-b`, the old `--build-candidate`, or any native build during implementation.
- X5 remains forbidden until the new exact HEAD/tree passes X4 re-preflight and fresh spec plus security/replay reviews.
- Preserve active/rollback 1.227 source policy and external bytes byte-for-byte.
- Keep 1.231 candidate-only; do not activate or promote it.
- Do not accept arbitrary caller-selected compiler, Python, sandbox, artifact, cache, Cargo, LLVM, or toolchain authority.
- No network, package fallback, root command, broker/exchange access, live trading, push, PR, merge, deploy, skip, xfail, or weakened assertion.
- Production changes are limited to `scripts/build_nautilus_engine.py` and the exact downstream receipt validation in `scripts/materialize_nautilus_runtime_closure.py`; behavior tests stay in `tests/nautilus_upgrade/test_v1231_candidate_closure.py`.
- Preserve the existing uncommitted `docs/implementation/p1-real-nautilus/task-ledger.md` change and untracked `.superpowers/plans/P1_U04_X4_X9_EXECUTION_PLAN.md`; do not stage or overwrite them.

---

### Task 1: Receipt-bound separate candidate build actions

**Files:**
- Modify: `scripts/build_nautilus_engine.py`
- Modify: `scripts/materialize_nautilus_runtime_closure.py`
- Test: `tests/nautilus_upgrade/test_v1231_candidate_closure.py`

**Interfaces:**
- Consumes: canonical X4 receipt schema `p1-u04-x4-authority-preflight-v1`, caller-supplied receipt SHA-256, committed candidate policies, `_verify_candidate_authority()`, `_materialize_candidate_inputs()`, and `_build_candidate_once()`.
- Produces: `build_candidate_a(*, authority_receipt: Path, authority_receipt_sha256: str) -> dict[str, object]`.
- Produces: `build_candidate_b(*, authority_receipt: Path, authority_receipt_sha256: str, retain_raw_wheel_pair: bool = False) -> dict[str, object]`.
- Produces: CLI actions `--build-candidate-a` and `--build-candidate-b`, each requiring `--offline`, `--authority-receipt`, and `--authority-receipt-sha256`.
- Produces: sealed policy-derived children `candidate_build_root/build-a`, `candidate_build_root/build-b`, and the existing final `candidate_build_root/artifacts`.
- Preserves: final artifact manifest fields required by `scripts/materialize_nautilus_runtime_closure.py`, including `build_count == 2`, two source identities, raw/native/manifest equality, and Build A wheel authority.
- Extends: the final reproducibility receipt with `process_identities`, validated downstream as exactly two distinct Linux boot/PID/start-time records.

- [ ] **Step 1: Add receipt and split-boundary RED tests**

Add literal behavior tests that exercise real parser/public functions while monkeypatching only the native build primitive and live external-authority probes:

```python
def test_candidate_build_a_runs_one_build_and_never_publishes_final_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = builder.build_candidate_a(
        authority_receipt=receipt,
        authority_receipt_sha256=receipt_sha256,
    )
    assert build_calls == ["A"]
    assert result["kind"] == "P1_U04_BUILD_A"
    assert (build_root / "build-a").is_dir()
    assert not (build_root / "build-b").exists()
    assert not (build_root / "artifacts").exists()


def test_candidate_build_b_requires_a_different_process_and_same_x4_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(builder.VerificationError, match="distinct process"):
        builder.build_candidate_b(
            authority_receipt=receipt,
            authority_receipt_sha256=receipt_sha256,
        )


@pytest.mark.parametrize("mutation", ("digest", "schema", "verdict", "head", "tree", "mode", "link"))
def test_candidate_actions_reject_untrusted_x4_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    with pytest.raises(builder.VerificationError, match="X4 authority receipt"):
        builder.build_candidate_a(
            authority_receipt=receipt,
            authority_receipt_sha256=expected_sha256,
        )


def test_candidate_build_b_rejects_modified_build_a_and_leaves_final_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(builder.VerificationError, match="Build A"):
        builder.build_candidate_b(
            authority_receipt=receipt,
            authority_receipt_sha256=receipt_sha256,
        )
    assert not (build_root / "artifacts").exists()
```

Add CLI tests proving the two actions are mutually exclusive; receipt arguments are mandatory; legacy authority-path flags and non-offline mode fail before calling a build function. Name every test for the production break it catches and use literal expected values rather than builder helpers for expected receipt fields.

- [ ] **Step 2: Run the focused tests and capture RED**

Run:

```bash
uv run pytest -q tests/nautilus_upgrade/test_v1231_candidate_closure.py \
  -k 'candidate_build_a or candidate_build_b or untrusted_x4_receipt or candidate_split_cli'
```

Expected: FAIL because `build_candidate_a`, `build_candidate_b`, and the split CLI actions do not exist and the current entry point still executes two builds in one process. Record exact failing node IDs and failure messages in the task report before editing production code.

- [ ] **Step 3: Implement exact receipt validation**

Add these private interfaces to `scripts/build_nautilus_engine.py`:

```python
_X4_RECEIPT_SCHEMA = "p1-u04-x4-authority-preflight-v1"
_BUILD_A_DIRECTORY = "build-a"
_BUILD_B_DIRECTORY = "build-b"


def _candidate_git_identity() -> dict[str, str]:
    """Return clean tracked HEAD/tree from fixed _ROOT using /usr/bin/git."""


def _candidate_process_identity() -> dict[str, object]:
    """Return boot_id, pid, and /proc/self/stat start_time_ticks."""


def _validate_x4_authority_receipt(
    path: Path,
    expected_sha256: str,
    *,
    phase: str,
) -> dict[str, object]:
    """Verify receipt file identity, digest, schema, verdict, source and policy bindings."""
```

Validation must require a regular non-symlink file, task UID, mode `0400`, link count `1`, a lowercase 64-hex expected digest, exact canonical JSON object shape, `build_a_authorized is True`, verdict `X4_READY_FOR_BUILD_A`, exact current clean HEAD/tree, exact committed policy hashes, and a valid hash/size binding to the complete authority receipt. It must rerun `_verify_candidate_authority()` and compare live Python/Bubblewrap/toolchain identities already represented by policy or generated manifest authority. Phase A requires `build-a`, `build-b`, and `artifacts` absent and the private build parent empty. Phase B allows only sealed `build-a`; `build-b` and `artifacts` remain absent.

Use `['/usr/bin/git', '-C', str(_ROOT)]` with `env={"LC_ALL": "C", "LANG": "C"}` only for source identity. Reject dirty tracked/index state. Do not read authority paths from caller-controlled receipt fields; compare them with internally pinned policy values.

- [ ] **Step 4: Implement sealed intermediate result helpers**

Add one minimum shared publication/validation path:

```python
def _publish_candidate_build_result(
    roots: dict[str, Path],
    *,
    label: str,
    wheel_payload: bytes,
    artifact_core: dict[str, object],
    source_identity: dict[str, str],
    process_identity: dict[str, object],
    x4_receipt_sha256: str,
) -> dict[str, object]:
    """Atomically publish build-a or build-b with exact three-file sealed layout."""


def _load_candidate_build_result(
    roots: dict[str, Path],
    *,
    label: str,
) -> tuple[bytes, dict[str, object], dict[str, object]]:
    """Verify and return wheel bytes, artifact core, and build receipt."""
```

The only files are the exact candidate wheel, `artifact-core.json`, and `build-receipt.json`; directories seal to `0500`, files to `0400`. The receipt binds file names, sizes, SHA-256 values, X4 digest, HEAD/tree, process/source identities, policy/toolchain digests, and `sanitized_environment_sha256`, defined as SHA-256 of canonical ASCII JSON for the verified `inputs['native_build_environment']` contract. Publication must use the existing no-replace/private-parent pattern and remove task-owned staging on failure.

- [ ] **Step 5: Implement Build A as one build**

Implement:

```python
def build_candidate_a(
    *,
    authority_receipt: Path,
    authority_receipt_sha256: str,
) -> dict[str, object]:
    receipt = _validate_x4_authority_receipt(
        authority_receipt,
        authority_receipt_sha256,
        phase="A",
    )
    engine, inputs = _verify_candidate_authority()
    roots = _materialize_candidate_inputs(engine, inputs)
    # Call _build_candidate_once exactly once, close its descriptor, then publish build-a.
```

Descriptor closure must occur before intermediate publication and on every failure. Build A returns its receipt and must never invoke reproducibility comparison, publish final artifacts, create Build B/forensic/runtime roots, or call the native primitive twice.

- [ ] **Step 6: Implement Build B as one separate-process build and comparison gate**

Implement:

```python
def build_candidate_b(
    *,
    authority_receipt: Path,
    authority_receipt_sha256: str,
    retain_raw_wheel_pair: bool = False,
) -> dict[str, object]:
    receipt = _validate_x4_authority_receipt(
        authority_receipt,
        authority_receipt_sha256,
        phase="B",
    )
    # Load and verify sealed build-a, reject same process/X4 mismatch.
    # Call _build_candidate_once exactly once and publish sealed build-b.
    # Compare raw wheel and artifact core; publish final Build A artifact only on equality.
```

Build B must reject equal process identities, equal source descriptor identities, a different X4 digest, modified Build A files, raw wheel drift, native/core drift, or unexpected roots. The final receipt keeps `build_count: 2`, `fresh_physical_stages: true`, `raw_wheel_equality: true`, `native_inventory_equality: true`, `authoritative_manifest_equality: true`, Build A wheel SHA, and both source plus process identities. Update `_CANDIDATE_REPRODUCIBILITY_FIELDS` and `_validate_candidate_artifact()` in `scripts/materialize_nautilus_runtime_closure.py` so schema-7 materialization requires exactly two distinct process identities with fields `boot_id`, `pid`, and `start_time_ticks`. On mismatch final artifacts remain absent. If explicit forensic retention is requested, retain only the sealed A/B raw pair using the existing forensic contract and still fail.

- [ ] **Step 7: Replace the combined candidate CLI action**

Replace `--build-candidate` with mutually exclusive `--build-candidate-a` and `--build-candidate-b`. Add:

```python
parser.add_argument("--authority-receipt", type=Path)
parser.add_argument("--authority-receipt-sha256")
parser.add_argument("--retain-raw-wheel-pair", action="store_true")
```

Both candidate actions require receipt path, digest, and `--offline`; reject every legacy authority-path argument and non-default sandbox. `--retain-raw-wheel-pair` is accepted only with Build B. Remove the callable same-process two-build path so no production entry point can cross X5 into X6.

- [ ] **Step 8: Run focused GREEN tests**

Run:

```bash
uv run pytest -q tests/nautilus_upgrade/test_v1231_candidate_closure.py \
  -k 'candidate_build_a or candidate_build_b or untrusted_x4_receipt or candidate_split_cli or candidate_forensic or candidate_build_closes'
```

Expected: all selected tests PASS, zero skips/xfails. Do not invoke either native candidate CLI.

- [ ] **Step 9: Run the complete portable candidate-closure regression file**

Run:

```bash
uv run pytest -q tests/nautilus_upgrade/test_v1231_candidate_closure.py
```

Expected: PASS with zero skips/xfails. If the complete file requires real host authority, run the repository's documented portable exclusion for host tests and report the exact excluded node set; never substitute synthetic authority for host/native tests.

- [ ] **Step 10: Self-review and commit only task-owned code/tests**

Verify:

```bash
git diff --check
git diff --stat -- scripts/build_nautilus_engine.py scripts/materialize_nautilus_runtime_closure.py tests/nautilus_upgrade/test_v1231_candidate_closure.py
git status --short
```

Confirm no active/rollback 1.227 policy, candidate policy JSON, baseline inventory, lockfile, workflow, or unrelated dirty file changed. Append the RED and GREEN commands/results, exact commit/tree, and concerns to the task report. Commit only the three task files:

```bash
git add -- scripts/build_nautilus_engine.py scripts/materialize_nautilus_runtime_closure.py tests/nautilus_upgrade/test_v1231_candidate_closure.py
git commit -m "fix(p1-u04): split receipt-bound candidate builds"
```

Do not stage the pre-existing task-ledger change, the untracked continuation plan, caches, `.venv`, receipts, or native outputs.
