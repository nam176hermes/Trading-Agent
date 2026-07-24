# Package F — Clean Candidate and Staging Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Copy boundary:** This file is standalone and ends at the preparation hard stop. Do not execute Package G automatically.

**Goal:** Freeze the final clean source authority, run complete verification, and publish one immutable staging candidate only after reproducibility, tamper, systemd, review, and copy-on-promote gates pass.

**Architecture:** Bind source identity outside Git, build three independent `NO_GO` candidates with pinned offline drivers, verify future staging and `/opt` paths, then promote a pair and atomically publish one parent.

**Tech Stack:** Git archives, pinned CPython 3.11, offline wheels, pytest, npm, JSON manifests, systemd-analyze.

## Global Constraints

- This file is non-authorizing until exact source/release implementation scope is granted.
- Final disposable tests require two new final-commit records: `DISPOSABLE_PG_RED` and `DISPOSABLE_PG_GREEN`.
- Keep runtime PostgreSQL offline and all Job services/timer inactive; ports 55432 and 8401 remain closed.
- Keep `paper/paper`, false/false live gates, and kill-switch semantics unchanged.
- Do not use host `uv`/Python, a mutable checkout script, global site-packages, or network during final builds.
- Do not publish any commit-qualified staging path while promotion is `NO_GO`.
- Do not install `/opt`, units, environment files, or credentials.
- Do not run SNAPSHOT or any provider/broker/exchange code.
- Runtime counts and 30/0 may only be cited as timestamped historical evidence while PostgreSQL remains offline.
- Stop on a dirty source tree, failed required test, build drift, tamper false-negative, systemd warning, missing digest, failed human promotion review, or any runtime change.

## Package Authority and Exit Gate

- **Entry:** Packages A–E complete; exact final source policies, inputs, runbooks, and authority tooling committed.
- **Produces:** protected final-source authority and one atomic `CANDIDATE_VERIFIED / NOT_INSTALLED / NOT_RUNNING` staging parent.
- **Exit:** Git is clean; three NO_GO builds agree; promoted pair agrees; independent verifier passes; runtime remains untouched.
- **Hard stop:** end the session after Task 15. Package G needs a new explicit `/opt` provisioning approval.

---

### Task 14: Freeze and verify the final clean source candidate

**Files:**
- Create before the final test run: `docs/implementation/job-plane-clean-candidate-v4.md`
- Create before the final test run: `docs/implementation/job-plane-authority-preparation-final.md`
- Create before the final test run: `docs/implementation/job-plane-residual-risks-v3.md`
- External evidence only: exact final verification log and its SHA-256
- External evidence only: mode-0600 final-source authority record and mode-0500 protected copies of its validated source-authority validator, safe archive materializer, and reviewed provisioning launcher

**Interfaces:**
- Produces: the exact clean commit/tree consumed by Task 15.
- Avoids impossible self-reference: a file committed inside the candidate does not claim to contain its own Git commit hash. The external verification record and release manifest bind final `HEAD`.
- The external record binds final commit/tree, reviewed backend subtree/per-file policy, scoped commit list, verification-log digest, acquisition-record digest, immutable input/CAS manifest digest, source-policy blobs, and Git blob hashes for every build, compose, compare, tamper, promotion, verifier, and provisioning tool. It is reviewed outside Git and contains no secret.
- Task 15 consumes this protected record; it must not discover or substitute identity from ambient `HEAD`.

- [ ] **Step 1: Require a clean source tree before writing the deterministic gate document**

Record scoped commits, dependency changes, excluded old dirty-worktree paths, test commands, stop conditions, and the rule that dynamic final identity/evidence remains external. Do not copy logs, runtime evidence, build outputs, approvals, or placeholders into Git.

- [ ] **Step 2: Commit the deterministic gate document**

```bash
git add docs/implementation/job-plane-clean-candidate-v4.md \
  docs/implementation/job-plane-authority-preparation-final.md \
  docs/implementation/job-plane-residual-risks-v3.md
git commit -m "docs: define final clean candidate evidence gate"
git diff --check
git status --short --branch
```

Expected: status is clean. The documentation-only commit must not change `git rev-parse HEAD:legacy/research-backend` from the reviewed subtree.

- [ ] **Step 3: Run complete verification on that exact final commit**

The final commit differs from the earlier RED/GREEN proof commits, so obtain two new short-lived, exact-final-commit records: one `DISPOSABLE_PG_RED`, one `DISPOSABLE_PG_GREEN`. They remain non-interchangeable. First run the broad suite with all disposable controls unset; every database-starting operation must be an explicit intended skip. Then run only the reviewed RED operation list under the RED record and only the reviewed GREEN/restore operation list under the GREEN record.

