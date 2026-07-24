# Canonical Source Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create `/home/thenam176/projects/trading-agent` as one standalone,
clean Git source authority containing the proven control plane, an exact
source-only research-backend snapshot, and an exact dashboard snapshot, while
leaving the active paper-trading runtime and all three source repositories
unchanged.

**Architecture:** The canonical root is a standalone local clone at the exact
core application/ops commit. A standard-library, Git-object importer builds,
reviews, applies, and re-verifies canonical manifests for the backend root and
dashboard subtree. Components keep separate dependency locks and communicate
through existing process/data contracts; no Python workspace, npm workspace,
nested repository, submodule, or repository symlink is introduced. A
read-only root audit enforces the resulting authority.

**Tech Stack:** Git object plumbing, Python 3.11 standard library, dataclasses,
JSON/SHA-256 manifests, pytest, uv, Next.js 16.2.6, React 19.2.4, TypeScript 5,
ESLint 9, npm lockfiles, Bash read-only verification.

## Global Constraints

- The approved design is
  `docs/superpowers/specs/2026-07-13-canonical-monorepo-design.md`; this plan
  implements only its first plan, **Canonical source consolidation**.
- Requested/effective mode stays `paper/paper`,
  `LIVE_EXECUTION_ENABLED=false`, and `LIVE_TRADING_APPROVED=false`.
- Never initialize or probe an exchange/broker, send/cancel an order, run a
  live strategy, alter a kill switch, or print credentials/environment files.
- Do not install into `/opt`, change `/etc`, reload/start/stop systemd, change
  PostgreSQL/Alembic, port 3002, Cloudflare, timers, cron, PM2, or active PIDs.
- Task 0 is a read-only prerequisite. A failed Phase 4B gate stops this plan;
  it may not be repaired or bypassed as part of consolidation.
- Do not reset, clean, reformat, commit in, or copy from the mutable working
  trees of the old backend/dashboard. Snapshot bytes come only from exact Git
  objects.
- Do not push, add a network remote, delete a branch/repository, or rewrite
  history. `migration-source` is a local read-only provenance remote.
- Preserve the three lock domains: root `uv.lock`, backend `uv.lock`, and
  dashboard `package-lock.json`. Do not add or update dependencies.
- Use `apply_patch` for hand-authored edits. Generated manifests and generated
  snapshot materialization are the only bulk mechanical writes.
- Every behavior change follows RED-GREEN-REFACTOR. A failed check stops the
  task; do not weaken an assertion or forbidden-path rule to make it pass.
- Commit after every green task using the exact commit message listed. Do not
  squash the backend or dashboard provenance commits.
- This plan must end with either `GO FOR MONOREPO RELEASE AUTHORITY V2` or
  `NO-GO — CANONICAL SOURCE OR EQUIVALENCE BLOCKERS REMAIN`. It never
  authorizes Release Authority v2 work or production cutover.

## Fixed Authority

| Component | Git identity | Git tree | Source prefix | Destination |
|---|---|---|---|---|
| Core application/ops | `d9d46fa363f26bd78f5560300d26913494e11e4d` | `bfac951424d09f21359fcc11abb0bbe000456b4e` | `.` | `.` |
| Research backend | `41f055b48033714c660f44cc20498b7545366e75` | `b15af11d8600e042e20403dba982a3c1bc1b4b60` | `.` | `legacy/research-backend` |
| Dashboard | `ca57a7e018eb3afdc263e40b343b7ebbe3f8ccbb` | `3246350253575256b0566cfd54076e8e8ce0412e` | `trading-agent` | `apps/dashboard` |
| Sealed Phase 4B metadata | `f1ed595c86df4cc7ac7274272dd00798ddb497a98ca3a12a1ed9680769468d7c` | n/a | sealed stage | prerequisite only |

The core source worktree may contain a docs-only authority amendment after
`d9d46fa363f26bd78f5560300d26913494e11e4d`. The canonical clone itself is
checked out at exactly that commit; application and ops bytes are never
inferred from the later documentation commit.

---

### Task 0: Enforce the Phase 4B and source-identity prerequisite

**Files:**
- Read: `ops/phase4b/verify-installed.sh`
- Read: `docs/implementation/phase-4b-preprovision-checkpoint.md`
- Create later, after Task 1: `docs/consolidation/phase4b-prerequisite-evidence.md`

**Interfaces:**
- Consumes the sealed stage
  `/home/thenam176/.cache/trading-agent-phase4b-stage-fdc085a0-41f055b4-sealed-final`
  and approved metadata digest from Fixed Authority.
- Produces only redacted read-only observations. It does not provision,
  clean, restart, or create the canonical root.

- [ ] **Step 1: Prove no provisioning process is running and the rejected
  root-owned residue is gone.**

  Run from
  `/home/thenam176/.local/share/codex-worktrees/trading-agent-phase4`:

  ```bash
  pgrep -af 'ops/phase4b/provision-root.sh|trading-agent-phase4b-stage' || true
  test ! -e /run/trading-agent-phase4-provision.63GKAy
  ```

  Expected: `pgrep` shows no provisioning command and `test` exits `0`. If the
  exact `/run` path still exists, stop. Its deletion is a separate destructive
  root action requiring explicit operator approval; do not run `sudo rm` from
  this plan.

- [ ] **Step 2: Verify installed immutable authority.**

  ```bash
  test -d /etc/trading-agent
  bash ops/phase4b/verify-installed.sh \
    /home/thenam176/.cache/trading-agent-phase4b-stage-fdc085a0-41f055b4-sealed-final \
    f1ed595c86df4cc7ac7274272dd00798ddb497a98ca3a12a1ed9680769468d7c
  ```

  Expected final line:

  ```text
  phase 4b installed authority verification passed
  ```

  Any missing `/etc/trading-agent`, verifier rejection, owner/mode mismatch,
  unit mismatch, or metadata mismatch is an immediate `NO-GO`. Return to the
  Phase 4B runtime-provisioning plan; do not reinterpret a partial `/opt`
  install as success.

