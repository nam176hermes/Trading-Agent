# Package D — Hermetic Release Tooling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Copy boundary:** This file is standalone. Execute only Package D; it qualifies tooling but does not build the final staging candidate.

**Goal:** Close CPython/wheel/build inputs offline, build sealed app/backend/migrator tooling, and prove reproducibility, tamper rejection, promotion, provisioning, and systemd verification behavior in fixtures.

**Architecture:** Commit acquisition tooling before network access, use pinned standalone CPython and a hash-addressed wheel CAS, compose candidates as `NO_GO`, and qualify copy-on-promote plus maintenance-only provisioning in private roots.

**Tech Stack:** Python 3.11.15 standalone, uv lockfiles, wheels, pytest, JSON manifests, systemd-analyze, Git archives.

## Global Constraints

- This file is non-authorizing until the user grants exact `SOURCE_RELEASE_IMPLEMENTATION` scope.
- Network acquisition additionally requires a separate reviewed build-input record bound to committed tooling bytes.
- Keep `paper/paper`, false/false live gates, and kill-switch semantics unchanged.
- Do not access runtime PostgreSQL, `/opt`, systemd installation, daemon reload, services, jobs, or providers.
- Final build/runtime must not depend on host `uv`, global Python, editable installs, mutable worktrees, or service-start network access.
- New candidates default to `NO_GO / NOT_INSTALLED / NOT_RUNNING`.
- No manifest digest may be null; digest relationships must remain acyclic.
- Do not execute SNAPSHOT; backend checks are import/argv/environment/fixture-validator only.
- Stop on unresolved native dependencies/licenses, network-isolation failure, logical build drift, tamper false-negative, verifier-pin mismatch, or systemd warning.

## Package Authority and Exit Gate

- **Entry:** reviewed Package C component-source policies and exact Package B database authority.
- **Produces:** sealed input closure, release builders/verifiers, promotion protocol, maintenance subset verification, and exact unit verification tooling.
- **Exit:** offline closure passes; independent fixture builds agree; all tamper cases reject; systemd is warning-free; no runtime path changed.
- **Next:** Package E is source-only runbook/authority-contract work and needs a separate instruction.

---

### Task 9: Split and seal hermetic Python and wheel inputs

**Files:**
- Create: `docs/adr/ADR-job-plane-hermetic-release-layout.md`
- Create: `ops/release-v2/inputs/python-3.11.15.json`
- Create: `ops/release-v2/inputs/app-wheel-selection.json`
- Create: `ops/release-v2/inputs/backend-wheel-selection.json`
- Create: `ops/release-v2/inputs/migrator-wheel-selection.json`
- Create: `ops/release-v2/inputs/build-wheel-selection.json`
- Create: `ops/release-v2/inputs/license-policy.json`
- Create: `ops/release-v2/inputs/host-abi-linux-x86_64.json`
- Create: `schemas/release-build-input-approval.schema.json`
- Create: `ops/release-v2/build-input-approval.example.json`
- Create: `scripts/validate_release_build_input_approval.py`
- Create: `packages/runtime_release/hermetic_inputs.py`
- Create: `packages/runtime_release/hermetic_python.py`
- Create: `packages/runtime_release/wheelhouse.py`
- Create: `ops/release-v2/acquire-inputs.py`
- Create: `ops/release-v2/bootstrap-build-driver.py`
- Create: `ops/release-v2/run-offline-build.py`
- Create from reviewed deterministic evidence: `docs/implementation/job-plane-hermetic-python-design.md`
- Create from reviewed deterministic evidence: `docs/implementation/job-plane-wheelhouse-closure.md`
- Test: `tests/runtime_release/test_hermetic_inputs.py`
- Test: `tests/runtime_release/test_hermetic_python.py`
- Test: `tests/runtime_release/test_wheelhouse.py`
- Test: `tests/runtime_release/test_build_input_approval.py`
- Test: `tests/runtime_release/test_offline_build_driver.py`