```bash
FINAL_COMMIT="$(git rev-parse HEAD)"
FINAL_TREE="$(git rev-parse HEAD^{tree})"
make audit
make check-contracts
python3 scripts/validate_disposable_postgres_approval.py \
  --record "$TRADING_TEST_DISPOSABLE_RED_APPROVAL_RECORD" \
  --expected-scope DISPOSABLE_PG_RED \
  --expected-commit "$FINAL_COMMIT" \
  --expected-tree "$FINAL_TREE"
python3 scripts/validate_disposable_postgres_approval.py \
  --record "$TRADING_TEST_DISPOSABLE_GREEN_APPROVAL_RECORD" \
  --expected-scope DISPOSABLE_PG_GREEN \
  --expected-commit "$FINAL_COMMIT" \
  --expected-tree "$FINAL_TREE"
env -u TRADING_TEST_ALLOW_DISPOSABLE_POSTGRES \
  -u TRADING_TEST_DISPOSABLE_APPROVAL_RECORD \
  -u TRADING_TEST_DISPOSABLE_APPROVAL_SCOPE \
  PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest -q \
  tests/jobs tests/control_api/test_alembic_schema.py
TRADING_TEST_ALLOW_DISPOSABLE_POSTGRES=YES \
  TRADING_TEST_DISPOSABLE_APPROVAL_RECORD="$TRADING_TEST_DISPOSABLE_RED_APPROVAL_RECORD" \
  TRADING_TEST_DISPOSABLE_APPROVAL_SCOPE=DISPOSABLE_PG_RED \
  PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest -q \
  tests/jobs/test_job_authority_catalog.py \
  tests/jobs/test_job_event_chain_authority.py
TRADING_TEST_ALLOW_DISPOSABLE_POSTGRES=YES \
  TRADING_TEST_DISPOSABLE_APPROVAL_RECORD="$TRADING_TEST_DISPOSABLE_GREEN_APPROVAL_RECORD" \
  TRADING_TEST_DISPOSABLE_APPROVAL_SCOPE=DISPOSABLE_PG_GREEN \
  PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest -q \
  tests/jobs/test_job_authority_catalog.py \
  tests/jobs/test_job_event_chain_authority.py \
  tests/jobs/test_job_transition_authority.py \
  tests/jobs/test_job_role_permissions.py \
  tests/jobs/test_repository_transition_capabilities.py
PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest -q tests/runtime_release
PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest -q \
  tests/production/test_postgres_recovery_approval.py
cd apps/dashboard
npm test
./node_modules/.bin/tsc --noEmit
npm run lint
npm run build
cd ../..
git diff --check
test "$(git rev-parse HEAD)" = "$FINAL_COMMIT"
test "$(git rev-parse HEAD^{tree})" = "$FINAL_TREE"
git status --short --branch
```

Also run the documented isolated backend offline suite, standalone integration suite, and Phase 1 safety suite. Save fresh totals, exact command lines, timestamps, exit codes, `FINAL_COMMIT`, `FINAL_TREE`, and a sanitized log digest in the protected external evidence root. Require zero modified, staged, and untracked paths when all checks finish.

- [ ] **Step 4: Seal and independently review final source authority**

Create the external record from the already captured `FINAL_COMMIT`/`FINAL_TREE`; do not recompute those values in Task 15. Require the reviewed backend subtree to be unchanged, all recorded tool/source-policy blob IDs to resolve at that exact commit, the Task 9 input-policy blobs and acquisition/CAS hashes to match, and the verification log digest to match the completed run. Copy the committed validator, source materializer, and provisioning launcher to the protected evidence root, record their Git blobs and file SHA-256 values, make all three non-writable, then validate the record with pinned CPython in isolated mode. The future `MAINTENANCE_KIT_PROVISIONING` approval independently pins the protected launcher hash before elevation; the launcher's self-declared pins are never the trust root.

```bash
PINNED_INPUT_PYTHON="$RELEASE_INPUT_ROOT/python/runtime/bin/python3.11"
"$PINNED_INPUT_PYTHON" -I scripts/validate_final_source_authority.py \
  --record "$FINAL_SOURCE_AUTHORITY_RECORD" \
  --expected-commit "$FINAL_COMMIT" \
  --expected-tree "$FINAL_TREE" \
  --expected-verification-log-sha256 "$FINAL_VERIFICATION_LOG_SHA256"
test "$(stat -c '%a' "$FINAL_SOURCE_AUTHORITY_RECORD")" = 600
test -z "$(git status --porcelain=v1 --untracked-files=all)"
```

The record must be canonical, placeholder-free, and independently reviewed. If any source byte changes afterward, invalidate the record and repeat Task 14 from the test run.

**Exit gate:** exact final commit is clean and fully green; the protected final-source authority is valid and reviewed; target runtime PostgreSQL and every Job service remain untouched.

---

### Task 15: Build and verify the final immutable staging candidate

