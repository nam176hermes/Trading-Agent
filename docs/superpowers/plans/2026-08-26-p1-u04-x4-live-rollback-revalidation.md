# P1-U04 X4 Live Rollback Revalidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every X4 build/publication receipt validation recompute and exactly match the current retained 1.227 schema-6 rollback authority.

**Architecture:** Reuse the existing physical rollback attestor in `materialize_nautilus_runtime_closure.py`; expose one narrow builder projection helper and compare its result inside the shared X4 receipt validator. Build A/B already route every entry and publication boundary through that validator, so no caller-specific guards or schema changes are needed.

**Tech Stack:** Python 3.11, stdlib `importlib`/JSON/path validation, existing schema-6 Nautilus closure attestor, pytest, uv frozen/offline.

**Spec:** `docs/superpowers/specs/2026-08-26-p1-u04-x4-live-rollback-revalidation-design.md`

## Global Constraints

- Packet scope is only live schema-6 rollback recomputation at the X4 production boundary and stale-replay regression.
- Maximum two implementation/review rounds; an Important or Critical finding remaining after round 2 ends `P1_U04_ARCHITECTURE_ESCALATION_REQUIRED`.
- Do not run native Build A/B until implementation, fresh spec review, fresh security/replay review, exact-source X4 re-preflight, and fresh X4 receipt reviews all PASS.
- Preserve active/rollback 1.227 byte-for-byte and keep 1.231 candidate-only and inactive.
- Portable tests may use synthetic rollback projection only inside test fixtures; host/native checks may not use synthetic authority.
- Missing external authority is `DEFERRED`; supplied invalid authority is `FAIL`.
- No skip, xfail, weakened assertion, network/package fallback, moving version, ambient compiler/package authority, push, PR, merge, deploy, production mutation, broker access, or network/live trading.
- Source planning base is exact commit `80b432b84048747810143b33b9ba6be8d2cd8547`, tree `b2a51a08552766be9beb6d4d098bf03e5718c366`; the controller must record the exact post-plan execution HEAD/tree in the SDD ledger before implementation.

---

### Task 1: Bind X4 receipt validation to live rollback authority

**Files:**
- Modify: `scripts/build_nautilus_engine.py`
- Test: `tests/nautilus_upgrade/test_v1231_candidate_closure.py`

**Interfaces:**
- Consumes: `_candidate_roots(engine)["rollback_root"]`, the existing materializer `_load_policy()`, `_validate_base_runtime()`, and `_selected_base_authority()` interfaces.
- Produces: `_candidate_live_rollback_authority(rollback_root: Path) -> dict[str, object]`, returning exactly the canonical eight-field X4 rollback receipt projection.
- Enforces: `_validate_x4_authority_receipt(..., phase="A" | "B" | "FINAL")` rejects when `checks.rollback_authority` differs from the live projection.

- [ ] **Step 1: Extend the portable X4 fixture with its recorded rollback projection**

After constructing `receipt_document` in `_write_x4_authority_receipt()`, inject the fixture's exact recorded projection without touching host authority:

```python
monkeypatch.setattr(
    builder,
    "_candidate_live_rollback_authority",
    lambda _rollback_root: copy.deepcopy(
        receipt_document["checks"]["rollback_authority"]
    ),
    raising=False,
)
```

This is portable test authority only. Production and host/native tests must call the real helper.

- [ ] **Step 2: Write the stale-rollback replay regression**

Add a Build A test beside the existing phase-A revalidation test. Reuse the existing fake build setup, then replace `_candidate_live_rollback_authority` with a function that returns the receipt projection on the first call and the same projection with `closure_sha256 = "9" * 64` on the second call:

```python
def test_candidate_build_a_rejects_live_rollback_drift_before_publication(
    x4_posix_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt, receipt_sha256, _engine, _inputs, roots = _write_x4_authority_receipt(
        x4_posix_tmp_path, monkeypatch
    )
    recorded = copy.deepcopy(json.loads(receipt.read_bytes())["checks"]["rollback_authority"])
    calls = 0

    def live_projection(_rollback_root: Path) -> dict[str, object]:
        nonlocal calls
        calls += 1
        observed = copy.deepcopy(recorded)
        if calls == 2:
            observed["closure_sha256"] = "9" * 64
        return observed

    monkeypatch.setattr(builder, "_candidate_live_rollback_authority", live_projection, raising=False)
    descriptor = os.open(
        x4_posix_tmp_path, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW
    )
    monkeypatch.setattr(builder, "_materialize_candidate_inputs", lambda *_args: roots)
    monkeypatch.setattr(
        builder,
        "_build_candidate_once",
        lambda *_args: (
            b"wheel-a",
            _empty_candidate_preflight(),
            {
                "wheel": {
                    "filename": WHEEL_FILENAME,
                    "sha256": hashlib.sha256(b"wheel-a").hexdigest(),
                    "size": 7,
                }
            },
            {"P1_U04_SOURCE_ST_DEV": "1", "P1_U04_SOURCE_ST_INO": "2"},
            descriptor,
        ),
    )
    monkeypatch.setattr(
        builder,
        "_candidate_process_identity",
        lambda: {
            "boot_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "pid": 100,
            "start_time_ticks": 200,
        },
    )
    with pytest.raises(builder.VerificationError, match="live rollback authority drifted"):
        builder.build_candidate_a(
            authority_receipt=receipt,
            authority_receipt_sha256=receipt_sha256,
        )
    assert calls == 2
    assert not (roots["candidate_build_root"] / "build-a").exists()
```

