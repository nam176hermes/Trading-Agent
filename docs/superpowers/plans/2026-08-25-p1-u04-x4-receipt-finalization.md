# P1-U04 X4 Receipt Finalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the split Build A/B path consume the issued canonical X4 receipt and bind final/schema-7 evidence only to fully validated sealed intermediate bytes.

**Architecture:** Keep the current split CLI and policy-owned roots. Correct the canonical X4 schema projection, extend the existing strict `_load_candidate_build_result()` return value with the digest of its validated receipt bytes, use a final full A/B reload as the publication barrier, and make schema-7 call that same production validator instead of maintaining a weaker receipt-only loop.

**Tech Stack:** Python stdlib, pytest, and the existing frozen `uv` environment; no dependency changes.

**Spec:** `docs/superpowers/specs/2026-08-25-p1-u04-x4-receipt-finalization-design.md`

## Global Constraints

- Implementation base is exact commit `f31ecad47c7cf7de7d6d2c7c28037f61390bacb9` / tree `114b719669f3c93eca68e6b6f73a3eb8a390156a`.
- One implementation task, at most two implementation/review rounds for the packet.
- Do not run `--build-candidate-a`, `--build-candidate-b`, X4 re-preflight, or any native build command during implementation.
- Do not mutate external authority/output roots, active/rollback 1.227 authority, activation, promotion, X5/U05, broker/exchange state, or live trading.
- No dependency install, network/package fallback, ambient authority, skip, xfail, or weakened assertion.
- Portable tests use `TMPDIR=/tmp TEMP=/tmp TMP=/tmp` and must report zero skips/xfails.
- Preserve the pre-existing untracked `.superpowers/plans/` continuation evidence.
- Commit only the two production files and one test file named below.

---

### Task 1: Close the canonical receipt and sealed-result trust boundary

**Files:**
- Modify: `scripts/build_nautilus_engine.py:1331-1570,3436-3542,4573-4730`
- Modify: `scripts/materialize_nautilus_runtime_closure.py:1510-1760`
- Test: `tests/nautilus_upgrade/test_v1231_candidate_closure.py:648-920,1179-1435,3810-3973`

**Interfaces:**
- Consumes: canonical `p1-u04-x4-authority-preflight-v1`, sealed intermediate schema `p1-u04-candidate-build-result-v1`, `_candidate_build_policy_binding()`, and policy-derived `candidate_build_root`.
- Changes: `_load_candidate_build_result(roots: dict[str, Path], *, label: str) -> tuple[bytes, dict[str, object], dict[str, object], str]`; the final string is SHA-256 of the exact validated canonical `build-receipt.json` bytes.
- Produces: final reproducibility fields `build_a_receipt_sha256`, `build_b_receipt_sha256`, and `x4_authority_receipt_sha256` derived only from final validated A/B/X4 values.
- Reuses downstream: `builder._load_candidate_build_result(roots, label=...)`; convert builder validation failures to `RuntimeClosureMaterializationError` without implementing a second partial receipt validator.

- [ ] **Step 1: Correct the literal canonical X4 fixture and add receipt-schema RED tests**

In `_write_x4_authority_receipt()`, make these exact literal-fixture changes:

```python
receipt_document["checks"]["network_capability"] = (
    "DISABLED_BY_BUBBLEWRAP_UNSHARE_ALL"
)
del receipt_document["policy_sha256"]["toolchain_inputs"]
```

Place the network member in the literal `checks` mapping and omit the
toolchain member from the literal `policy_sha256` mapping; the statements above
show the exact before/after semantics and are not retained fixture code.

Add `synthetic_toolchain_policy` and `network_capability` fixture mutations.
The first adds `policy_sha256.toolchain_inputs`; the second replaces the
network literal with `AMBIENT`. Recompute canonical bytes and the outer digest
after mutation so each node reaches schema validation.

```python
@pytest.mark.parametrize(
    "mutation", ("synthetic_toolchain_policy", "network_capability")
)
def test_candidate_x4_receipt_rejects_noncanonical_policy_and_network_shape(
    x4_posix_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    receipt, digest, *_ = _write_x4_authority_receipt(
        x4_posix_tmp_path, monkeypatch, mutation=mutation
    )
    with pytest.raises(builder.VerificationError, match="X4 authority receipt"):
        builder._validate_x4_authority_receipt(receipt, digest, phase="A")
```