**Files:**
- Read only: protected final-source authority and exact source commit from Task 14
- External outputs only: application, backend, migrator, authority, and unit roots plus exact logs/manifests under the approved release-candidate/evidence roots

**Interfaces:**
- Produces: a static `CANDIDATE_VERIFIED / NOT_INSTALLED / NOT_RUNNING` authority bound to the exact Task 14 commit.
- Makes no Git change. If any source or deterministic documentation must change, abandon the build, return to Task 14, create a new commit, and rebuild from zero.
- `COMMIT`, `TREE`, backend subtree, tool hashes, and input-policy hashes come only from the validated external final-source record. Ambient `HEAD` is checked for equality but never used as authority.
- Final staging publication is one atomic parent rename to `/home/thenam176/.local/share/trading-agent/release-candidates/job-plane-${COMMIT}/`, containing exact `app/`, `backend/`, `migrator/`, `authority/`, and `units/` subroots. No commit-qualified public staging path exists while state is `NO_GO`.
- Both builds use separately materialized build drivers from the pinned standalone CPython and local build-wheel CAS. No command in this task invokes `uv`, a host Python, a global site-package, or a working-tree script.

- [ ] **Step 1: Validate source authority and create two independent source exports**

Invoke the protected Task 14 copy of `validate_final_source_authority.py` with the checksum-pinned standalone input Python. Its `--print-field` mode returns one already-validated scalar and rejects control characters or noncanonical records. Verify the local repository contains that exact commit/tree/backend subtree and is clean; equality is a check, not a source of identity. Create two independent `git archive` exports of that exact commit, require identical archive hashes, extract them into unrelated private roots, and revalidate their file/blob policies before executing any exported tool.

```bash
BASE=/home/thenam176/.local/share/trading-agent/release-candidates
INPUTS=/home/thenam176/.local/share/trading-agent/release-inputs/v2
EVIDENCE=/home/thenam176/.local/share/trading-agent/release-evidence/v2
PINNED_INPUT_PYTHON="$INPUTS/python/runtime/bin/python3.11"
TRUSTED_SOURCE_VALIDATOR="$EVIDENCE/tools/validate_final_source_authority.py"
TRUSTED_SOURCE_MATERIALIZER="$EVIDENCE/tools/materialize_release_source.py"
test "$(sha256sum "$PINNED_INPUT_PYTHON" | cut -d' ' -f1)" = \
  "$SOURCE_RELEASE_APPROVED_INPUT_PYTHON_SHA256"
test "$(sha256sum "$TRUSTED_SOURCE_VALIDATOR" | cut -d' ' -f1)" = \
  "$SOURCE_RELEASE_APPROVED_VALIDATOR_SHA256"
test "$(sha256sum "$TRUSTED_SOURCE_MATERIALIZER" | cut -d' ' -f1)" = \
  "$SOURCE_RELEASE_APPROVED_MATERIALIZER_SHA256"

COMMIT="$("$PINNED_INPUT_PYTHON" -I "$TRUSTED_SOURCE_VALIDATOR" \
  --record "$FINAL_SOURCE_AUTHORITY_RECORD" --print-field source_commit)"
TREE="$("$PINNED_INPUT_PYTHON" -I "$TRUSTED_SOURCE_VALIDATOR" \
  --record "$FINAL_SOURCE_AUTHORITY_RECORD" --print-field source_tree)"
BACKEND_TREE="$("$PINNED_INPUT_PYTHON" -I "$TRUSTED_SOURCE_VALIDATOR" \
  --record "$FINAL_SOURCE_AUTHORITY_RECORD" --print-field backend_subtree)"
EXPECTED_INPUT_DIGEST="$("$PINNED_INPUT_PYTHON" -I "$TRUSTED_SOURCE_VALIDATOR" \
  --record "$FINAL_SOURCE_AUTHORITY_RECORD" --print-field input_manifest_sha256)"
EXPECTED_VERIFIER_SHA="$("$PINNED_INPUT_PYTHON" -I "$TRUSTED_SOURCE_VALIDATOR" \
  --record "$FINAL_SOURCE_AUTHORITY_RECORD" --print-field release_verifier_sha256)"

test "$(git rev-parse HEAD)" = "$COMMIT"
test "$(git rev-parse HEAD^{tree})" = "$TREE"
test "$(git rev-parse HEAD:legacy/research-backend)" = "$BACKEND_TREE"
test -z "$(git status --porcelain=v1 --untracked-files=all)"

EXPORT_A="$(mktemp -d "$BASE/.source-a-$COMMIT.XXXXXX")"
EXPORT_B="$(mktemp -d "$BASE/.source-b-$COMMIT.XXXXXX")"
git archive --format=tar -o "$EXPORT_A/source.tar" "$COMMIT"
git archive --format=tar -o "$EXPORT_B/source.tar" "$COMMIT"
test "$(sha256sum "$EXPORT_A/source.tar" | cut -d' ' -f1)" = \
  "$(sha256sum "$EXPORT_B/source.tar" | cut -d' ' -f1)"
"$PINNED_INPUT_PYTHON" -I "$TRUSTED_SOURCE_MATERIALIZER" \
  --archive "$EXPORT_A/source.tar" --output "$EXPORT_A/source" \
  --authority-record "$FINAL_SOURCE_AUTHORITY_RECORD"
"$PINNED_INPUT_PYTHON" -I "$TRUSTED_SOURCE_MATERIALIZER" \
  --archive "$EXPORT_B/source.tar" --output "$EXPORT_B/source" \
  --authority-record "$FINAL_SOURCE_AUTHORITY_RECORD"
```