- [ ] **Step 3: Recapture the safety invariant without printing secrets.**

  Read exactly `.mode`, the canonical kill-switch presence, the two named live
  variables from the active agent PID, and SQLite counts through URI
  `mode=ro`. Record values only; do not dump a process environment or database
  row.

  ```bash
  test "$(tr -d '\r\n' </home/thenam176/.hermes/crypto-research/.mode)" = paper
  test ! -e /home/thenam176/.hermes/crypto-research/.kill_switch
  python3 - <<'PY'
  import sqlite3
  uri = "file:/home/thenam176/.hermes/crypto-research/memory/trading.db?mode=ro"
  with sqlite3.connect(uri, uri=True) as db:
      values = tuple(db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                     for table in ("orders", "trades"))
  print(f"orders={values[0]} trades={values[1]}")
  assert values == (30, 0)
  PY
  ```

  For the two live variables, obtain `MainPID` with `systemctl --user show
  trading-agent.service --property=MainPID --value`, then use a short Python
  reader that selects only `LIVE_EXECUTION_ENABLED` and
  `LIVE_TRADING_APPROVED` from `/proc/$MAIN_PID/environ`. Expected values are
  `false` and `false`; missing, duplicate, unreadable, or different values stop
  the plan.

- [ ] **Step 4: Verify immutable Git identities and cleanliness.**

  ```bash
  git rev-parse d9d46fa363f26bd78f5560300d26913494e11e4d^{tree}
  git status --porcelain=v1
  git -C /home/thenam176/.local/share/codex-worktrees/trading-agent-phase4-backend rev-parse HEAD HEAD^{tree}
  git -C /home/thenam176/.local/share/codex-worktrees/trading-agent-phase4-backend status --porcelain=v1
  git -C /home/thenam176/.local/share/codex-worktrees/trading-agent-security-phase4 rev-parse HEAD HEAD:trading-agent
  git -C /home/thenam176/.local/share/codex-worktrees/trading-agent-security-phase4 status --porcelain=v1
  ```

  Expected: the three tree IDs match Fixed Authority; both component HEADs
  match Fixed Authority; all porcelain outputs are empty. Record the current
  core documentation HEAD separately and prove code-bearing paths are still
  the approved core tree by checking out the canonical clone at
  `d9d46fa363f26bd78f5560300d26913494e11e4d`
  in Task 1, never by copying the current worktree.

### Task 1: Create the standalone canonical root and authority record

**Files:**
- Create: `docs/consolidation/phase4b-prerequisite-evidence.md`
- Create: `docs/consolidation/source-checkpoint.md`
- Create: `ops/consolidation/source-authority.json`
- Copy as documentation: approved design and this implementation plan under
  `docs/superpowers/`

**Interfaces:**
- Canonical path: `/home/thenam176/projects/trading-agent`
- Branch: `codex/canonical-monorepo`
- Local provenance remote: `migration-source`
- `source-authority.json` is schema version 1 and rejects unknown keys in Task
  2's loader.

- [ ] **Step 1: Refuse ambiguous destination state.**

  ```bash
  test ! -e /home/thenam176/projects/trading-agent
  test ! -L /home/thenam176/projects/trading-agent
  ```

  Expected: both exit `0`. If a path already exists, stop and inspect it; do
  not delete, rename, merge into, or reuse it.

- [ ] **Step 2: Create a no-hardlink standalone clone at the fixed core
  commit.**

  ```bash
  git clone --no-hardlinks --no-checkout \
    /home/thenam176/.local/share/codex-worktrees/trading-agent-phase4 \
    /home/thenam176/projects/trading-agent
  git -C /home/thenam176/projects/trading-agent remote rename origin migration-source
  git -C /home/thenam176/projects/trading-agent checkout -b codex/canonical-monorepo \
    d9d46fa363f26bd78f5560300d26913494e11e4d
  ```

  Expected: checkout reports the new branch; `HEAD` is exactly
  `d9d46fa363f26bd78f5560300d26913494e11e4d`.

- [ ] **Step 3: Prove it owns one standalone Git directory.**

  ```bash
  test -d /home/thenam176/projects/trading-agent/.git
  test ! -L /home/thenam176/projects/trading-agent/.git
  git -C /home/thenam176/projects/trading-agent rev-parse --show-toplevel
  git -C /home/thenam176/projects/trading-agent rev-parse --path-format=absolute --git-common-dir
  git -C /home/thenam176/projects/trading-agent remote -v
  ```

  Expected: top-level is the canonical path; common-dir is its `.git`; the
  only remote is the local `migration-source`. A `.git` file, common-dir
  outside the root, network URL, or extra remote fails.