**Interfaces:**
- CPython input: version 3.11.15, tag `20260623`, asset `cpython-3.11.15+20260623-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz`, SHA-256 `0604cd029b142dc223e131f17f5941c0c8d2d5074997c8178b515b19eea2a6c2`.
- External input root: `/home/thenam176/.local/share/trading-agent/release-inputs/v2`.
- The input root contains a sealed bootstrap interpreter at `python/runtime/bin/python3.11`; its file SHA-256 must equal the approved CPython-derived runtime manifest before it may validate or bootstrap anything.
- Application, backend, and maintenance-only migrator have separate wheel selections and component roots.
- The external build-input record binds exact scope, expiry, source commit/tree, approved immutable URLs/domains, artifact names/hashes/sizes, output/CAS root, network operation IDs, distinct reviewers, and canonical digest. It contains no credential and authorizes neither runtime nor service actions.
- `bootstrap-build-driver.py` materializes a separate pinned-CPython build driver from the local build-wheel closure. Final builds invoke no host `uv`, host Python, global site-packages, or working-tree script.
- `run-offline-build.py` requires a no-network namespace/sandbox, sets offline package-manager controls, executes the commit-exported builder under the pinned driver, and stops if network isolation is unavailable.

- [ ] **Step 1: TDD strict input records**

Reject latest URLs, hash/size drift, sdists, ambiguous wheels, unexpected domains, external/dangling links, special files, mutable inputs, unreviewed licenses, unresolved native DSOs, and every missing/expired/wrong-commit build-input approval. The CLI and library validator must make the same decision. Implement acquisition, safe standalone-Python materialization, wheel verification, build-driver bootstrap, and offline wrapper behind these tests, but perform no network acquisition yet.

- [ ] **Step 2: Verify and commit the exact acquisition/tooling authority before requesting network approval**

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest -q \
  tests/runtime_release/test_hermetic_inputs.py \
  tests/runtime_release/test_hermetic_python.py \
  tests/runtime_release/test_wheelhouse.py \
  tests/runtime_release/test_build_input_approval.py \
  tests/runtime_release/test_offline_build_driver.py
git add docs/adr/ADR-job-plane-hermetic-release-layout.md \
  ops/release-v2/inputs/python-3.11.15.json \
  ops/release-v2/inputs/app-wheel-selection.json \
  ops/release-v2/inputs/backend-wheel-selection.json \
  ops/release-v2/inputs/migrator-wheel-selection.json \
  ops/release-v2/inputs/build-wheel-selection.json \
  ops/release-v2/inputs/license-policy.json \
  ops/release-v2/inputs/host-abi-linux-x86_64.json \
  schemas/release-build-input-approval.schema.json \
  ops/release-v2/build-input-approval.example.json \
  scripts/validate_release_build_input_approval.py \
  packages/runtime_release/hermetic_inputs.py \
  packages/runtime_release/hermetic_python.py \
  packages/runtime_release/wheelhouse.py \
  ops/release-v2/acquire-inputs.py \
  ops/release-v2/bootstrap-build-driver.py \
  ops/release-v2/run-offline-build.py \
  tests/runtime_release/test_hermetic_inputs.py \
  tests/runtime_release/test_hermetic_python.py \
  tests/runtime_release/test_wheelhouse.py \
  tests/runtime_release/test_build_input_approval.py \
  tests/runtime_release/test_offline_build_driver.py