- [ ] **Step 2: Run the canonical receipt RED tests**

```bash
TMPDIR=/tmp TEMP=/tmp TMP=/tmp uv run pytest -q \
  tests/nautilus_upgrade/test_v1231_candidate_closure.py \
  -k 'canonical_x4_authority_receipt or noncanonical_policy_and_network_shape'
```

Expected: canonical acceptance fails with `X4 authority receipt object is
invalid` or `external authority drifted`; no candidate CLI is invoked.

- [ ] **Step 3: Implement the exact canonical X4 projection**

Keep `_candidate_policy_receipt()` restricted to five committed policy hashes:

```python
def _candidate_policy_receipt(inputs: dict[str, object]) -> dict[str, str]:
    hashes = inputs.get("policy_hashes")
    if not isinstance(hashes, dict):
        raise VerificationError("X4 authority receipt policy binding is invalid")
    mapping = {
        "cargo_registry": "cargo_registry_policy_sha256",
        "engine_build": "engine_build_policy_sha256",
        "input_cache": "input_cache_policy_sha256",
        "release_provenance": "release_provenance_policy_sha256",
        "wheel_cache": "wheel_cache_policy_sha256",
    }
    receipt = {name: hashes.get(source) for name, source in mapping.items()}
    if any(
        not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None
        for value in receipt.values()
    ):
        raise VerificationError("X4 authority receipt policy binding is invalid")
    return receipt  # type: ignore[return-value]
```

Add `network_capability` to the exact `checks` set and require:

```python
checks.get("network_capability") == "DISABLED_BY_BUBBLEWRAP_UNSHARE_ALL"
```

Continue comparing `checks.toolchain_inputs.sha256` with
`_sha256(_CANDIDATE_TOOLCHAIN_INPUTS)`; never copy it into `policy_sha256`.

- [ ] **Step 4: Run canonical receipt GREEN and existing receipt attacks**

```bash
TMPDIR=/tmp TEMP=/tmp TMP=/tmp uv run pytest -q \
  tests/nautilus_upgrade/test_v1231_candidate_closure.py \
  -k 'canonical_x4_authority_receipt or noncanonical_policy_and_network_shape or untrusted_x4_receipt'
```

Expected: all selected tests pass with zero skips/xfails.

- [ ] **Step 5: Add final-barrier RED tests for validated receipt bytes**

Reuse the arrangement from
`test_candidate_build_b_runs_one_build_and_publishes_build_a_final_artifact`.
Wrap the real loader and prove final evidence comes from its returned digest:

```python
real_load = builder._load_candidate_build_result
loads: list[tuple[str, str]] = []

def record_load(roots, *, label):
    loaded = real_load(roots, label=label)
    loads.append((label, loaded[3]))
    return loaded

monkeypatch.setattr(builder, "_load_candidate_build_result", record_load)
manifest = builder.build_candidate_b(
    authority_receipt=receipt,
    authority_receipt_sha256=receipt_sha256,
)
assert [label for label, _digest in loads[-2:]] == ["A", "B"]
assert manifest["reproducible_build"]["build_a_receipt_sha256"] == loads[-2][1]
assert manifest["reproducible_build"]["build_b_receipt_sha256"] == loads[-1][1]
```

Add `test_candidate_build_b_final_barrier_rejects_build_a_drift`. Drive the
fault from loader call count: immediately before the final A load, chmod the
sealed receipt to `0600`, replace `kind` with `P1_U04_BUILD_DRIFTED`, write
canonical ASCII JSON, and restore `0400`. Assert `VerificationError` and final
`artifacts` absent. Use only existing test fixture mutations; add no production
test hook.

- [ ] **Step 6: Run the final-barrier RED tests**

```bash
TMPDIR=/tmp TEMP=/tmp TMP=/tmp uv run pytest -q \
  tests/nautilus_upgrade/test_v1231_candidate_closure.py \
  -k 'final_receipt_digests_come_from_final_validated_loads or final_barrier_rejects_build_a_drift'
```