- [ ] **Step 4: Write exact authority JSON.**

  Use `apply_patch` to add this canonical key set and values:

  ```json
  {
    "schema_version": 1,
    "sealed_phase4b_metadata_sha256": "f1ed595c86df4cc7ac7274272dd00798ddb497a98ca3a12a1ed9680769468d7c",
    "components": {
      "core": {
        "repository": "/home/thenam176/.local/share/codex-worktrees/trading-agent-phase4",
        "commit": "d9d46fa363f26bd78f5560300d26913494e11e4d",
        "tree": "bfac951424d09f21359fcc11abb0bbe000456b4e",
        "source_prefix": ".",
        "destination_prefix": "."
      },
      "backend": {
        "repository": "/home/thenam176/.local/share/codex-worktrees/trading-agent-phase4-backend",
        "commit": "41f055b48033714c660f44cc20498b7545366e75",
        "tree": "b15af11d8600e042e20403dba982a3c1bc1b4b60",
        "source_prefix": ".",
        "destination_prefix": "legacy/research-backend"
      },
      "dashboard": {
        "repository": "/home/thenam176/.local/share/codex-worktrees/trading-agent-security-phase4",
        "commit": "ca57a7e018eb3afdc263e40b343b7ebbe3f8ccbb",
        "tree": "3246350253575256b0566cfd54076e8e8ce0412e",
        "source_prefix": "trading-agent",
        "destination_prefix": "apps/dashboard"
      }
    }
  }
  ```

- [ ] **Step 5: Add redacted prerequisite/checkpoint evidence and exact copies
  of the approved design/plan.**

  The evidence must record timestamp, PASS/FAIL, the fixed hashes, source
  branches/HEADs/status counts, `paper/paper`, `false/false`, kill-switch
  state, `30/0`, and verifier final result. It must not contain PIDs' other
  environment values, DSNs, credentials, file contents, or tokens. Add the two
  docs using `apply_patch`; verify their bytes against the source documents
  with `cmp -s` before commit.

- [ ] **Step 6: Commit the authority baseline.**

  ```bash
  git add docs/consolidation docs/superpowers ops/consolidation/source-authority.json
  git diff --cached --check
  git commit -m "chore: establish canonical source authority"
  ```

  Expected: one docs/authority commit whose parent is exactly
  `d9d46fa363f26bd78f5560300d26913494e11e4d`.

### Task 2: Build the strict authority and snapshot-manifest model

**Files:**
- Create: `packages/consolidation/__init__.py`
- Create: `packages/consolidation/authority.py`
- Create: `packages/consolidation/manifest.py`
- Create: `tests/consolidation/__init__.py`
- Create: `tests/consolidation/test_authority.py`
- Create: `tests/consolidation/test_manifest.py`

**Interfaces:**
- `load_source_authority(path: Path) -> SourceAuthority`
- `propose_manifest(authority: ComponentAuthority, policy: ImportPolicy) -> ComponentManifest`
- `canonical_manifest_bytes(manifest: ComponentManifest) -> bytes`
- `verify_manifest_source(manifest: ComponentManifest) -> None`
- Only Git modes `100644` and `100755` are regular files. `120000` symlinks,
  `160000` gitlinks, missing objects, duplicate paths, traversal, absolute
  paths, NUL/newline paths, Unicode-normalization collisions, and
  case-folding collisions fail closed.

- [ ] **Step 1: Write RED authority tests.**

  Test the exact JSON from Task 1 plus rejection of unknown/missing keys,
  non-absolute repository paths, invalid 40/64-hex values, unsupported schema,
  wrong component names/prefixes, source tree mismatch, and repository
  mutation. The first run must fail with an import error:

  ```bash
  uv run pytest -q tests/consolidation/test_authority.py
  ```

  Expected RED: `ModuleNotFoundError: packages.consolidation`.

- [ ] **Step 2: Implement immutable dataclasses and strict JSON loading.**

  Use these public shapes:

  ```python
  @dataclass(frozen=True, slots=True)
  class ComponentAuthority:
      name: str
      repository: Path
      commit: str
      tree: str
      source_prefix: PurePosixPath
      destination_prefix: PurePosixPath

  @dataclass(frozen=True, slots=True)
  class SourceAuthority:
      schema_version: int
      sealed_phase4b_metadata_sha256: str
      components: Mapping[str, ComponentAuthority]
  ```

  Resolve commits/trees with `git rev-parse --verify` using argument arrays,
  `check=True`, `text=False`, and a minimal environment. Never invoke a shell
  or use the source working tree.

- [ ] **Step 3: Write RED manifest tests.**

  Create temporary Git repositories that cover deterministic byte order,
  binary files, executable files, non-UTF-8 blob contents, modified source
  worktrees, a different commit after proposal, forbidden paths, symlinks,
  gitlinks, traversal-like names, duplicate/case/Unicode collisions, and
  aggregate tamper. Assert errors expose a stable reason code and relative path
  only.

  ```bash
  uv run pytest -q tests/consolidation/test_manifest.py
  ```

  Expected RED: missing `propose_manifest`/`canonical_manifest_bytes`.

- [ ] **Step 4: Implement the canonical manifest schema.**

  Each manifest must have exactly these top-level keys:

  ```text
  schema_version, component, source_repository, source_commit, source_tree,
  source_prefix, destination_prefix, policy, entries, aggregate_sha256
  ```

  Each entry must have exactly:

  ```text
  source_path, destination_path, git_blob, size, mode, sha256
  ```

  Sort entries by UTF-8 bytes of `destination_path`. Canonical JSON is UTF-8,
  `ensure_ascii=False`, `sort_keys=True`, `separators=(",", ":")`, and ends
  with one newline. Compute `aggregate_sha256` over canonical JSON bytes of the
  sorted `entries` array only. Read tree entries with `git ls-tree -rz` and
  blob bytes with `git cat-file`; never open a source-worktree path.

- [ ] **Step 5: Run focused GREEN checks.**

  ```bash
  uv run pytest -q tests/consolidation/test_authority.py tests/consolidation/test_manifest.py
  uv run python -m compileall -q packages/consolidation tests/consolidation
  git diff --check
  ```

  Expected: all tests pass, compile exits `0`, and no whitespace errors.