git commit -m "release: define hermetic build input authority"
```

The commit above is the acquisition-tooling identity. No external record may authorize uncommitted acquisition code, and any later tooling/policy edit invalidates the pending record.

- [ ] **Step 3: Acquire inputs in a separately approved network-enabled build step**

Verify archive identity before extraction. Store wheels in a SHA-addressed external CAS, never Git or the final component. Root and backend `uv.lock` remain version authority; selection files are reviewed projections.

```bash
INPUT_ROOT=/home/thenam176/.local/share/trading-agent/release-inputs/v2
INPUT_TOOLING_COMMIT="$(git rev-parse HEAD)"
INPUT_TOOLING_TREE="$(git rev-parse HEAD^{tree})"
python3 scripts/validate_release_build_input_approval.py \
  --record "$RELEASE_BUILD_INPUT_APPROVAL_RECORD" \
  --expected-commit "$INPUT_TOOLING_COMMIT" \
  --expected-tree "$INPUT_TOOLING_TREE" \
  --expected-input-root "$INPUT_ROOT"
uv run --frozen python ops/release-v2/acquire-inputs.py fetch \
  --records-dir ops/release-v2/inputs \
  --input-root "$INPUT_ROOT" \
  --approval-record "$RELEASE_BUILD_INPUT_APPROVAL_RECORD" \
  --network-approved
uv run --frozen python ops/release-v2/acquire-inputs.py verify \
  --records-dir ops/release-v2/inputs \
  --input-root "$INPUT_ROOT" --offline
```

`--network-approved` is a technical acknowledgement and is accepted only when the separately reviewed build-input approval record also validates; it is never sufficient by itself.

- [ ] **Step 4: Materialize and seal the bootstrap standalone Python at the declared path**

Use the already committed, hash-validated materializer to extract the approved archive to the previously absent `$INPUT_ROOT/python/runtime/`, with final interpreter `$INPUT_ROOT/python/runtime/bin/python3.11`. Produce external `python-runtime-manifest.json` and `input-manifest.json`, fsync, remove write bits, then re-open and hash every path. Do not use a host-based venv. Reject UV/home/worktree/global-site references in shebangs, `.pth`, `pyvenv.cfg`, sysconfig, RPATH/RUNPATH, or text metadata. Prove relocation in two unrelated private roots before sealing the canonical input root.

- [ ] **Step 5: Close wheels, ABI, licenses, and the build driver**

Install only from local wheels using `--no-index`, `--find-links`, `--require-hashes`, and `--no-deps`. At this stage, build fixture/local component wheels twice with fixed `SOURCE_DATE_EPOCH` to qualify the tooling; source-derived release wheels are deliberately absent from the external CAS and are rebuilt twice from the final Task 14 archive in Task 15. Use static `readelf`, not `ldd`, for native closure. Remove pip/build tools/editable metadata after installation.

Materialize two independent copies of the build driver from the same pinned CPython/build-wheel inputs and require equal logical runtime manifests. Unit tests must prove `UV_OFFLINE=1`, `PIP_NO_INDEX=1`, isolated HOME/cache, no inherited Python/loader variables, network namespace enforcement, and fail-closed behavior when any control is unavailable.

- [ ] **Step 6: Reverify the sealed external closure and commit only sanitized deterministic evidence**

```bash
INPUT_ROOT=/home/thenam176/.local/share/trading-agent/release-inputs/v2
INPUT_TOOLING_COMMIT="$(python3 scripts/validate_release_build_input_approval.py \
  --record "$RELEASE_BUILD_INPUT_APPROVAL_RECORD" --print-field source_commit)"
INPUT_TOOLING_SCRATCH="$(mktemp -d /tmp/job-plane-input-tooling.XXXXXX)"
INPUT_TOOLING_EXPORT="$INPUT_TOOLING_SCRATCH/source"
git worktree add --detach "$INPUT_TOOLING_EXPORT" "$INPUT_TOOLING_COMMIT"
"$INPUT_ROOT/python/runtime/bin/python3.11" -I \
  "$INPUT_TOOLING_EXPORT/ops/release-v2/acquire-inputs.py" verify \
  --records-dir "$INPUT_TOOLING_EXPORT/ops/release-v2/inputs" \
  --input-root "$INPUT_ROOT" --offline --require-sealed
git add \
  docs/implementation/job-plane-hermetic-python-design.md \
  docs/implementation/job-plane-wheelhouse-closure.md