- [ ] **Step 2: Build two candidates with independently bootstrapped offline drivers**

Bootstrap `DRIVER_A` and `DRIVER_B` from the exact standalone-Python/build-wheel input closure, then invoke each commit-exported `run-offline-build.py` under its own driver. The wrapper must prove a no-network namespace, isolated HOME/cache, cleared Python/loader variables, `PIP_NO_INDEX=1`, `UV_OFFLINE=1`, and source-archive-only input before it invokes `build-candidate.py`. Each build creates app, backend, migrator, authoritative final-intent unit, and authority subroots in a new private output root.

```bash
SCRATCH_A="$(mktemp -d "$BASE/.build-a-$COMMIT.XXXXXX")"
SCRATCH_B="$(mktemp -d "$BASE/.build-b-$COMMIT.XXXXXX")"
DRIVER_A="$SCRATCH_A/driver"
DRIVER_B="$SCRATCH_B/driver"
BUILD_A="$SCRATCH_A/candidate"
BUILD_B="$SCRATCH_B/candidate"
FINAL_PREFIX="/opt/trading-agent-v2/releases/$COMMIT"

"$PINNED_INPUT_PYTHON" -I "$EXPORT_A/source/ops/release-v2/bootstrap-build-driver.py" \
  --input-root "$INPUTS" --expected-input-digest "$EXPECTED_INPUT_DIGEST" \
  --output "$DRIVER_A"
"$PINNED_INPUT_PYTHON" -I "$EXPORT_B/source/ops/release-v2/bootstrap-build-driver.py" \
  --input-root "$INPUTS" --expected-input-digest "$EXPECTED_INPUT_DIGEST" \
  --output "$DRIVER_B"

"$DRIVER_A/runtime/bin/python3.11" -I \
  "$EXPORT_A/source/ops/release-v2/run-offline-build.py" \
  --driver-root "$DRIVER_A" --input-root "$INPUTS" \
  --expected-input-digest "$EXPECTED_INPUT_DIGEST" \
  --builder "$EXPORT_A/source/ops/release-v2/build-candidate.py" -- \
  --source-archive "$EXPORT_A/source.tar" --source-commit "$COMMIT" \
  --source-tree "$TREE" --input-root "$INPUTS" --build-driver "$DRIVER_A" \
  --output-root "$BUILD_A" \
  --logical-prefix "$FINAL_PREFIX"
"$DRIVER_B/runtime/bin/python3.11" -I \
  "$EXPORT_B/source/ops/release-v2/run-offline-build.py" \
  --driver-root "$DRIVER_B" --input-root "$INPUTS" \
  --expected-input-digest "$EXPECTED_INPUT_DIGEST" \
  --builder "$EXPORT_B/source/ops/release-v2/build-candidate.py" -- \
  --source-archive "$EXPORT_B/source.tar" --source-commit "$COMMIT" \
  --source-tree "$TREE" --input-root "$INPUTS" --build-driver "$DRIVER_B" \
  --output-root "$BUILD_B" \
  --logical-prefix "$FINAL_PREFIX"
```

- [ ] **Step 3: Compare and seal the independent NO_GO aggregate without publishing**

Require equality of source/tree/backend identity, all three component identities, CPython archive/interpreter, locks, wheel/installed-package manifests, commands, semantic inputs, migration authority through 0007, exact unit bundle, complete file lists, `NO_GO / NOT_INSTALLED / NOT_RUNNING`, and aggregate logical digest. `compare-builds.py` writes a mode-0600 external comparison record containing both independently computed `aggregate-manifest.json` SHA-256 values. Review that record, require equality, and use its value as the independent expected NO_GO aggregate digest; it is not read from a candidate-controlled pointer. Keep both builds only in private quarantine roots.