- [ ] **Step 6: Commit.**

  ```bash
  git add packages/consolidation tests/consolidation
  git commit -m "consolidation: add strict source manifest model"
  ```

### Task 3: Implement proposal, apply, verify, and canonical-root audit CLIs

**Files:**
- Create: `scripts/import_component_snapshot.py`
- Create: `scripts/verify_component_snapshot.py`
- Create: `scripts/audit_canonical_repo.py`
- Create: `tests/consolidation/test_import_component_snapshot.py`
- Create: `tests/consolidation/test_audit_canonical_repo.py`

**Interfaces:**
- Proposal:
  `import_component_snapshot.py propose --authority FILE --component NAME --output FILE`
- Apply:
  `import_component_snapshot.py apply --authority FILE --manifest FILE --root DIR`
- Verify:
  `verify_component_snapshot.py --authority FILE --manifest FILE --root DIR
  [--revision COMMIT]`
- Audit:
  `audit_canonical_repo.py --root DIR [--release] [--json]`
- Successful commands write no source file contents. Failures use one of:
  `E_ARGUMENT`, `E_AUTHORITY`, `E_GIT_OBJECT`, `E_POLICY`, `E_MANIFEST`,
  `E_DESTINATION`, `E_TAMPER`, `E_ROOT`, `E_NESTED_GIT`, `E_TRACKED_LINK`,
  `E_FORBIDDEN`, `E_REQUIRED`, `E_SOURCE_PATH`, `E_DIRTY`.

- [ ] **Step 1: Write RED CLI tests.**

  Cover proposal to an explicit new file; refusal to overwrite; apply to an
  empty exact destination only; manifest/source mismatch; source worktree
  mutation after approval; missing/extra/modified destination; destination
  symlink; hardlink (`st_nlink != 1`); group/world-writable files; second apply;
  and redacted stderr.

  For the root audit, prove rejection of a relative root, symlinked root,
  linked worktree `.git` file, common-dir outside root, nested `.git`, gitlink,
  tracked symlink, forbidden tracked name, missing lock/instruction files, and
  dirty `--release` mode.

  ```bash
  uv run pytest -q tests/consolidation/test_import_component_snapshot.py tests/consolidation/test_audit_canonical_repo.py
  ```

  Expected RED: three CLI files are absent.

- [ ] **Step 2: Implement proposal and apply.**

  `propose` loads one fixed component, evaluates its policy, writes a new
  canonical manifest using `O_CREAT|O_EXCL|O_NOFOLLOW` with mode `0600`, fsyncs
  the file and parent, then prints only component, file count, tree, and
  aggregate digest.

  `apply` re-resolves source commit/tree, re-computes and byte-compares the
  approved manifest, requires a nonexistent destination prefix below the
  canonical root, materializes blobs into a private temporary sibling using
  descriptor-relative no-follow opens, verifies exact bytes/modes/link count,
  and atomically renames it. No partial destination survives a rejected apply.

- [ ] **Step 3: Implement independent destination verification.**

  `verify_component_snapshot.py` must not call the apply path. It walks the
  destination without following symlinks, requires the exact relative-file set,
  validates regular-file type, `st_nlink == 1`, no group/world write, expected
  executable bit, size and SHA-256, and checks the source objects again.
  With `--revision`, it reads the destination from Git objects at that commit
  instead of the working tree. The root audit locates the unique commit that
  added `legacy/research-backend/pyproject.toml` or
  `apps/dashboard/package.json`, proves the component prefix was absent in its
  parent, and replays its manifest there. Later reviewed canonical path repairs
  are validated separately and never rewrite the import commit.

- [ ] **Step 4: Implement the audit in two modes.**

  Base mode validates standalone root, no nested Git/gitlinks/tracked links,
  authority schema, required component roots/lockfiles/AGENTS, manifests,
  imported bytes at each unique introduction commit, current forbidden tracked
  paths, and current source-path scan. `--release` additionally requires empty
  staged/unstaged/untracked status. `--json` returns exactly `schema_version`,
  `root`, `head`, `branch`, `status`, `components`, and `result`; paths inside
  errors remain root-relative.

- [ ] **Step 5: Run GREEN and help smoke.**

  ```bash
  uv run pytest -q tests/consolidation/test_import_component_snapshot.py tests/consolidation/test_audit_canonical_repo.py
  uv run python scripts/import_component_snapshot.py --help
  uv run python scripts/verify_component_snapshot.py --help
  uv run python scripts/audit_canonical_repo.py --help
  git diff --check
  ```

  Expected: tests pass, each help exits `0`, and none reads a source working
  tree or prints secret-bearing content.

- [ ] **Step 6: Commit.**

  ```bash
  git add scripts/import_component_snapshot.py scripts/verify_component_snapshot.py \
    scripts/audit_canonical_repo.py tests/consolidation
  git commit -m "consolidation: add snapshot import and root audit tooling"
  ```

### Task 4: Import the reviewed research-backend snapshot atomically

**Files:**
- Create generated: `ops/consolidation/backend-source-manifest.json`
- Create generated: `legacy/research-backend/**`
- Test: `tests/consolidation/test_backend_snapshot.py`

**Interfaces:**
- Backend policy includes tracked regular root `*.py`, `.gitignore`,
  `pyproject.toml`, `uv.lock`, `constraints-phase1.txt`, reviewed root Markdown,
  `exchange/**/*.py`, `db/**/*.py` except databases, and `tests/**`.