git commit -m "docs(release): record hermetic input closure"
git worktree remove "$INPUT_TOOLING_EXPORT"
```

`INPUT_TOOLING_EXPORT` is a safe materialization of `INPUT_TOOLING_COMMIT`, not the later mutable worktree. Evidence records its commit/tree, CPython artifact/interpreter hashes, input-manifest digest, wheel CAS digest, licenses, native ABI closure, and acquisition-record digest. Final source authority later binds these immutable values plus the unchanged policy/tool blobs.

**Stop condition:** unresolved Torch/CUDA/RDMA/MPI/UCX dependency or incomplete license/SBOM evidence. Reducing the backend dependency graph is a separate behavior-change phase and must not be hidden inside release closure.

---

### Task 10: Build sealed service/backend components and a migrator kit

**Files:**
- Create: `packages/runtime_release/release_manifests.py`
- Create: `ops/release-v2/build-component.py`
- Create: `ops/release-v2/build-candidate.py`
- Create: `ops/release-v2/compose-candidate.py`
- Modify: `packages/runtime_release/v2.py`
- Modify: `ops/release-v2/verify-stage.py`
- Modify: `docs/production/release-authority-v2.md`
- Create from reviewed deterministic evidence: `docs/implementation/job-plane-release-v2-hermetic-candidate.md`
- Test: `tests/runtime_release/test_v2.py`
- Test: `tests/runtime_release/test_v2_provisioning.py`
- Test: `tests/runtime_release/test_build_candidate.py`

**Interfaces:**
- Builders require explicit source archive, source commit/tree, component output, and authority output; tests use private temporary roots.
- Final staging path naming is applied only in Task 15 after the source commit is frozen.
- Dashboard and Node are excluded from this Job Plane release.
- Component/manifests never serialize physical build roots. They bind relative component paths plus the same commit-qualified logical `/opt/trading-agent-v2/releases/${COMMIT}` prefix; the standalone verifier receives an explicit physical-to-logical root mapping.
- `build-component.py build --component {app,backend,migrator} --source-archive PATH --source-commit SHA --source-tree TREE --input-root PATH --output PATH` safely materializes only the validated archive and refuses a checkout path or existing output.
- `build-candidate.py --source-archive PATH --source-commit SHA --source-tree TREE --input-root PATH --build-driver PATH --output-root PATH --logical-prefix PATH` orchestrates one fully offline fresh candidate and refuses a working-tree source or existing output.
- `compose-candidate.py compose --app-root PATH --backend-root PATH --migrator-root PATH --unit-root PATH --unit-manifest PATH --authority-output PATH --source-commit SHA` recomputes the exact unit file set/hashes/modes before creating the exact eight-file authority root and refuses existing, writable, or unsealed input.
- `verify-stage.py --authority-root PATH --app-root PATH --backend-root PATH --migrator-root PATH --unit-root PATH --expected-commit SHA --expected-aggregate-sha256 SHA256` is standalone, read-only, and returns nonzero on any undeclared byte/path.
- The migrator contains `maintenance-authority-manifest.json`, a canonical subset manifest listing exact destination-relative migrator, runbook, launcher, and validator files/hashes/modes plus source commit. It contains neither authority-file hashes nor the aggregate digest, avoiding a cycle. `release-manifest.json` binds its SHA-256. `verify-stage.py --maintenance-only --maintenance-root PATH --expected-commit SHA --expected-aggregate-sha256 SHA256 --expected-maintenance-manifest-sha256 SHA256` first verifies the copied eight-file authority DAG against the independent aggregate SHA, then reads the release-manifest-bound subset digest and verifies only the maintenance files. It never claims absent app/backend/unit roots were checked.

- [ ] **Step 1: Write manifest-shape RED tests**

Authority root must contain exactly `release-manifest.json`, `python-runtime-manifest.json`, `wheelhouse-manifest.json`, `installed-packages-manifest.json`, `command-manifest.json`, `semantic-input-manifest.json`, `aggregate-manifest.json`, and `promotion-record.json`. Reject null digests, unknown keys, extra files, mutable paths, and circular digest dependencies.

Define the acyclic digest DAG explicitly: the first six manifests plus `promotion-record.json` are seven canonical-JSON leaves and may not reference `aggregate-manifest.json`; `release-manifest.json` binds component/file manifests, source policies, unit-bundle digest, and verifier identity, while promotion binds only candidate state/source identity. `aggregate-manifest.json` contains a fixed-order mapping of the seven leaf names to SHA-256 plus the aggregate logical digest, but never its own file hash. Protected external final-release evidence supplies the independent expected SHA-256 of canonical `aggregate-manifest.json`; standalone verification requires that expected value.

- [ ] **Step 2: Exercise application, backend, and migrator builders independently**

Builders materialize source only through Task 8's exact app/backend/migrator policies; they never use the root wheel's broad package discovery as release scope. In disposable fixture roots, application smoke imports only `apps.job_api.main` and `services.job_worker.main`. Backend verification is limited to import smoke, exact SNAPSHOT argv construction, empty/research-only child-environment validation, and fixture result-validator tests; it never invokes SNAPSHOT or any research/provider code. The maintenance-only migrator kit contains its own hermetic Python closure, Alembic/config, the complete exact migration graph through 0007, `ops/postgres/provision-job-roles.sql`, database/release authority verifiers, exact V2 recovery and migration runbooks, their fixed launchers/approval validators, and the bound maintenance-subset manifest. Offline smoke loads the revision graph and verifies every migration/tool/runbook/subset hash without connecting to a database. All three components are read-only and contain no `.git`, database, log, report, secret, UV path, or source checkout reference. Before composition, fsync every task-owned component/unit file and directory, remove write bits, re-open/re-hash by stable descriptor, and reject any path/inode/mode change. These are tooling tests, not the final candidate build.

- [ ] **Step 3: Compose candidate authority**

Aggregate authority binds final root commit, reviewed backend subtree, component roots, CPython archive/interpreter identities, locks/wheels, exact SNAPSHOT cwd/interpreter/argv/timeout/validator, semantic hashes, verifier, 0007 database ancestry, normalized API/worker unit-template policy, and the exact external `systemd-bundle-manifest.json` digest. Rendered path-specific units are not placed inside the eight-file authority root.

- [ ] **Step 4: Make every newly composed candidate fail closed by default**

```text
NO_GO
NOT_INSTALLED
NOT_RUNNING
```

The promotion state is a strict enum: `NO_GO`, `CANDIDATE_VERIFIED`, or future `INSTALLED` (not used in this plan). Composition can emit only `NO_GO`; it cannot accept a caller-supplied override. `CANDIDATE_VERIFIED` is created only by Task 15's copy-on-promote gate after all reproducibility, tamper, systemd, and independent-review evidence exists. No activation or runtime database health claim belongs in the static build.

- [ ] **Step 5: Run focused tests and commit the builder**

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest -q \
  tests/runtime_release/test_v2.py \
  tests/runtime_release/test_v2_provisioning.py \
  tests/runtime_release/test_build_candidate.py
git add packages/runtime_release/release_manifests.py \
  packages/runtime_release/v2.py \
  ops/release-v2/build-component.py \
  ops/release-v2/build-candidate.py \
  ops/release-v2/compose-candidate.py \
  ops/release-v2/verify-stage.py \
  docs/production/release-authority-v2.md \
  docs/implementation/job-plane-release-v2-hermetic-candidate.md \
  tests/runtime_release/test_v2.py \
  tests/runtime_release/test_v2_provisioning.py \
  tests/runtime_release/test_build_candidate.py
git commit -m "release: compose sealed Release Authority v2 candidates"
```

