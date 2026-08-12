# Portable source-authority semantics amendment

## Decision and classification

`BLOCKER_CLASSIFICATION: PORTABLE_AUDIT_SEMANTICS_DEFECT`

This is **not** an isolated assembly tamper. It is **not** permission to rewrite an immutable import manifest, and it is **not** permission to revert reviewed safety changes. The defect is confined to portable-mode audit semantics: it checks the immutable import snapshot correctly, then incorrectly checks current `HEAD` against that same historical snapshot. Strict mode does not perform the second check, and intentionally accepts committed component evolution.

The selected repair is **A: introduction-only portable verification**. Portable mode substitutes embedded authority only for unavailable external source authority at the import boundary. It must retain all strict-mode semantics for later canonical-repository history.

## Source map and independently verified evidence

| Owner | Current responsibility | Evidence |
| --- | --- | --- |
| `scripts/audit_canonical_repo.py` | Chooses strict versus portable authority and determines each component introduction. In `audit()` the component loop currently sets `revisions = (introduction, head) if portable else (introduction,)`, then calls `verify_embedded_snapshot` or `verify_snapshot`. | Current lines 398-462; this is the defect location. |
| `scripts/verify_component_snapshot.py` | Validates manifest structure/aggregate, binds each manifest to source authority, and verifies a named Git revision. `verify_snapshot()` additionally regenerates the manifest from external authority; `verify_embedded_snapshot()` uses only embedded authority. | Current lines 280-311. No change is required. |
| `ops/consolidation/source-authority.json` | Sealed component identity: backend external commit `59578f984b72d5d03583a2c06b15a53a224b31c8`; dashboard external commit `84627f16e9753b1104d661697720b93897f27d27`. | Current authority document. |
| `ops/consolidation/backend-source-manifest.json` and `ops/consolidation/dashboard-source-manifest.json` | Immutable original-import manifests. Each is verified by `_immutable_evidence()` against its single add commit, `HEAD`, and the working tree. | Both manifests have the sole history record `302a5ebb79ef3319432ea7d0dd5f27f0d1bf14cd`; backend manifest blob is unchanged (`ca908f0fe28637265dc0c6f8a18cfd4ac625bbec`) from that commit to `HEAD`. |
| `tests/consolidation/test_audit_canonical_repo.py` | Isolated Git fixture and portable/strict authority tests. `_valid_root()` commits base, backend import, and dashboard import; `_remove_authority_repositories()` creates portable availability. | Current focused suite: 75 passing tests. |
| `tests/consolidation/test_repository_shape.py` | CI and Makefile topology assertions. It already requires Foundation checkout `fetch-depth: 0` and `make ci-portable`. | Current focused suite: 12 passing tests. |

The backend manifest entry for `legacy/research-backend/assembly.py` contains Git blob `9921d8c755e93e477c8c0e6b2f0709ebc4c16137`. The current `HEAD` blob is `5937cd913611e5e40a31fcd1fa5a79c1ce053288`. The latter was introduced by `b42607cb8c21d7a7b5ffeb854f08d62e9d15ff2f` (`fix(foundation): make failure modes explicit`), whose parent is `7689ffdf6bab72f31e61d7695ea601d7bea5faaf`. No Phase 1 autopilot commit changes `assembly.py` (`b42607c..HEAD` has no path diff). Therefore the mismatch is reviewed later evolution, not evidence of an unreviewed assembly-only alteration.

The immutable authority/manifests predate the component imports: authority evidence was added in `302a5ebb79ef3319432ea7d0dd5f27f0d1bf14cd`; the backend and dashboard imports are respectively `10f4d340f8a00a359a9298e2382c69f730655f54` and `e567bdc29adf34ee121fd757ce84e2b31dc80042`. `_introduction()` requires one sentinel add, resolves its parent, and proves that the component prefix is absent from that parent. Missing history/object resolution fails closed as `E_GIT_OBJECT`.

### Complete post-import component-evolution inventory

Backend commits after `10f4d340` (eight):

1. `b42607cb` — `fix(foundation): make failure modes explicit` (16 backend paths, including `assembly.py`).
2. `ee6aacf5` — `refactor(research): decompose Track B pipeline coordinator` (2 backend paths).
3. `1a7c54b6` — `fix(trading): complete Track B P9 remediation` (4 backend paths).
4. `18dbdbc8` — `feat: complete canonical market data authority packets` (2 backend paths; also 4 dashboard paths).
5. `5185547b` — `feat: complete paper-only remediation phases 0-4` (1 backend path; also 9 dashboard paths).
6. `9722d838` — `feat: close Nautilus research campaign evidence` (2 backend paths).
7. `fede4bce` — `fix: harden Nautilus research campaign evidence` (2 backend paths).
8. `602a2002` — `fix: retain Nautilus research forensics` (2 backend paths).