- It excludes `.keys.enc`, `.env*`, `.mode`, `.kill_switch`, `.dexter/**`,
  `.codegraph/**`, `.venv/**`, `.superpowers/**`, caches/bytecode,
  `decisions/**`, `memory/**`, `models/**`, `signals/**`, `reports/**`,
  runtime/generated `scratchpad/**` directories and scratchpad JSON/JSONL
  state, job artifacts, `run_status.json`, `live_prices.json`,
  `decisions_scored.jsonl`, `strategy.json`, `db/trading.db`, `deploy/**`,
  obsolete `scripts/**`, and the `reference/ml4t` gitlink. `.dexter/**`
  remains excluded, including all `.dexter/scratchpad/**` history.
- An exact regular root module named `scratchpad.py`, `memory.py`,
  `model_config.py`, or `signal_parser.py` is allowed; policy matches full
  paths/components and artifact extensions, never broad name substrings.

- [ ] **Step 1: Add RED snapshot-completeness tests.**

  Assert the fixed source identity/tree, required source modules and lockfile,
  forbidden families, no manifest globs, no gitlink, and exact reproduction by
  the verifier. Before import the test must fail because the manifest and
  destination are absent.

  ```bash
  uv run pytest -q tests/consolidation/test_backend_snapshot.py
  ```

- [ ] **Step 2: Generate the proposal twice and prove determinism.**

  ```bash
  umask 077
  uv run python scripts/import_component_snapshot.py propose \
    --authority ops/consolidation/source-authority.json \
    --component backend --output /tmp/backend-source-manifest.1.json
  uv run python scripts/import_component_snapshot.py propose \
    --authority ops/consolidation/source-authority.json \
    --component backend --output /tmp/backend-source-manifest.2.json
  cmp -s /tmp/backend-source-manifest.1.json /tmp/backend-source-manifest.2.json
  ```

  Expected: identical bytes and aggregate digest.

- [ ] **Step 3: Review every proposed path before approval.**

  Print only `source_path`, `destination_path`, mode, and size with `jq`; scan
  the list for every forbidden family; inspect executable entries separately.
  Reject any secret/runtime/generated/deploy/gitlink entry. After review, place
  the generated bytes at
  `ops/consolidation/backend-source-manifest.json` and set mode `0644`.

- [ ] **Step 4: Apply and independently verify.**

  ```bash
  uv run python scripts/import_component_snapshot.py apply \
    --authority ops/consolidation/source-authority.json \
    --manifest ops/consolidation/backend-source-manifest.json --root "$PWD"
  uv run python scripts/verify_component_snapshot.py \
    --authority ops/consolidation/source-authority.json \
    --manifest ops/consolidation/backend-source-manifest.json --root "$PWD"
  ```

  Expected: both report backend PASS; destination has no `.git`, symlink,
  hardlink, database, runtime state, model, report, signal, credential, or
  generated cache.

- [ ] **Step 5: Install only the locked test environment and run the safe
  backend suite.**

  ```bash
  cd legacy/research-backend
  uv sync --frozen --extra test
  uv run --frozen --extra test pytest -q
  cd ../..
  uv run pytest -q tests/consolidation/test_backend_snapshot.py
  ```

  Expected baseline: `204 passed, 2 skipped` for the imported backend and PASS
  for completeness. Any exchange/broker network initialization is a failure,
  not a skip candidate.

- [ ] **Step 6: Commit the snapshot and manifest atomically.**

  ```bash
  git add ops/consolidation/backend-source-manifest.json \
    legacy/research-backend tests/consolidation/test_backend_snapshot.py
  git diff --cached --check
  git commit -m "consolidation: import attested research backend snapshot"
  ```

### Task 5: Import the dashboard subtree atomically

**Files:**
- Create generated: `ops/consolidation/dashboard-source-manifest.json`
- Create generated: `apps/dashboard/**`
- Test: `tests/consolidation/test_dashboard_snapshot.py`

**Interfaces:**
- Source is only the `trading-agent/` subtree at the fixed dashboard commit.
- All tracked regular files are eligible except `.env*`, `.next/**`,
  `node_modules/**`, coverage, caches, logs, credentials, secret-named files,
  and runtime-data paths. Symlinks/gitlinks/special entries fail.

- [ ] **Step 1: Write RED subtree-completeness tests.**

  Assert exact source prefix/tree, required `package.json`, `package-lock.json`,
  `src`, `tests`, existing Next.js AGENTS rule, forbidden paths, and verifier
  reproduction. Run and observe missing manifest/destination failure.

- [ ] **Step 2: Generate twice, compare, and review exact paths.**

  ```bash
  umask 077
  uv run python scripts/import_component_snapshot.py propose \
    --authority ops/consolidation/source-authority.json \
    --component dashboard --output /tmp/dashboard-source-manifest.1.json
  uv run python scripts/import_component_snapshot.py propose \
    --authority ops/consolidation/source-authority.json \
    --component dashboard --output /tmp/dashboard-source-manifest.2.json
  cmp -s /tmp/dashboard-source-manifest.1.json /tmp/dashboard-source-manifest.2.json
  ```

  Review the exact entry list and executable modes; then place the approved
  bytes at `ops/consolidation/dashboard-source-manifest.json` with mode `0644`.

- [ ] **Step 3: Apply and verify.**

  ```bash
  uv run python scripts/import_component_snapshot.py apply \
    --authority ops/consolidation/source-authority.json \
    --manifest ops/consolidation/dashboard-source-manifest.json --root "$PWD"
  uv run python scripts/verify_component_snapshot.py \
    --authority ops/consolidation/source-authority.json \
    --manifest ops/consolidation/dashboard-source-manifest.json --root "$PWD"
  ```

  Expected: dashboard PASS and no nested `.git`, build output, dependency tree,
  credential, or runtime-data path.