---

### Task 11: Implement reproducibility, tamper detection, and exact systemd verification

**Files:**
- Create: `ops/release-v2/compare-builds.py`
- Create: `ops/release-v2/tamper-candidate.py`
- Create: `ops/release-v2/promote-candidate.py`
- Create: `packages/runtime_release/source_authority.py`
- Create: `schemas/final-source-authority.schema.json`
- Create: `schemas/release-candidate-promotion.schema.json`
- Create: `scripts/validate_final_source_authority.py`
- Create: `scripts/materialize_release_source.py`
- Create: `tests/runtime_release/test_release_reproducibility.py`
- Create: `tests/runtime_release/test_release_tamper.py`
- Create: `tests/runtime_release/test_release_promotion.py`
- Create: `tests/runtime_release/test_systemd_staging.py`
- Create: `tests/runtime_release/test_source_authority.py`
- Modify: unit rendering in `packages/runtime_release/v2.py`
- Modify: `ops/release-v2/provision-root.sh`
- Create from reviewed deterministic evidence: `docs/implementation/job-plane-reproducibility-evidence.md`
- Create from reviewed deterministic evidence: `docs/implementation/job-plane-tamper-evidence.md`
- Update: `docs/implementation/job-plane-systemd-staging-verification.md`

**Interfaces:**
- Produces: tested tooling for two-build logical equality, full tamper rejection, and units bound to real staging paths. Task 15 applies it to the frozen final commit.
- Units are written to a separate sealed `UNIT_ROOT`, never under the authority root. The authoritative bundle renders the same commit-qualified logical `/opt/trading-agent-v2/releases/${COMMIT}/...` paths in both independent builds; `UNIT_ROOT/systemd-bundle-manifest.json` binds source commit, those logical paths, normalized template policy, and rendered unit hashes. The aggregate manifest then binds that unit-manifest digest. Staging-path units are a separate, non-authoritative verification bundle. The units do not contain the aggregate digest, so no circular hash exists.
- A canonical external staging-verification envelope binds the full candidate aggregate/authoritative-unit digest, staging unit-bundle digest, intended absolute staging prefix, materialization-tool blob, and systemd output hashes. It never replaces `UNIT_ROOT` inside candidate authority. Promotion binds this envelope digest as qualification evidence.
- `compare-builds.py` requires explicit left/right app, backend, migrator, unit, and authority roots plus the expected commit.
- `tamper-candidate.py` requires explicit pristine app/backend/migrator/unit/authority roots, a pristine input-CAS root, independent expected aggregate/input digests, and a private scratch root. It runs one named case or `--case all` and never edits pristine inputs.
- `promote-candidate.py promote-pair` is copy-on-promote only. It accepts two byte-equal sealed `NO_GO` candidates plus a protected, independently reviewed one-use promotion record binding source authority, A/B/C comparison, tamper, staging-intent/final-intent systemd, aggregate, and both private output identities. In one operation it copies to two previously absent destinations, changes only each promotion leaf, recomputes aggregate authority, requires logical equality, fsyncs/seals, and emits `CANDIDATE_VERIFIED / NOT_INSTALLED / NOT_RUNNING`. It can never emit `INSTALLED`, mutate a quarantine input, publish staging, or touch `/opt`.
- The promotion leaf binds only the external pre-promotion qualification-record digest, never the final aggregate that contains that leaf. The final promoted A/B comparison supplies the independent expected final aggregate SHA-256, preserving the acyclic digest DAG.
- `source_authority.py` validates a protected external final-source record containing exact commit/tree/backend subtree, scoped-commit list, final verification-log digest, and Git blob hashes for every build/compose/compare/tamper/promotion/verifier/provisioning tool. Protected copies of the validator and safe source-archive materializer are pinned by the external `SOURCE_RELEASE_IMPLEMENTATION` approval, not by the record they parse. Task 15 may not derive release identity from ambient `HEAD`.
- `compose-candidate.py render-units --authority-path LOGICAL_PATH --app-source PATH --app-path LOGICAL_PATH --backend-source PATH --backend-path LOGICAL_PATH --unit-output PATH --source-commit SHA --path-mode {staging,final-intent}` validates physical sources separately from rendered logical paths and refuses scheduler/timer output or existing targets.
- `compose-candidate.py materialize-path-intent --verification-root PATH --logical-prefix ABSOLUTE_PATH --app-root PATH --backend-root PATH --migrator-root PATH --authority-root PATH --authoritative-unit-root PATH --verification-unit-root PATH --source-commit SHA --materialization-output PATH` first verifies the candidate against its authoritative unit root, separately verifies the non-authoritative staging unit bundle against the normalized template policy, materializes candidate bytes at the intended prefix and staging units only under `/etc/systemd/user`, then writes a canonical materialization record. After systemd runs, `compose-candidate.py seal-systemd-envelope --materialization-record PATH --stdout PATH --stderr PATH --output PATH` requires successful warning-free logs and writes the external envelope. Both commands reject digest substitution, `/`, traversal, existing output, and paths outside the approved staging or `/opt/trading-agent-v2/releases/${COMMIT}` prefixes.
- `compose-candidate.py materialize-final-intent --verification-root PATH --app-root PATH --backend-root PATH --migrator-root PATH --authority-root PATH --unit-root PATH --source-commit SHA --materialization-output PATH` copies only sealed task-owned inputs beneath the commit-qualified logical paths in the disposable verification root, emits a canonical materialization record for the same envelope finalizer, and never writes `/opt` itself.
- `provision-root.sh` accepts only named operation-specific protocols plus an independently supplied expected aggregate-manifest SHA-256. Fake-root tests qualify the complete split app/backend/migrator/unit/authority form for future use, but this plan authorizes only `--maintenance-only --candidate-root ... --destination ...`; that form verifies all five candidate subroots, rejects individual replacement-root, environment, or service arguments, and copies only authority plus the maintenance-filtered migrator containing exact runbooks/launchers. It rejects the legacy positional/monolithic protocol. Before any copy it hashes the migrator-contained `verify-stage.py`, compares that hash with an independently reviewed literal pin in the script, and invokes that sealed verifier over all candidate subroots with the external expected digest.