```bash
QA_APP="$BUILD_A/app"
QA_BACKEND="$BUILD_A/backend"
QA_MIGRATOR="$BUILD_A/migrator"
QA_AUTHORITY="$BUILD_A/authority"
QA_UNIT="$BUILD_A/units"
NO_GO_COMPARISON_EVIDENCE="$EVIDENCE/no-go-comparison-$COMMIT.json"
CANDIDATE_ROOT="$BASE/job-plane-$COMMIT"

test ! -e "$CANDIDATE_ROOT"

"$DRIVER_A/runtime/bin/python3.11" -I \
  "$EXPORT_A/source/ops/release-v2/compare-builds.py" \
  --left-app "$BUILD_A/app" --left-backend "$BUILD_A/backend" \
  --left-migrator "$BUILD_A/migrator" --left-unit "$BUILD_A/units" \
  --left-authority "$BUILD_A/authority" \
  --right-app "$BUILD_B/app" --right-backend "$BUILD_B/backend" \
  --right-migrator "$BUILD_B/migrator" --right-unit "$BUILD_B/units" \
  --right-authority "$BUILD_B/authority" --expected-commit "$COMMIT" \
  --evidence-output "$NO_GO_COMPARISON_EVIDENCE"
test "$(stat -c '%a' "$NO_GO_COMPARISON_EVIDENCE")" = 600
EXPECTED_AGGREGATE_SHA="$("$DRIVER_A/runtime/bin/python3.11" -I \
  "$EXPORT_A/source/ops/release-v2/compare-builds.py" verify-evidence \
  --record "$NO_GO_COMPARISON_EVIDENCE" \
  --print-field aggregate_manifest_sha256)"

SEALED_VERIFIER="$QA_MIGRATOR/ops/release-v2/verify-stage.py"
test "$(sha256sum "$SEALED_VERIFIER" | cut -d' ' -f1)" = "$EXPECTED_VERIFIER_SHA"
"$QA_MIGRATOR/runtime/bin/python3.11" -I "$SEALED_VERIFIER" \
  --authority-root "$QA_AUTHORITY" --app-root "$QA_APP" \
  --backend-root "$QA_BACKEND" --migrator-root "$QA_MIGRATOR" \
  --unit-root "$QA_UNIT" --expected-commit "$COMMIT" \
  --expected-promotion-state NO_GO \
  --expected-aggregate-sha256 "$EXPECTED_AGGREGATE_SHA"
```

- [ ] **Step 4: Run every tamper case and perform an actual third clean rebuild**

Application source, backend semantic input, migrator migration/role SQL, interpreter, installed package, copied CAS wheel, argv, sealed verifier, rendered unit/bundle, unexpected executable, missing manifest, and promotion-state mutations must each fail against the original independent expected digests. The tamper tool copies both candidate roots and selected input CAS objects before mutation. Afterward independently bootstrap `DRIVER_C`, export the exact source again, build `BUILD_C` from the pristine input root with network disabled, and compare C to the published A. Reverification of untouched A alone does not satisfy this gate.

```bash
TAMPER_ROOT="$(mktemp -d "$BASE/.tamper-$COMMIT.XXXXXX")"
TAMPER_EVIDENCE="$EVIDENCE/tamper-$COMMIT.json"
"$DRIVER_A/runtime/bin/python3.11" -I \
  "$EXPORT_A/source/ops/release-v2/tamper-candidate.py" \
  --source-app "$QA_APP" --source-backend "$QA_BACKEND" \
  --source-migrator "$QA_MIGRATOR" --source-unit "$QA_UNIT" \
  --source-authority "$QA_AUTHORITY" --source-input-root "$INPUTS" \
  --expected-input-digest "$EXPECTED_INPUT_DIGEST" \
  --expected-aggregate-sha256 "$EXPECTED_AGGREGATE_SHA" \
  --scratch-root "$TAMPER_ROOT" --case all \
  --evidence-output "$TAMPER_EVIDENCE"

EXPORT_C="$(mktemp -d "$BASE/.source-c-$COMMIT.XXXXXX")"
SCRATCH_C="$(mktemp -d "$BASE/.build-c-$COMMIT.XXXXXX")"
DRIVER_C="$SCRATCH_C/driver"
BUILD_C="$SCRATCH_C/candidate"
git archive --format=tar -o "$EXPORT_C/source.tar" "$COMMIT"
"$PINNED_INPUT_PYTHON" -I "$TRUSTED_SOURCE_MATERIALIZER" \
  --archive "$EXPORT_C/source.tar" --output "$EXPORT_C/source" \
  --authority-record "$FINAL_SOURCE_AUTHORITY_RECORD"
"$PINNED_INPUT_PYTHON" -I "$EXPORT_C/source/ops/release-v2/bootstrap-build-driver.py" \
  --input-root "$INPUTS" --expected-input-digest "$EXPECTED_INPUT_DIGEST" \
  --output "$DRIVER_C"
"$DRIVER_C/runtime/bin/python3.11" -I \
  "$EXPORT_C/source/ops/release-v2/run-offline-build.py" \
  --driver-root "$DRIVER_C" --input-root "$INPUTS" \
  --expected-input-digest "$EXPECTED_INPUT_DIGEST" \
  --builder "$EXPORT_C/source/ops/release-v2/build-candidate.py" -- \
  --source-archive "$EXPORT_C/source.tar" --source-commit "$COMMIT" \
  --source-tree "$TREE" --input-root "$INPUTS" --build-driver "$DRIVER_C" \
  --output-root "$BUILD_C" \
  --logical-prefix "$FINAL_PREFIX"
"$DRIVER_C/runtime/bin/python3.11" -I \
  "$EXPORT_C/source/ops/release-v2/compare-builds.py" \
  --left-app "$QA_APP" --left-backend "$QA_BACKEND" \
  --left-migrator "$QA_MIGRATOR" --left-unit "$QA_UNIT" \
  --left-authority "$QA_AUTHORITY" \
  --right-app "$BUILD_C/app" --right-backend "$BUILD_C/backend" \
  --right-migrator "$BUILD_C/migrator" --right-unit "$BUILD_C/units" \
  --right-authority "$BUILD_C/authority" --expected-commit "$COMMIT"
```