Expected: fail because the loader returns three values and current final
digests come from separate raw reads.

- [ ] **Step 7: Return the validated digest and make final reload authoritative**

At the end of `_load_candidate_build_result()`:

```python
receipt_sha256 = _sha256_bytes(receipt_raw)
return wheel_payload, artifact_core, receipt, receipt_sha256
```

Update every caller to unpack four values. In `build_candidate_b()`, retain
early validated A/B values for drift comparison. Make this the final barrier
immediately before `_publish_candidate_artifacts()`:

```python
final_x4 = _validate_x4_authority_receipt(
    authority_receipt, authority_receipt_sha256, phase="FINAL"
)
if final_x4 != validated_x4:
    raise VerificationError("validated X4 authority changed before final publication")

final_a = _load_candidate_build_result(roots, label="A")
final_b = _load_candidate_build_result(roots, label="B")
if final_a[:3] != confirmed_a[:3] or final_b[:3] != confirmed_b[:3]:
    raise VerificationError("candidate build result changed before final publication")

final_a_payload, final_a_core, final_a_receipt, final_a_digest = final_a
final_b_payload, final_b_core, final_b_receipt, final_b_digest = final_b
if final_a_payload != final_b_payload or final_a_core != final_b_core:
    raise VerificationError("candidate build result changed before final publication")
```

Construct reproducibility only after this block, using `final_a_digest`,
`final_b_digest`, `authority_receipt_sha256`, and identities from final A/B
receipts. Pass `final_a_payload` and `final_a_core` to publication. Delete both
raw receipt digest rereads; never read A/B again after this barrier.

- [ ] **Step 8: Run final-barrier GREEN and split-process regressions**

```bash
TMPDIR=/tmp TEMP=/tmp TMP=/tmp uv run pytest -q \
  tests/nautilus_upgrade/test_v1231_candidate_closure.py \
  -k 'candidate_build_a or candidate_build_b or final_receipt_digests or final_barrier or candidate_split_cli'
```

Expected: all selected tests pass; test doubles prove one native primitive call
per action, and no real candidate CLI runs.

- [ ] **Step 9: Add schema-7 RED tests for complete A/B records**

Extend `_write_candidate_artifact()` so each valid Build A/B directory contains
the exact wheel, `artifact-core.json`, and `build-receipt.json` used by the
production contract. Add:

```python
@pytest.mark.parametrize("missing", (WHEEL_FILENAME, "artifact-core.json"))
def test_candidate_artifact_validator_rejects_incomplete_sealed_build_record(
    tmp_path: Path, missing: str
) -> None:
    engine, inputs, roots, _document = _write_candidate_artifact(tmp_path)
    target = tmp_path / "build-b" / missing
    target.chmod(0o600)
    target.unlink()
    with pytest.raises(
        materializer.RuntimeClosureMaterializationError,
        match="candidate artifact reproducibility authority drifted",
    ):
        materializer._validate_candidate_artifact(builder, engine, inputs, roots)
```

Add `test_candidate_artifact_validator_rejects_minimal_build_receipt` by
replacing Build B receipt with canonical JSON containing only the current X4
digest while leaving the final manifest digest fields syntactically valid.
Assert the same fail-closed error. Parameterize extra file, wrong label,
receipt/wheel/core digest drift, reused process/source identity,
candidate/policy/authority drift, and final-core mismatch using existing
fixture mutation patterns.

- [ ] **Step 10: Run the schema-7 RED tests**

```bash
TMPDIR=/tmp TEMP=/tmp TMP=/tmp uv run pytest -q \
  tests/nautilus_upgrade/test_v1231_candidate_closure.py \
  -k 'incomplete_sealed_build_record or minimal_build_receipt or exact_build_and_x4_receipt_digests'
```

Expected: incomplete/minimal record nodes fail because downstream currently
reads only `build-receipt.json`.

- [ ] **Step 11: Replace downstream partial validation with the shared validator**

In `_validate_candidate_artifact()`, remove the receipt-only loop and call:

```python
try:
    build_a = builder._load_candidate_build_result(roots, label="A")
    build_b = builder._load_candidate_build_result(roots, label="B")
except (OSError, RuntimeError, TypeError, ValueError) as exc:
    raise RuntimeClosureMaterializationError(
        "candidate artifact reproducibility authority drifted"
    ) from exc

a_payload, a_core, a_receipt, a_digest = build_a
b_payload, b_core, b_receipt, b_digest = build_b
```

Require all of these exact bindings in one conditional:

```python
a_digest == receipt["build_a_receipt_sha256"]
b_digest == receipt["build_b_receipt_sha256"]
a_receipt["x4_authority_receipt_sha256"] == receipt["x4_authority_receipt_sha256"]
b_receipt["x4_authority_receipt_sha256"] == receipt["x4_authority_receipt_sha256"]
a_payload == b_payload == wheel.read_bytes()
a_core == b_core
a_receipt["process_identity"] == process_identities[0]
b_receipt["process_identity"] == process_identities[1]
a_receipt["source_identity"] == identities[0]
b_receipt["source_identity"] == identities[1]
a_receipt["candidate"] == b_receipt["candidate"]
a_receipt["policy_sha256"] == b_receipt["policy_sha256"]
a_receipt["authority_identities"] == b_receipt["authority_identities"]
a_receipt["sanitized_environment_sha256"] == b_receipt["sanitized_environment_sha256"]
```

Cross-bind `a_core` with the final artifact manifest using its exact published
projection:

```python
final_core = {
    key: value for key, value in document.items() if key != "reproducible_build"
}
a_core == b_core == final_core
```

Preserve the materializer error text and fail closed on builder validation
exceptions.

- [ ] **Step 12: Run schema-7 GREEN and focused trust-boundary regression**

```bash
TMPDIR=/tmp TEMP=/tmp TMP=/tmp uv run pytest -q \
  tests/nautilus_upgrade/test_v1231_candidate_closure.py \
  -k 'canonical_x4 or untrusted_x4_receipt or candidate_build_a or candidate_build_b or candidate_artifact_validator'
```

Expected: all selected tests pass; zero skips/xfails.

- [ ] **Step 13: Run the exact portable file gate**

```bash
TMPDIR=/tmp TEMP=/tmp TMP=/tmp uv run pytest -q \
  tests/nautilus_upgrade/test_v1231_candidate_closure.py
```

Expected: every collected test passes with zero skips/xfails. Retain `/tmp` for
POSIX mode tests; never weaken their assertions for a DrvFS inherited temp.

- [ ] **Step 14: Verify scope and forbidden-output absence**

```bash
git diff --check
git status --short
git diff --name-only f31ecad47c7cf7de7d6d2c7c28037f61390bacb9 -- \
  scripts/build_nautilus_engine.py \
  scripts/materialize_nautilus_runtime_closure.py \
  tests/nautilus_upgrade/test_v1231_candidate_closure.py
find /home/thenam176/build/nautilus-p1-u04 -mindepth 1 -maxdepth 2 -print
```

Expected: diff check clean; only three task files changed; candidate build
parent has no child; candidate artifact/runtime/forensic roots remain absent.
Do not delete or alter an unexpected output—stop and report it.

- [ ] **Step 15: Commit the implementation round**

```bash
git add \
  scripts/build_nautilus_engine.py \
  scripts/materialize_nautilus_runtime_closure.py \
  tests/nautilus_upgrade/test_v1231_candidate_closure.py
git commit -m "fix(p1-u04): finalize X4 receipt bindings"
```

Record exact commit/tree, RED/GREEN commands and counts, output-root absence,
and that no native/candidate build ran. Generate the review package from fixed
base `f31ecad47c7cf7de7d6d2c7c28037f61390bacb9`, never `HEAD~1`.

- [ ] **Step 16: Fresh review gate**

Dispatch one fresh spec/code reviewer and one fresh security/replay reviewer on
the exact base-to-candidate range. Both inspect canonical receipt compatibility,
full sealed A/B validation, final-barrier ordering, downstream cross-binding,
fail-closed paths, and fault-injection quality. Any Critical/Important resumes
the same implementer for round 2/2. Any Critical/Important after round 2 stops
with `P1_U04_ARCHITECTURE_ESCALATION_REQUIRED`.

Only clean reviews authorize X4 re-preflight. They do not authorize native
Build A/B or X5.