- [ ] **Step 1: Build twice in unrelated private fixture roots**

Compare source commit, backend subtree, CPython archive/interpreter hashes, locks, wheel selections, installed distributions, command/semantic manifests, complete file lists, and aggregate logical digest. Any binary exception must be path-specific and reviewed.

The safe archive materializer runs under pinned standalone CPython, rejects traversal, links, special files, duplicate/case-colliding paths, undeclared files, and mode/type drift, and proves the extracted paths against the exact Git tree/source policies before any exported code executes.

- [ ] **Step 2: Run every tamper case**

Modify application source, backend semantic input, migrator migration/role SQL, embedded interpreter, an installed distribution payload, a copied wheel in a private CAS clone, command argv, the sealed verifier, a rendered unit or its bundle manifest, add an unexpected executable, remove a manifest, and change promotion state. Component verification and input-CAS verification must each fail against the original independent expected digests. Rebuild a third throwaway candidate from pristine sealed inputs afterward and compare it to the first; merely reverifying an untouched candidate is insufficient.

- [ ] **Step 3: Render only API and worker units**

Require existing embedded interpreter/module/cwd, loopback 8401, worker concurrency one, distinct DB roles, no UV/worktree/global Python, no secret unit bytes, cleared Python/loader injection variables, and exact read-only app/backend/authority binds. Emit only the two units plus a non-secret `systemd-bundle-manifest.json` into `UNIT_ROOT`; do not emit scheduler service or timer. Materialize any required non-secret verification-only environment files inside the private verification root so `systemd-analyze` never resolves mutable operator files.