- [ ] **Step 5: Verify exact staging and final-intent systemd paths with zero warnings**

Render a separate non-authoritative staging bundle against the exact not-yet-published `CANDIDATE_ROOT` paths, while validating bytes from the quarantine A roots. Materialize those future absolute paths beneath an alternate verification root, then verify. Separately materialize the authoritative final-intent `/opt` bundle. Both invocations use `--recursive-errors=yes`, name exactly the API and worker units, capture stdout/stderr outside the materialized roots, require empty stderr and no warning/error output, and assert that no scheduler/timer unit exists. Do not publish, install, daemon-reload, start, or enable anything.

```bash
STAGING_UNIT_SCRATCH="$(mktemp -d "$BASE/.staging-units-$COMMIT.XXXXXX")"
STAGING_UNIT_ROOT="$STAGING_UNIT_SCRATCH/units"
STAGING_MATERIALIZATION="$STAGING_UNIT_SCRATCH/materialization.json"
STAGING_ENVELOPE="$EVIDENCE/staging-systemd-envelope-$COMMIT.json"
"$DRIVER_A/runtime/bin/python3.11" -I \
  "$EXPORT_A/source/ops/release-v2/compose-candidate.py" render-units \
  --authority-path "$CANDIDATE_ROOT/authority" \
  --app-source "$QA_APP" --app-path "$CANDIDATE_ROOT/app" \
  --backend-source "$QA_BACKEND" --backend-path "$CANDIDATE_ROOT/backend" \
  --unit-output "$STAGING_UNIT_ROOT" --source-commit "$COMMIT" \
  --path-mode staging
test "$(find "$STAGING_UNIT_ROOT" -maxdepth 1 -type f -printf '%f\n' | sort | paste -sd, -)" = \
  "systemd-bundle-manifest.json,trading-job-api.service,trading-job-worker.service"
STAGING_INTENT_ROOT="$STAGING_UNIT_SCRATCH/root"
"$DRIVER_A/runtime/bin/python3.11" -I \
  "$EXPORT_A/source/ops/release-v2/compose-candidate.py" \
  materialize-path-intent --verification-root "$STAGING_INTENT_ROOT" \
  --logical-prefix "$CANDIDATE_ROOT" \
  --app-root "$QA_APP" --backend-root "$QA_BACKEND" \
  --migrator-root "$QA_MIGRATOR" --authority-root "$QA_AUTHORITY" \
  --authoritative-unit-root "$QA_UNIT" \
  --verification-unit-root "$STAGING_UNIT_ROOT" \
  --source-commit "$COMMIT" \
  --materialization-output "$STAGING_MATERIALIZATION"
systemd-analyze --user --root="$STAGING_INTENT_ROOT" --recursive-errors=yes verify \
  /etc/systemd/user/trading-job-api.service \
  /etc/systemd/user/trading-job-worker.service \
  >"$STAGING_UNIT_SCRATCH/systemd.stdout" \
  2>"$STAGING_UNIT_SCRATCH/systemd.stderr"
test ! -s "$STAGING_UNIT_SCRATCH/systemd.stderr"
if rg -i '\b(warn|error|failed)\b' "$STAGING_UNIT_SCRATCH/systemd.stdout"; then exit 1; fi
"$DRIVER_A/runtime/bin/python3.11" -I \
  "$EXPORT_A/source/ops/release-v2/compose-candidate.py" \
  seal-systemd-envelope --materialization-record "$STAGING_MATERIALIZATION" \
  --stdout "$STAGING_UNIT_SCRATCH/systemd.stdout" \
  --stderr "$STAGING_UNIT_SCRATCH/systemd.stderr" \
  --output "$STAGING_ENVELOPE"

FINAL_INTENT_SCRATCH="$(mktemp -d "$BASE/.final-intent-$COMMIT.XXXXXX")"
FINAL_INTENT_ROOT="$FINAL_INTENT_SCRATCH/root"
FINAL_INTENT_MATERIALIZATION="$FINAL_INTENT_SCRATCH/materialization.json"
FINAL_INTENT_ENVELOPE="$EVIDENCE/final-intent-systemd-envelope-$COMMIT.json"
"$DRIVER_A/runtime/bin/python3.11" -I \
  "$EXPORT_A/source/ops/release-v2/compose-candidate.py" \
  materialize-final-intent --verification-root "$FINAL_INTENT_ROOT" \
  --app-root "$QA_APP" --backend-root "$QA_BACKEND" \
  --migrator-root "$QA_MIGRATOR" --authority-root "$QA_AUTHORITY" \
  --unit-root "$QA_UNIT" --source-commit "$COMMIT" \
  --materialization-output "$FINAL_INTENT_MATERIALIZATION"
systemd-analyze --user --root="$FINAL_INTENT_ROOT" --recursive-errors=yes verify \
  /etc/systemd/user/trading-job-api.service \
  /etc/systemd/user/trading-job-worker.service \
  >"$FINAL_INTENT_SCRATCH/systemd.stdout" \
  2>"$FINAL_INTENT_SCRATCH/systemd.stderr"
test ! -s "$FINAL_INTENT_SCRATCH/systemd.stderr"
if rg -i '\b(warn|error|failed)\b' "$FINAL_INTENT_SCRATCH/systemd.stdout"; then exit 1; fi
"$DRIVER_A/runtime/bin/python3.11" -I \
  "$EXPORT_A/source/ops/release-v2/compose-candidate.py" \
  seal-systemd-envelope --materialization-record "$FINAL_INTENT_MATERIALIZATION" \
  --stdout "$FINAL_INTENT_SCRATCH/systemd.stdout" \
  --stderr "$FINAL_INTENT_SCRATCH/systemd.stderr" \
  --output "$FINAL_INTENT_ENVELOPE"
if find "$STAGING_UNIT_ROOT" "$FINAL_INTENT_ROOT/etc/systemd/user" \
  -type f | rg -q 'scheduler|timer'; then exit 1; fi
systemctl --user show \
  trading-job-api.service trading-job-worker.service \
  trading-job-scheduler.service trading-job-scheduler.timer \
  -p ActiveState -p UnitFileState -p MainPID -p NRestarts
if ss -ltnp | rg -q ':(55432|8401)\b'; then exit 1; fi
```