- [ ] **Step 4: Install from lock and run pre-repair baseline.**

  ```bash
  cd apps/dashboard
  npm ci
  npm test
  ./node_modules/.bin/tsc --noEmit
  npm run lint
  npm run build
  cd ../..
  uv run pytest -q tests/consolidation/test_dashboard_snapshot.py
  ```

  Expected baseline: Node/integration tests pass (latest source baseline: 73
  Node tests), TypeScript/ESLint/build exit `0`, and completeness passes.

- [ ] **Step 5: Commit the snapshot and manifest atomically.**

  ```bash
  git add ops/consolidation/dashboard-source-manifest.json apps/dashboard \
    tests/consolidation/test_dashboard_snapshot.py
  git diff --cached --check
  git commit -m "consolidation: import attested dashboard snapshot"
  ```

### Task 6: Repair source-path presentation and centralize runtime paths

**Files:**
- Create: `tests/consolidation/test_absolute_source_paths.py`
- Modify: `legacy/research-backend/runtime_paths.py`
- Modify: `legacy/research-backend/README.md`
- Modify: backend callers that contain `.hermes` source/config defaults,
  including `broker.py`, `exchange/ccxt_bridge.py`, `main.py`,
  `alert_manager.py`, `adanos_collector.py`, `kalshi_collector.py`,
  `run_arena_round.py`, `sentiment_filter.py`, and `set_mode.py`
- Modify: `apps/dashboard/src/lib/trading/paths.ts`
- Modify: `apps/dashboard/src/lib/trading/collectors.ts`
- Modify: dashboard mode/update-stop/watchlist route path declarations
- Modify: `apps/dashboard/src/lib/trading/auth.ts`
- Modify: `apps/dashboard/src/lib/trading/kill-switch.ts`
- Modify: dashboard plan/settings operator-facing copy
- Modify: `apps/dashboard/tests/mode-auth.integration.sh`
- Create: `apps/dashboard/tests/trading-paths.test.mjs`

**Interfaces:**
- Backend `runtime_paths.py` owns `data_root()`, `reports_dir()`,
  `signal_output_dir()`, `mode_file()`, `kill_switch_file()`, and
  `configured_env_file()`.
- Dashboard `paths.ts` owns `researchDataRoot()` plus derived report, decision,
  memory, mode, and kill-switch paths.
- Defaults are external runtime paths below
  `~/.local/share/trading-agent`; a protected environment variable may
  override them. No default points at `.hermes`, `codex-worktrees`, the
  canonical checkout, or a component source directory.

- [ ] **Step 1: Add a RED repository source-path scan.**

  Scan tracked executable source, test scripts, build/provisioning config, and
  current operator instructions. Reject `/home/thenam176/.hermes`,
  `~/.hermes`, `.local/share/codex-worktrees`,
  `/home/thenam176/projects/trading-dashboard`, and
  `/home/thenam176/projects/trading-agent-migration`. Historical audit and
  phase evidence under `docs/implementation`, `.superpowers`, imported audit
  samples, and this provenance plan are documented exclusions; they are not
  executable authority.

  ```bash
  uv run pytest -q tests/consolidation/test_absolute_source_paths.py
  ```

  Expected RED: current backend/dashboard hard-coded paths are listed as
  root-relative failures.

- [ ] **Step 2: Centralize backend runtime resolution without loading secrets
  by default.**

  `configured_env_file()` returns `None` unless `TRADING_ENV_FILE` is set;
  callers do not search `.env` files. `mode_file()` and
  `kill_switch_file()` derive only from explicit `TRADING_MODE_FILE` /
  `TRADING_KILL_SWITCH_PATH` or the external runtime root. Remove
  `_ensure_scripts_path()` and the implicit `~/.hermes/scripts` import; the
  optional legacy logger is used only through an explicitly configured module
  or falls back to standard logging. Preserve strict worker behavior and all
  false-gate defaults.

- [ ] **Step 3: Centralize dashboard runtime resolution.**

  Replace duplicate `HOME/.hermes/crypto-research` construction with imports
  from `src/lib/trading/paths.ts`. Ensure the module is server-only where it
  reads environment. Replace UI commands that tell operators to `cd` into a
  legacy source checkout with neutral labels for the configured external data
  root and Control/Job API operations. Never show a credential filename as a
  setup instruction.

- [ ] **Step 4: Make dashboard integration self-contained.**

  Remove absolute `PYTHONPATH` and `.venv/bin/python` references from
  `mode-auth.integration.sh`. When the test needs kill-switch parity, invoke
  the imported backend with `python3` and `PYTHONPATH` resolved relative to the
  canonical root, using only its isolated temporary mode/kill paths. It must
  not read the real runtime root.

- [ ] **Step 5: Run focused GREEN checks.**

  ```bash
  uv run pytest -q tests/consolidation/test_absolute_source_paths.py
  cd legacy/research-backend && uv run --frozen --extra test pytest -q && cd ../..
  cd apps/dashboard && npm test && ./node_modules/.bin/tsc --noEmit && npm run lint && npm run build && cd ../..
  git diff --check
  ```

  Expected: path scan passes; backend remains `204 passed, 2 skipped` unless a
  test-count-only change is explicitly explained; dashboard test/type/lint/
  build all pass. No command contacts an exchange or active mutation route.

- [ ] **Step 6: Commit.**

  ```bash
  git add legacy/research-backend apps/dashboard tests/consolidation/test_absolute_source_paths.py
  git commit -m "consolidation: centralize external runtime paths"
  ```

### Task 7: Remove repository links and add root orchestration

**Files:**
- Delete: `crypto-research`
- Delete: `legacy-trading-agent`
- Delete: `trading-dashboard`
- Modify: `AGENTS.md`
- Create: `legacy/research-backend/AGENTS.md`
- Modify: `apps/dashboard/AGENTS.md`
- Modify: `README.md`
- Modify: `Makefile`
- Test: `tests/consolidation/test_repository_shape.py`