Dashboard commits after `e567bdc2` (nine):

1. `270733a9` — `test: complete Package 3 evidence governance` (6 dashboard paths).
2. `b42607cb` — `fix(foundation): make failure modes explicit` (1 dashboard path; also backend evolution above).
3. `18dbdbc8` — `feat: complete canonical market data authority packets` (4 dashboard paths).
4. `5185547b` — `feat: complete paper-only remediation phases 0-4` (9 dashboard paths).
5. `d677b9c6` — `fix: close paper release projection checks` (1 dashboard path).
6. `a63b3e74` — `Fix engine authority consumer contracts` (2 dashboard paths).
7. `9ffb88a5` — `Preserve microsecond backtest windows` (2 dashboard paths).
8. `b179c3f4` — `fix(dashboard): pin safe nanoid release` (2 dashboard paths).
9. `94eaa007` — `fix(dashboard): pin safe js-yaml release` (2 dashboard paths).

This inventory establishes that the first observed assembly mismatch cannot be treated as the only possible post-import difference.

## Current versus required semantics

| Mode | Authority source | Snapshot revision that must match original manifest | Later committed component evolution |
| --- | --- | --- | --- |
| Strict, current and required | All external authorities available | Exact introduction | Allowed |
| Portable, current | All external authorities absent, `--portable` explicit | Introduction **and HEAD** | Incorrectly rejected |
| Portable, required | All external authorities absent, `--portable` explicit | Exact introduction only | Allowed |

Strict behavior is demonstrated by the existing `test_strict_mode_seals_introduction_but_allows_later_component_evolution`. The current `test_portable_audit_still_rejects_component_byte_tamper` instead commits a changed backend file after the valid import and expects `E_TAMPER`; that assertion encodes the divergent, defective portable behavior and must be replaced, not retained as a safety invariant.

## Alternatives considered

### A. Introduction-only portable verification — selected

Portable retains the authoritative, immutable component-import boundary: embedded authority must agree with the manifest; the manifest aggregate and identity must parse; the exact introduction must match all manifest blobs; the component must be absent from the introduction parent; and Git objects must exist. It then accepts later commits in the canonical repository just as strict mode already does. This is the smallest source-backed repair and does not create a new authority system.

### B. Append-only post-import evolution receipts — rejected

Receipts would need a receipt for every later component transition, including the eight backend and nine dashboard commits enumerated above (with shared commits reconciled carefully). That is retroactive authority reconstruction and creates a second code-approval system beside normal reviewed Git history. The present strict semantics demonstrate that receipts are unnecessary to preserve the original-import provenance boundary. No source evidence requires this wider mechanism.

### C. Revert components to their original imports — rejected

This would discard reviewed safety, research, dependency, and dashboard evolution merely to satisfy a portable-only false condition. The history identifies `b42607c` as reviewed evolution and confirms additional changes. No source evidence establishes that the later commits are accidental; reversal is outside the amendment's scope.

## Frozen implementation design

### Production change

Change only `scripts/audit_canonical_repo.py`, inside `audit()`'s existing per-component loop (current lines 432-444). Retain `_introduction()` and both verifier APIs unchanged. Compute/iterate only the component's `introduction` revision in both modes, then preserve the current mode-specific verifier choice:

```text
strict:   verify_snapshot(authority_path, manifest_path, root, introduction)
portable: verify_embedded_snapshot(parsed_authority, manifest_path, root, introduction)
```

Do not pass `HEAD` to either original-import manifest verifier. This change does not read a mutable working tree as immutable evidence and does not add a fallback: `_audit_authority()` remains the sole selector, requiring all authorities for strict, none plus explicit `--portable` for portable, and rejecting partial availability in either invocation. `_immutable_evidence()` continues to run after component checks for `source-authority.json` and both original manifests.

The production budget is one hand-written production file and a small loop change (well below four files and 500 production lines). The expected implementation test change is one focused test file. No CI topology edit is anticipated: `tests/consolidation/test_repository_shape.py` changes only if a new assertion is strictly required, which current source evidence does not indicate.

### Frozen test changes

Edit only `tests/consolidation/test_audit_canonical_repo.py` for behavioral coverage. Reuse `_valid_root`, `_remove_authority_repositories`, `_git`, and `_run`; do not invent a live or external fixture.