- [ ] **Step 6: Review qualification evidence, copy-on-promote twice, and publish once**

Seal hashes for the source authority, NO_GO A/B and A/C comparisons, tamper record, input closure, staging-intent and final-intent systemd envelopes, exact output path, and runtime-invariant observation into a mode-0600 promotion record. A distinct human reviewer must approve it after all prior steps finish. Then use one `promote-pair` invocation to create two private `CANDIDATE_VERIFIED` copies; compare and independently verify them. Only after that final comparison passes may one complete parent be atomically renamed to `CANDIDATE_ROOT`. The NO_GO quarantines and second promoted copy remain evidence until review; no component is published piecemeal.

```bash
PROMOTE_SCRATCH_A="$(mktemp -d "$BASE/.promote-a-$COMMIT.XXXXXX")"
PROMOTE_SCRATCH_B="$(mktemp -d "$BASE/.promote-b-$COMMIT.XXXXXX")"
PROMOTED_A="$PROMOTE_SCRATCH_A/candidate"
PROMOTED_B="$PROMOTE_SCRATCH_B/candidate"
FINAL_COMPARISON_EVIDENCE="$EVIDENCE/final-comparison-$COMMIT.json"

"$DRIVER_A/runtime/bin/python3.11" -I \
  "$EXPORT_A/source/ops/release-v2/promote-candidate.py" promote-pair \
  --left-app "$QA_APP" --left-backend "$QA_BACKEND" \
  --left-migrator "$QA_MIGRATOR" --left-unit "$QA_UNIT" \
  --left-authority "$QA_AUTHORITY" \
  --right-app "$BUILD_B/app" --right-backend "$BUILD_B/backend" \
  --right-migrator "$BUILD_B/migrator" --right-unit "$BUILD_B/units" \
  --right-authority "$BUILD_B/authority" \
  --promotion-record "$PROMOTION_AUTHORITY_RECORD" \
  --left-output "$PROMOTED_A" --right-output "$PROMOTED_B"

"$DRIVER_A/runtime/bin/python3.11" -I \
  "$EXPORT_A/source/ops/release-v2/compare-builds.py" \
  --left-app "$PROMOTED_A/app" --left-backend "$PROMOTED_A/backend" \
  --left-migrator "$PROMOTED_A/migrator" --left-unit "$PROMOTED_A/units" \
  --left-authority "$PROMOTED_A/authority" \
  --right-app "$PROMOTED_B/app" --right-backend "$PROMOTED_B/backend" \
  --right-migrator "$PROMOTED_B/migrator" --right-unit "$PROMOTED_B/units" \
  --right-authority "$PROMOTED_B/authority" --expected-commit "$COMMIT" \
  --expected-promotion-state CANDIDATE_VERIFIED \
  --evidence-output "$FINAL_COMPARISON_EVIDENCE"
EXPECTED_FINAL_AGGREGATE_SHA="$("$DRIVER_A/runtime/bin/python3.11" -I \
  "$EXPORT_A/source/ops/release-v2/compare-builds.py" verify-evidence \
  --record "$FINAL_COMPARISON_EVIDENCE" \
  --print-field aggregate_manifest_sha256)"

FINAL_SEALED_VERIFIER="$PROMOTED_A/migrator/ops/release-v2/verify-stage.py"
test "$(sha256sum "$FINAL_SEALED_VERIFIER" | cut -d' ' -f1)" = \
  "$EXPECTED_VERIFIER_SHA"
"$PROMOTED_A/migrator/runtime/bin/python3.11" -I "$FINAL_SEALED_VERIFIER" \
  --authority-root "$PROMOTED_A/authority" --app-root "$PROMOTED_A/app" \
  --backend-root "$PROMOTED_A/backend" --migrator-root "$PROMOTED_A/migrator" \
  --unit-root "$PROMOTED_A/units" --expected-commit "$COMMIT" \
  --expected-promotion-state CANDIDATE_VERIFIED \
  --expected-aggregate-sha256 "$EXPECTED_FINAL_AGGREGATE_SHA"
test ! -e "$CANDIDATE_ROOT"
mv "$PROMOTED_A" "$CANDIDATE_ROOT"
```