**Interfaces:**
- Root Make targets: `audit`, `audit-release`, `check-contracts`, `test-core`,
  `test-backend`, `test-dashboard`, `typecheck-dashboard`, `lint-dashboard`,
  `build-dashboard`, `test-all`.
- `test-all` does not start servers or mutate runtime. Dashboard build remains
  a separate explicit target because it writes ignored `.next` output.

- [ ] **Step 1: Write RED repository-shape tests before deleting links.**

  Assert exactly one `.git` directory, no `.git` files below root, no gitlinks,
  no tracked symlinks, required component lockfiles/instructions, no Python
  import crossing from core to `legacy/research-backend`, and Make targets.

  ```bash
  uv run pytest -q tests/consolidation/test_repository_shape.py
  uv run python scripts/audit_canonical_repo.py --root "$PWD"
  ```

  Expected RED: the three existing tracked repository symlinks are reported
  with `E_TRACKED_LINK`.

- [ ] **Step 2: Remove only the three tracked repository symlinks.**

  ```bash
  git rm crypto-research legacy-trading-agent trading-dashboard
  ```

  Do not follow them, inspect their targets recursively, or remove anything in
  the old repositories.

- [ ] **Step 3: Replace root instructions and project map.**

  `AGENTS.md` must describe the one-root component boundaries, paper-only
  safety, component-local dependency commands, runtime/source separation, and
  ask-first rules for production/root/remote changes. `README.md` must show the
  canonical tree, component authority, local setup, safe validation commands,
  and explicitly separate Release Authority v2 and cutover as later plans.
  `legacy/research-backend/AGENTS.md` must require Python 3.11, its own
  `uv.lock`, flat imports, paper mode, no exchange probe, no dependency merge,
  and no in-process core import. Extend the imported dashboard AGENTS file
  while preserving its Next.js 16 local-doc rule; require the dashboard's own
  lockfile, server-only external data access, and no production mutation route.

- [ ] **Step 4: Add exact Make orchestration.**

  Use recipes equivalent to:

  ```make
  audit:
	uv run python scripts/audit_canonical_repo.py --root "$(CURDIR)"
  audit-release:
	uv run python scripts/audit_canonical_repo.py --root "$(CURDIR)" --release
  test-core:
	uv run pytest -q --ignore=legacy/research-backend --ignore=apps/dashboard
  test-backend:
	cd legacy/research-backend && uv run --frozen --extra test pytest -q
  test-dashboard:
	cd apps/dashboard && npm test
  typecheck-dashboard:
	cd apps/dashboard && ./node_modules/.bin/tsc --noEmit
  lint-dashboard:
	cd apps/dashboard && npm run lint
  build-dashboard:
	cd apps/dashboard && npm run build
  test-all: audit check-contracts test-core test-backend test-dashboard typecheck-dashboard lint-dashboard
  ```

  Keep existing `generate-contracts` and `check-contracts`. Declare all targets
  `.PHONY`. No target installs dependencies, changes lockfiles, starts a server,
  applies a migration, or invokes a broker.

- [ ] **Step 5: Run GREEN shape/orchestration checks.**

  ```bash
  uv run pytest -q tests/consolidation/test_repository_shape.py
  make audit
  make check-contracts
  make test-all
  make build-dashboard
  git diff --check
  ```

  Expected: all commands pass. `git status --short` may show only this task's
  intended tracked changes; dependency/build directories remain ignored.

- [ ] **Step 6: Commit.**

  ```bash
  git add AGENTS.md README.md Makefile legacy/research-backend/AGENTS.md \
    apps/dashboard/AGENTS.md tests/consolidation/test_repository_shape.py
  git add -u crypto-research legacy-trading-agent trading-dashboard
  git commit -m "repo: establish one-root component boundaries"
  ```

### Task 8: Complete root audit, tamper, and source-equivalence evidence

**Files:**
- Modify: `tests/consolidation/test_audit_canonical_repo.py`
- Create: `docs/consolidation/source-equivalence.md`
- Create: `docs/consolidation/known-limitations.md`
- Create: `docs/consolidation/rollback.md`

**Interfaces:**
- Evidence records command, timestamp, exit status, high-level result, and
  commit/tree/digest identities. It never embeds secrets, DSNs, tokens,
  process environments, or source file contents.
- Tamper tests use isolated temporary copies only; canonical files and old
  repositories are never modified for a drill.

- [ ] **Step 1: Add final RED audit cases.**

  Add isolated-copy tests for one missing, one extra, and one modified imported
  file; changed manifest aggregate; authority commit/tree mismatch; nested Git;
  tracked forbidden file; dirty release mode; and redaction. Each must fail
  with its stable code, then leave the real canonical tree untouched.

- [ ] **Step 2: Run the complete component matrix from the canonical root.**

  ```bash
  make audit
  make check-contracts
  make test-core
  make test-backend
  make test-dashboard
  make typecheck-dashboard
  make lint-dashboard
  make build-dashboard
  uv run pytest -q tests/consolidation
  ```

  Expected current baselines: core suite `668 passed` with only the known
  warning if still applicable; backend `204 passed, 2 skipped`; dashboard Node
  baseline 73 tests plus integration, TypeScript, ESLint, and Next production
  build all pass. Record actual fresh counts; any regression is a blocker.