- [ ] **Step 4: Verify without installation**

```bash
systemd-analyze --user --recursive-errors=yes verify \
  "$UNIT_ROOT/trading-job-api.service" \
  "$UNIT_ROOT/trading-job-worker.service"
```

Capture stdout and stderr separately and require exit zero, empty stderr, and no warning/error text. Assert the unit bundle contains exactly the API unit, worker unit, and manifest. Also verify final-intent `/opt/trading-agent-v2/...` units in a disposable `--root` tree with `--recursive-errors=yes` and the same zero-warning rule. Do not install units, reload the daemon, or start a service. Results here qualify the tooling; final candidate paths are verified again in Task 15.

- [ ] **Step 5: TDD copy-on-promote authority**

Tests must reject promotion before every qualification digest is present, same reviewer/requester, expired or wrong-source record, a candidate not in exact `NO_GO`, any changed component/unit/input digest, pre-existing destination, in-place mutation, `INSTALLED`/`RUNNING`, and a second/replayed use. Two independent promotions from byte-equal NO_GO candidates must produce byte-equal logical authority. Promotion failures leave source quarantine roots unchanged and no destination labeled verified.

- [ ] **Step 6: Replace the legacy provisioning protocol in fake roots**

Write RED tests proving the old monolithic/positional invocation, omitted required roots, working-tree verifier path, mismatched aggregate digest, mutable root, scheduler/timer unit, and verifier-pin drift all fail before any destination write. For `--maintenance-only`, also reject any app/backend/unit/env/service argument, missing or mismatched maintenance-subset manifest, absent sealed runbook/launcher, full-candidate verifier masquerading as subset verification, or destination outside the exact maintenance prefix. Fake-root tests model the independently approval-pinned protected-launcher copy/hash-before-execute protocol and reject execution from a user-writable path. Implement only the named protocols above. This task never writes `/opt`, invokes `systemctl`, or installs units.