- [ ] **Step 7: Reverify the published staging root and stop boundary**

Run the same independently pinned verifier after the atomic parent rename and require no physical-path serialization or byte drift. Record final candidate path, all non-null digests, source/backend identities, interpreter/version/hash, lock/wheel closure, A/B/A-C/final-promoted comparisons, every tamper result, zero-warning systemd results, promotion record digest, and runtime invariant checks. Verify Git remains at the source-authority commit; never substitute current `HEAD` into evidence.

```bash
PUBLISHED_VERIFIER="$CANDIDATE_ROOT/migrator/ops/release-v2/verify-stage.py"
test "$(sha256sum "$PUBLISHED_VERIFIER" | cut -d' ' -f1)" = "$EXPECTED_VERIFIER_SHA"
"$CANDIDATE_ROOT/migrator/runtime/bin/python3.11" -I "$PUBLISHED_VERIFIER" \
  --authority-root "$CANDIDATE_ROOT/authority" \
  --app-root "$CANDIDATE_ROOT/app" --backend-root "$CANDIDATE_ROOT/backend" \
  --migrator-root "$CANDIDATE_ROOT/migrator" --unit-root "$CANDIDATE_ROOT/units" \
  --expected-commit "$COMMIT" --expected-promotion-state CANDIDATE_VERIFIED \
  --expected-aggregate-sha256 "$EXPECTED_FINAL_AGGREGATE_SHA"
systemctl --user show \
  trading-job-api.service trading-job-worker.service \
  trading-job-scheduler.service trading-job-scheduler.timer \
  -p ActiveState -p UnitFileState -p MainPID -p NRestarts
if ss -ltnp | rg -q ':(55432|8401)\b'; then
  exit 1
fi
test "$(git rev-parse HEAD)" = "$COMMIT"
test -z "$(git status --porcelain=v1 --untracked-files=all)"
```

Record `paper/paper`, false/false, kill switch INACTIVE, orders/trades 30/0, and zero Job Plane rows only as the last accepted historical evidence with its original timestamp. Do not reopen SQLite or claim a fresh runtime database observation.

**Exit gate:** one atomic staging parent exists only after two private promoted copies agree and verify from the independently pinned migrator-contained verifier plus external aggregate digest; three NO_GO clean builds agree around the tamper suite; systemd verification is warning-free; Git is unchanged; PostgreSQL remains offline; Job services remain inactive; 8401 remains closed; no job-insert path was invoked. Absolute zero-row status remains historical evidence until an approved runtime read is possible.

---

## Hard preparation-session stop boundary

An implementation session for this preparation plan must stop after Task 15. It may report only that no job-insert path was invoked and cite the last accepted zero-row evidence with its timestamp; while runtime PostgreSQL is offline it must not claim a fresh absolute row count. Tasks 16-19 below are follow-up plans, not continuation authority. Each requires a new explicit user invocation and its own reviewed approval. No approval obtained for Tasks 1-15 silently authorizes `/opt` provisioning, recovery, runtime backup/migration, or service rollout.

---