- [ ] **Step 3: Re-verify both manifests and Git shape.**

  ```bash
  BACKEND_IMPORT=$(git log --diff-filter=A --format=%H -- legacy/research-backend/pyproject.toml)
  DASHBOARD_IMPORT=$(git log --diff-filter=A --format=%H -- apps/dashboard/package.json)
  test "$(printf '%s\n' "$BACKEND_IMPORT" | sed '/^$/d' | wc -l)" -eq 1
  test "$(printf '%s\n' "$DASHBOARD_IMPORT" | sed '/^$/d' | wc -l)" -eq 1
  uv run python scripts/verify_component_snapshot.py \
    --authority ops/consolidation/source-authority.json \
    --manifest ops/consolidation/backend-source-manifest.json --root "$PWD" \
    --revision "$BACKEND_IMPORT"
  uv run python scripts/verify_component_snapshot.py \
    --authority ops/consolidation/source-authority.json \
    --manifest ops/consolidation/dashboard-source-manifest.json --root "$PWD" \
    --revision "$DASHBOARD_IMPORT"
  test "$(find . -mindepth 2 -name .git -print -quit)" = ""
  test "$(git ls-files -s | awk '$1 == 120000 || $1 == 160000 {print; exit}')" = ""
  ```

  Expected: each component has one atomic introduction commit, both original
  snapshots reproduce there, and no nested Git, symlink, or gitlink entry
  exists in current HEAD. Canonical path-repair deltas remain covered by the
  current-tree path, boundary, contract, and component suites.

- [ ] **Step 4: Prove the old repositories did not move.**

  Compare each old repository's current `HEAD`, tree, branch and porcelain
  state with Task 0's checkpoint. The core source may contain only the already
  approved docs commits captured at Task 0; backend/dashboard must remain at
  the fixed commits with empty porcelain. Any new change is investigated and
  is not silently reset.

- [ ] **Step 5: Recapture Phase 4B and trading safety read-only.**

  Re-run Task 0 Steps 2 and 3. Expected: installed verifier PASS,
  `paper/paper`, `false/false`, kill switch unchanged, and SQLite `30/0`. Do
  not start services merely to make an observation available.

- [ ] **Step 6: Write equivalence, known-limitations, and rollback records.**

  Equivalence must map every acceptance criterion to evidence. Known
  limitations must explicitly retain: separate dependency environments,
  flat legacy backend, external runtime data, old repos retained read-only,
  no Release Authority v2, and no production cutover. Rollback is selecting
  the old source/release authority and ceasing use of this branch; it does not
  delete/reset any repository or change runtime.

- [ ] **Step 7: Commit evidence.**

  ```bash
  git add tests/consolidation/test_audit_canonical_repo.py docs/consolidation
  git diff --cached --check
  git commit -m "docs: record canonical source equivalence"
  ```

### Task 9: Final clean-release gate and handoff

**Files:**
- Modify only if evidence results require correction:
  `docs/consolidation/source-equivalence.md`

**Interfaces:**
- Produces one clean canonical commit and one explicit GO/NO-GO decision.
- Does not create Release Authority v2 metadata, stage a release, install,
  start, deploy, push, archive, or cut over.

- [ ] **Step 1: Require an empty canonical worktree.**

  ```bash
  git status --short
  git diff --check HEAD
  make audit-release
  ```

  Expected: empty status; diff and release audit exit `0`.

- [ ] **Step 2: Re-run the non-mutating aggregate gate at HEAD.**

  ```bash
  make test-all
  ```

  Expected: audit, contracts, all three component tests, dashboard TypeScript
  and ESLint pass at the exact clean `HEAD`. The already-run production build
  result is referenced from Task 8.

- [ ] **Step 3: Record final identity.**

  ```bash
  git rev-parse HEAD HEAD^{tree}
  git branch --show-current
  git rev-parse --show-toplevel
  git rev-parse --path-format=absolute --git-common-dir
  git remote -v
  ```

  Expected branch `codex/canonical-monorepo`, top-level canonical path,
  common-dir canonical `.git`, and only local `migration-source`.

- [ ] **Step 4: Issue the decision.**

  Use `GO FOR MONOREPO RELEASE AUTHORITY V2` only if every Task 0–9 gate is
  green and evidence is committed at a clean HEAD. Otherwise use
  `NO-GO — CANONICAL SOURCE OR EQUIVALENCE BLOCKERS REMAIN` with exact failed
  gate(s). Neither phrase authorizes implementation of Plan 2 or any cutover.

## Self-Review Checklist Before Execution

- [ ] Every approved design section is covered: prerequisite, standalone Git,
  provenance, source-only policies, separate locks, path boundaries, audit,
  tests, rollback, and GO/NO-GO.
- [ ] The writing-plans placeholder scan finds no unfinished marker,
  angle-bracket placeholder, or three-dot omission in this plan.
- [ ] All repository paths, commits, trees, stage digest, branch, manifest
  names, commands, expected outcomes, and commit messages are concrete.
- [ ] Plan 1 contains no Release Authority v2 implementation and no root,
  runtime, service, database, dashboard deployment, or remote mutation.
- [ ] A Phase 4B failure stops before creation of
  `/home/thenam176/projects/trading-agent`.
- [ ] Backend/dashboard imports are separate atomic provenance commits, and old
  repositories remain unchanged.
- [ ] Final release audit runs only at a clean canonical HEAD.

## Execution Handoff

After this plan is committed, execute it with one of:

1. **Subagent-Driven (recommended):** use
   `superpowers:subagent-driven-development`, one fresh worker per task, with
   spec-compliance and code-quality review before proceeding.
2. **Inline Execution:** use `superpowers:executing-plans` in a dedicated
   execution session, following tasks sequentially and stopping at each gate.

Do not begin either mode until Task 0's Phase 4B prerequisite is independently
green.