- [ ] **Step 7: Re-pin the privileged verifier only after its final bytes freeze**

Compute the final SHA-256 of `ops/release-v2/verify-stage.py`, update the independent literal pin in `ops/release-v2/provision-root.sh`, and add a test that recomputes and requires equality. The pin must not be derived from a manifest controlled by the same candidate. Any later verifier edit invalidates Task 11 and requires a new pin, tests, clean-source freeze, and release build.

- [ ] **Step 8: Run focused tests and commit verification tooling**

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest -q \
  tests/runtime_release/test_release_reproducibility.py \
  tests/runtime_release/test_release_tamper.py \
  tests/runtime_release/test_release_promotion.py \
  tests/runtime_release/test_systemd_staging.py \
  tests/runtime_release/test_source_authority.py \
  tests/runtime_release/test_v2_provisioning.py
git add ops/release-v2/compare-builds.py \
  ops/release-v2/tamper-candidate.py \
  ops/release-v2/promote-candidate.py \
  ops/release-v2/provision-root.sh \
  packages/runtime_release/source_authority.py \
  packages/runtime_release/v2.py \
  schemas/final-source-authority.schema.json \
  schemas/release-candidate-promotion.schema.json \
  scripts/validate_final_source_authority.py \
  scripts/materialize_release_source.py \
  tests/runtime_release/test_release_reproducibility.py \
  tests/runtime_release/test_release_tamper.py \
  tests/runtime_release/test_release_promotion.py \
  tests/runtime_release/test_systemd_staging.py \
  tests/runtime_release/test_source_authority.py \
  tests/runtime_release/test_v2_provisioning.py \
  docs/implementation/job-plane-reproducibility-evidence.md \
  docs/implementation/job-plane-tamper-evidence.md \
  docs/implementation/job-plane-systemd-staging-verification.md
git commit -m "release: verify reproducibility tamper and systemd paths"
```

**Exit gate:** pre-freeze builds have equal logical authority, all tamper cases reject, both systemd verification modes exit zero with no warnings, the future installer accepts only the split sealed protocol, and ports/services remain unchanged.

---