1. Add `test_portable_mode_seals_introduction_but_allows_later_backend_component_evolution`. Build `_valid_root(tmp_path)`, remove **all** authority repositories, change `legacy/research-backend/main.py`, commit that file as legitimate post-import evolution, then run `_run(repository, "--portable", "--json")`. Assert return code zero, `authority_mode == "portable"`, `head` is the new commit, and backend result is `PASS`.
2. Add `test_portable_mode_seals_introduction_but_allows_later_dashboard_component_evolution`. Use the same valid fixture and availability removal, change and commit `apps/dashboard/src/app.ts`, run portable JSON audit, and assert zero, `authority_mode == "portable"`, new `head`, and dashboard `PASS`.
3. Replace the existing post-import-byte-tamper expectation with `test_portable_audit_rejects_tampered_backend_introduction_snapshot`. Build `_valid_root(tmp_path, backend_tamper="modified")`, which modifies `main.py` before the backend import commit, remove all authorities, run portable audit, and assert nonzero plus exactly `E_TAMPER: legacy/research-backend/main.py`. This proves that portable still seals the immutable introduction, rather than merely allowing `HEAD`.
4. Add `test_portable_audit_rejects_shallow_history_with_git_object_error`. Build the valid fixture, remove all authorities, create a local depth-one clone at a temporary fixture path (no network), and run portable audit on that clone. Assert nonzero and exactly `E_GIT_OBJECT`. The current code reports this generic Git-object failure when the shallow log cannot establish the introduction; the shallow boundary must not be treated as a valid introduction or skipped.
5. Retain and rerun the existing strict-evolution test unchanged. Retain and rerun `test_audit_requires_explicit_portable_mode_when_all_authorities_are_absent`, `test_portable_flag_rejects_fully_available_authorities`, both partial-authority tests, manifest identity/aggregate/coordinated authority-manifest tamper tests, release-plus-portable rejection, and the final isolated tamper/immutable-evidence checks. Their fixture mutation semantics remain valid because they attack authority/manifests or the introduction rather than approved later component commits.

### Invariants explicitly preserved

- Exactly one component introduction remains mandatory; its parent must lack the component prefix.
- Missing/invalid introduction Git history remains `E_GIT_OBJECT`; malformed manifest or component content still fails closed.
- `source-authority.json`, backend manifest, and dashboard manifest remain immutable and verified against introduction, `HEAD`, and working content.
- Manifest identity, aggregate integrity, component-authority bindings, tracked-path checks, source-marker scan, forbidden-file checks, repository root checks, status/head/branch reporting, and release cleanliness remain unchanged.
- Partial authority availability remains `E_AUTHORITY`; fully available authority plus `--portable` remains `E_AUTHORITY`; `--release --portable` remains `E_ARGUMENT`; strict never silently falls back to portable.
- Foundation remains full-history (`fetch-depth: 0`) and invokes `make ci-portable`, whose portable aggregate includes audit, contracts, secrets, root/backend/dashboard tests, dashboard typecheck/lint, coverage, build, source audit, and dependency audit.

### Explicitly forbidden scope

Do not change component sources (including `legacy/research-backend/assembly.py` or any `apps/dashboard/**` source), `ops/consolidation/source-authority.json`, either source manifest, lockfiles, generated contracts/artifacts, the Foundation workflow/Makefile unless topology evidence makes it indispensable, runtime/protected paths, production configuration, or any live flags. Do not rewrite history, contact external authorities, start services, run migrations, or enable live execution/trading.

## Validation and expected authority-state behavior

Focused implementation cycle:

```bash
uv run pytest -q tests/consolidation/test_audit_canonical_repo.py
uv run pytest -q tests/consolidation/test_repository_shape.py
```

Packet regression and gates after the focused tests are green:

```bash
make test-consolidation
make audit-portable
make check-contracts
make check-secrets
git diff --check
```

Run `make audit` only when all strict external authorities are present in the isolated environment; absence there is not a strict-source regression. The full CI closure is `make ci-portable` and hosted Foundation with full checkout history. Expected outcomes are: strict + all authorities uses `strict` and allows later committed evolution; all authorities absent + explicit portable uses `portable` and allows later committed evolution; no portable flag or partial/full-incompatible availability fails `E_AUTHORITY`; shallow history fails `E_GIT_OBJECT`; release+portable fails `E_ARGUMENT`.

## Self-review

No placeholders, alternate interpretation, or authority rewrite is left open. The design changes one production loop only, names exact existing and new tests/fixtures/assertions, preserves all authority and release constraints, and fits the stated file/line budget. It is ready for a fresh independent design review; implementation remains prohibited until that review reports `SPEC PASS`, `QUALITY PASS`, Critical 0, Important 0.