The production break this catches is removal of the live equality check or omission of the second phase-A recomputation.

- [ ] **Step 3: Run RED and record the expected failure**

Run:

```bash
UV_OFFLINE=1 uv run --frozen --offline pytest -q \
  tests/nautilus_upgrade/test_v1231_candidate_closure.py::test_candidate_build_a_rejects_live_rollback_drift_before_publication
```

Expected: FAIL because Build A publishes `build-a` instead of raising on the changed live rollback projection. An import or fixture error is not an acceptable RED.

- [ ] **Step 4: Add the exact materializer loader and live projection**

In `scripts/build_nautilus_engine.py`, add the exact tool path beside the other candidate tool constants:

```python
_CANDIDATE_RUNTIME_CLOSURE_TOOL = (
    _ROOT / "scripts/materialize_nautilus_runtime_closure.py"
)
```

Add a loader following the existing `_load_candidate_generator()` pattern:

```python
def _load_candidate_runtime_closure_tool():
    spec = importlib.util.spec_from_file_location(
        "materialize_nautilus_runtime_closure_for_candidate_build",
        _CANDIDATE_RUNTIME_CLOSURE_TOOL,
    )
    if spec is None or spec.loader is None:
        raise VerificationError("candidate rollback authority verifier is unavailable")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise VerificationError(
            "candidate rollback authority verifier is unavailable"
        ) from exc
    return module
```

Add the narrow projection helper:

```python
def _candidate_live_rollback_authority(rollback_root: Path) -> dict[str, object]:
    materializer = _load_candidate_runtime_closure_tool()
    try:
        policy = materializer._load_policy(materializer._CANDIDATE_BASE_POLICY)
        historical_manifest, historical_records = materializer._validate_base_runtime(
            rollback_root / "runtime-closure-v3", policy
        )
        selected = materializer._selected_base_authority(
            rollback_root,
            base_policy=policy,
            historical_manifest=historical_manifest,
            historical_records=historical_records,
        )
        return {
            "artifact_generation": selected["artifact_generation"],
            "artifact_manifest_sha256": selected["artifact_manifest_sha256"],
            "closure_sha256": selected["closure_sha256"],
            "generation": selected["generation"],
            "manifest_mode": selected["manifest_mode"],
            "manifest_sha256": selected["manifest_sha256"],
            "result": "PASS",
            "schema": 6,
        }
    except Exception as exc:
        raise VerificationError(
            "X4 authority receipt live rollback authority is invalid"
        ) from exc
```

The broad exception translation is deliberate at this authority boundary: any
loader, physical attestation, or projection failure stops the build. Do not add
a fallback.

- [ ] **Step 5: Enforce exact equality in the shared X4 validator**

Immediately after the existing strict checks object validation and before phase-specific root-state acceptance, add:

```python
if rollback != _candidate_live_rollback_authority(roots["rollback_root"]):
    raise VerificationError("X4 authority receipt live rollback authority drifted")
```

Do not add checks in individual Build A/B callers; every boundary already calls this shared validator.

- [ ] **Step 6: Run GREEN focused tests**

Run:

```bash
UV_OFFLINE=1 uv run --frozen --offline pytest -q \
  tests/nautilus_upgrade/test_v1231_candidate_closure.py::test_candidate_build_a_rejects_live_rollback_drift_before_publication \
  tests/nautilus_upgrade/test_v1231_candidate_closure.py::test_canonical_x4_authority_receipt_with_checks_is_accepted \
  tests/nautilus_upgrade/test_v1231_candidate_closure.py::test_candidate_build_a_runs_one_build_and_never_publishes_final_artifacts
```

Expected: `3 passed` with no skips or xfails.

- [ ] **Step 7: Run the full portable candidate-closure suite**

Run:

```bash
UV_OFFLINE=1 uv run --frozen --offline pytest -q \
  tests/nautilus_upgrade/test_v1231_candidate_closure.py
```

Expected: all tests pass with zero skips and zero xfails. Do not access `/home/thenam176`, private caches, or real `/usr/bin/bwrap` through this portable suite.

- [ ] **Step 8: Verify diff and commit round 1**

Run:

```bash
git diff --check
git status --short
```

Commit only the two task files:

```bash
git add scripts/build_nautilus_engine.py \
  tests/nautilus_upgrade/test_v1231_candidate_closure.py
git commit -m "fix(p1-u04): revalidate live rollback authority"
```

- [ ] **Step 9: Fresh review gate**

Dispatch fresh independent spec and security/replay reviewers against the exact execution-base-to-round-1 range. Both must report zero Critical and zero Important findings. If either fails, perform at most one TDD fix/re-review round. If an Important or Critical finding remains after round 2, stop with `P1_U04_ARCHITECTURE_ESCALATION_REQUIRED` and do not build.
