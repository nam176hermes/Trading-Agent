# Canonical Trading Agent Monorepo Design

**Status:** Approved by the operator on 2026-07-13

**Goal:** Establish `/home/thenam176/projects/trading-agent` as the single
standalone Git root and source authority for the control plane, research
backend, and dashboard without rewriting proven behavior, copying runtime
state into Git, or changing the active paper-trading runtime during source
consolidation.

## Decision

The canonical project is a new standalone clone of the existing
`trading-agent-migration` Git history, not a blank repository and not another
linked worktree. It starts from application/ops commit
`d9d46fa363f26bd78f5560300d26913494e11e4d` on a new
`codex/canonical-monorepo` branch. The research backend and dashboard are
imported as reviewed source snapshots from exact commits, each in its own
atomic commit with a machine-verifiable provenance manifest.

The operator approved advancing the original
`f5db6604ccf709066ce6631d66e2fbf971ee1d72` authority on
2026-07-13 after Phase 4B provisioning exposed two verifier defects. The new
authority contains only the approved design/plan documentation, reviewed
permission-safe verifier corrections, their regression tests, and audit
evidence above that original application/ops baseline.

The canonical repository has one `.git` directory, one HEAD, one status, and
one release source commit. It does not use repository symlinks, nested Git
repositories, or submodules. The old repositories remain unchanged and
read-only as provenance and rollback sources until a later archival decision.

## Current authority and prerequisite gate

The consolidation source identities are:

- application and ops history:
  `d9d46fa363f26bd78f5560300d26913494e11e4d`;
- sealed Phase 4B application release:
  `fdc085a05019d700ccbce59370941e2c97ef899a`;
- research backend:
  `41f055b48033714c660f44cc20498b7545366e75`;
- dashboard:
  `ca57a7e018eb3afdc263e40b343b7ebbe3f8ccbb`;
- sealed Phase 4B metadata:
  `f1ed595c86df4cc7ac7274272dd00798ddb497a98ca3a12a1ed9680769468d7c`.

Phase 4B runtime provisioning must be completed and independently verified
before creating the canonical repository. The current partial `/opt` install
and stale `/run/trading-agent-phase4-provision.63GKAy` snapshot are not an
acceptable consolidation baseline. Cleanup of that exact root-owned residue
requires explicit operator approval. The gate passes only when:

1. no provisioning process is running;
2. the stale `/run` snapshot is absent;
3. `ops/phase4b/verify-installed.sh` passes against the sealed stage;
4. requested and effective mode are `paper`;
5. `LIVE_EXECUTION_ENABLED=false` and `LIVE_TRADING_APPROVED=false`;
6. the active order/trade evidence has not drifted because of provisioning;
7. the three source worktrees are clean at the exact commits above.

No consolidation task may repair, bypass, or reinterpret a failed Phase 4B
gate.

## Scope

The consolidation includes:

- a standalone canonical Git root;
- source-only backend and dashboard imports;
- exact import provenance and forbidden-path enforcement;
- root-level audit, test, build, and contract-check commands;
- removal of the three repository symlinks from the canonical tree;
- replacement of source-worktree absolute paths with component descriptors or
  runtime configuration;
- a versioned monorepo release-authority format and offline staging dry-run;
- equivalence evidence for core, backend, dashboard, and immutable releases.

The consolidation does not:

- rewrite the research backend into a new package layout;
- combine the core and backend Python dependency environments;
- create an npm or uv workspace;
- import models, decisions, memory, reports, signals, credentials, databases,
  `.env`, `.keys.enc`, `.mode`, `.venv`, `node_modules`, `.next`, caches, or
  generated runtime artifacts;
- change PostgreSQL data, Alembic state, systemd state, port 3002, Cloudflare,
  scheduler state, live gates, strategy, risk policy, prompts, or models;
- install a monorepo-built release into `/opt`;
- switch the active dashboard or services to the canonical repository;
- push a remote branch or delete/archive an old repository.

Production cutover is a separate implementation plan after the source
consolidation and offline release-equivalence gates pass.

## Target repository structure

The existing control-plane layout remains stable. Imported components are
added at two explicit boundaries:

```text
/home/thenam176/projects/trading-agent/
├── .git/
├── AGENTS.md
├── README.md
├── Makefile
├── pyproject.toml
├── uv.lock
├── alembic/
├── apps/
│   ├── control_api/
│   ├── job_api/
│   └── dashboard/
│       ├── AGENTS.md
│       ├── package.json
│       ├── package-lock.json
│       ├── src/
│       └── tests/
├── services/
├── packages/
├── legacy/
│   └── research-backend/
│       ├── AGENTS.md
│       ├── pyproject.toml
│       ├── uv.lock
│       ├── main.py
│       ├── exchange/
│       └── tests/
├── generated/
├── ops/
│   ├── consolidation/
│   │   ├── source-authority.json
│   │   ├── backend-source-manifest.json
│   │   └── dashboard-source-manifest.json
│   ├── phase4b/
│   └── systemd/
├── scripts/
│   ├── import_component_snapshot.py
│   ├── verify_component_snapshot.py
│   └── audit_canonical_repo.py
└── tests/
    └── consolidation/
```

The backend stays flat inside `legacy/research-backend` so existing imports,
CLI behavior, working-directory assumptions, and result schemas are preserved
during the first consolidation. Packaging it as a normal Python package is a
later refactor with a separate design.

## Git and provenance model

### Canonical history

The canonical repository is created by a local standalone clone of the
migration repository and checked out at the exact application/ops commit. It
must report its own `.git` directory as `git rev-parse --git-common-dir`; a
`.git` file pointing at another worktree is rejected.

The local source repository may be retained as a read-only remote named
`migration-source`. No network remote is added or mutated without separate
operator approval.

### Snapshot imports

Unrelated histories are not merged. Each imported component is materialized
from an exact Git commit through a standard-library importer that reads Git
objects, not mutable working-tree files. The import produces an exact manifest
containing:

- schema version;
- component name;
- source repository identity;
- source commit and source tree ID;
- original and destination prefixes;
- one entry per imported regular file with source path, destination path,
  Git blob ID, byte size, mode, and SHA-256;
- a canonical aggregate SHA-256.

Apply consumes the reviewed manifest exactly. Extra, missing, changed,
duplicate, case-colliding, symlink, submodule, special, or writable executable
entries fail closed. Import never follows a working-tree symlink and never
reads an untracked file.

Byte equivalence is anchored at each component's atomic import commit. Later
canonical-only path/configuration repairs are ordinary reviewed commits and do
not rewrite that provenance point. The root audit replays the manifest against
the unique component-introduction commit, then separately validates the
current canonical delta through forbidden-path, boundary, contract, and
component test gates.

The old repositories preserve full historical context. The canonical import
commits preserve exact origin identities and bytes without bringing unrelated
Hermes or runtime history into the new authority.

## Component import policies

### Dashboard

Dashboard authority is the `trading-agent/` subtree at commit
`ca57a7e018eb3afdc263e40b343b7ebbe3f8ccbb`. It is imported to
`apps/dashboard/`.

Only Git-tracked regular files below that subtree are eligible. The importer
rejects `.env*`, `.next`, `node_modules`, coverage, log, cache, credential, and
runtime-data paths even if a later source commit accidentally tracks them. The
dashboard keeps its own `package-lock.json` and build commands.

The dashboard must stop presenting repository locations such as
`~/.hermes/crypto-research` as source-code locations. Server routes that still
need legacy runtime data consume the existing protected runtime configuration;
they do not resolve data relative to the monorepo checkout.

### Research backend

Backend authority is commit
`41f055b48033714c660f44cc20498b7545366e75`, imported to
`legacy/research-backend/`.

The reviewed manifest explicitly enumerates every imported file. Proposal
generation may select regular source, tests, locked dependency metadata, and
operator documentation, but apply accepts no globs. The following source
families are forbidden:

- `.keys.enc`, `.env*`, `.mode`, `.kill_switch`;
- `.dexter/`, `.codegraph/`, `.venv/`, caches and bytecode;
- `decisions/`, `memory/`, `models/`, `signals/`, `reports/`, runtime/generated
  scratchpad directories, scratchpad JSON/JSONL state, and job artifacts;
- `run_status.json`, `live_prices.json`, `decisions_scored.jsonl`, strategy
  state and other mutable JSON/JSONL state;
- sockets, FIFOs, devices, symlinks, hard-linked regular files and submodules.

The exact regular root source module `scratchpad.py` remains eligible, as do
modules such as `memory.py`, `model_config.py`, and `signal_parser.py`, because
policy evaluates exact paths, artifact extensions, and file types rather than
broad name substrings. `.dexter/scratchpad/**` remains runtime history and is
never source input.

The backend keeps its own `pyproject.toml`, `uv.lock`, Python 3.11 virtual
environment, test entrypoints, flat imports and research-only CLI contract.

### Core application

The existing `apps`, `services`, `packages`, `alembic`, `ops`, `generated`,
and root tests remain at their current paths. The tracked symlinks
`crypto-research`, `legacy-trading-agent`, and `trading-dashboard` are removed
only in the canonical branch, after both component snapshots and provenance
manifests verify.

Core code may not import Python modules from `legacy/research-backend` in
process. Communication remains through PostgreSQL, protected files, and the
existing Control API/Job API contracts.

## Dependency and build boundaries

The first canonical monorepo deliberately avoids a unified dependency graph:

- root `uv.lock` owns control-plane dependencies;
- `legacy/research-backend/uv.lock` owns the heavy research environment;
- `apps/dashboard/package-lock.json` owns Next.js dependencies.

Root commands orchestrate these isolated environments without silently
installing or updating dependencies. Lockfiles change only through their own
package managers and receive component-specific review.

The root Makefile exposes:

- `audit`: repository shape, provenance, forbidden files and clean authority;
- `check-contracts`: generated contract drift;
- `test-core`: complete core Python suite;
- `test-backend`: complete research-only backend suite;
- `test-dashboard`: dashboard Node and integration tests;
- `typecheck-dashboard`, `lint-dashboard`, and `build-dashboard`;
- `test-all`: the non-mutating aggregate gate.

No aggregate command starts a server, connects to an exchange, invokes a
broker, mutates PostgreSQL, runs a migration, changes systemd, or writes runtime
research output.

## Runtime and path boundaries

Source checkout paths are not runtime authority. Production continues to use:

- immutable releases under `/opt/trading-agent-phase4`;
- protected configuration and authority under `/etc/trading-agent`;
- user runtime state under `/home/thenam176/.local/share/trading-agent` and
  `/home/thenam176/.local/run/trading-agent`;
- canonical operational state in PostgreSQL at the configured loopback
  endpoint.

Absolute source-worktree paths are removed from source build/provisioning
configuration. Intentional runtime defaults receive centralized configuration
constants and tests proving they do not point into `.hermes`,
`codex-worktrees`, or the canonical source checkout.

The dashboard may display runtime locations only when they are operator-facing
runtime facts. It must not instruct operators to execute source code from the
old repositories.

## Release authority v2

The current sealed Phase 4B schema remains immutable and is never reinterpreted
as a monorepo release. Monorepo staging introduces a versioned authority schema
whose component identities share one source commit and bind exact source
prefixes:

| Field | Required value or validation |
|---|---|
| `schema_version` | integer `2` |
| `source_commit` | lowercase Git commit matching `^[0-9a-f]{40}$` |
| `components.application.source_prefix` | exact string `.` |
| `components.backend.source_prefix` | exact string `legacy/research-backend` |
| `components.dashboard.source_prefix` | exact string `apps/dashboard` |
| each `source_tree` | lowercase Git tree ID matching `^[0-9a-f]{40}$` and resolving at `source_commit:source_prefix` |

The actual schema additionally retains the current manifest file digests,
canonical content digests, interpreter identities, command authority, unit
hashes, stage-path binding, staging identity and standalone-verifier hash.
JSON Schema and strict tests reject unknown keys.

The release builder accepts one canonical repository and one commit. Component
descriptors select exact Git subtrees. The application release policy excludes
`apps/dashboard` and `legacy/research-backend`; backend export strips its source
prefix so the immutable release preserves the current flat runtime layout.
Dashboard packaging is attested independently and is not installed by the
source-consolidation plan.

An offline staging dry-run must reproduce application and backend behavior and
pass the standalone verifier. It creates a new stage and new metadata; it does
not overwrite, mutate, delete, install, or revoke the existing sealed stage.

## Canonical audit contract

`scripts/audit_canonical_repo.py` is a read-only, standard-library command. It
fails unless all of these are true:

1. the requested root is absolute, non-symlinked and owns a standalone `.git`
   directory;
2. Git top-level and common-dir resolve inside that root;
3. no nested `.git`, Git submodule, tracked symlink or repository-link path
   exists;
4. the three source-authority commits and tree IDs match, and each component
   manifest reproduces the imported bytes at its unique atomic introduction
   commit;
5. no forbidden secret, dependency, build, cache or runtime path is tracked;
6. component lockfiles and required instruction files exist;
7. release mode requires a clean index and worktree;
8. the audit output contains one repository HEAD/status and separate component
   test results, never three repository statuses.

Errors use stable reason codes and paths relative to the canonical root. They
do not print file contents, environment values, credentials, DSNs, tokens or
absolute legacy secret paths.

## Testing strategy

All behavior changes use RED-GREEN-REFACTOR. Test groups include:

- importer acceptance of exact regular Git blobs;
- missing, extra, modified, duplicate, traversal and case-collision rejection;
- symlink, submodule, special-file, hardlink and forbidden-path rejection;
- source worktree mutation after manifest approval;
- deterministic manifest bytes and aggregate digest;
- standalone-root, nested-Git and linked-worktree rejection;
- backend import completeness against its approved manifest;
- dashboard subtree completeness against its approved manifest;
- absolute source-path scans;
- core contract and runtime-release suites;
- backend research-only and no-execution suites;
- dashboard Node tests, security integration, TypeScript, ESLint and production
  build;
- monorepo release schema v2, component subtree identity, tamper and offline
  staging tests.

The migration records the pre-import and post-import test matrices. Existing
failures are not normalized away; any unexpected regression stops the import
before release-equivalence work.

## Implementation-plan decomposition

This program is implemented through three plans, not one cross-cutting plan:

1. **Canonical source consolidation:** prerequisite evidence, standalone root,
   importer, backend/dashboard snapshots, root audit, path repair and complete
   component equivalence. It does not change release metadata or runtime.
2. **Monorepo release authority v2:** component descriptors, schema v2,
   release-builder changes, verifier compatibility and a fresh offline stage.
   It does not install or start that stage.
3. **Production cutover:** root provisioning, service/dashboard deployment,
   smoke, rollback drill and source-repository archival decision. It requires
   separate operator approval for every root or runtime mutation.

Each plan has its own spec gate, task-level implementation plan, fresh tests,
commits and GO/NO-GO decision. Failure in one plan cannot be waived by a later
plan.

## Delivery sequence and review gates

1. **Runtime prerequisite:** complete and verify Phase 4B; recapture paper and
   false/false safety evidence.
2. **Source checkpoint:** record all four authority commits, tree IDs, statuses
   and old-repository locations without reading secrets.
3. **Canonical root:** create the standalone clone and prove one-root audit
   failures before implementing the audit tool.
4. **Import framework:** build and test deterministic proposal, review, apply
   and verify flows.
5. **Backend import:** approve exact manifest, import one atomic snapshot,
   install from its lockfile and run its complete safe suite.
6. **Dashboard import:** approve exact manifest, import one atomic snapshot and
   run Node, integration, type, lint and build gates.
7. **Root integration:** remove repository symlinks, add nested instructions,
   root commands and path scans; keep component environments isolated.
8. **Contract/path repair:** replace old source locations and prove runtime
   configuration remains external and fail-closed.
9. **Release authority v2:** in the second plan, implement test-first, build a
   fresh offline stage and independently verify it without installation.
10. **Release equivalence:** in the second plan, compare component manifests,
    contracts, release commands and safety invariants; publish its GO/NO-GO
    record.

Each numbered gate is independently reviewable and committed. A failed gate
stops the sequence; later tasks may not weaken or skip it.

## Rollback

Source consolidation does not change the active runtime, so rollback is a Git
and workspace selection decision:

- stop using the canonical branch;
- retain its failed evidence for diagnosis;
- continue using the existing sealed Phase 4B releases and old source
  repositories;
- do not reset, clean, overwrite or delete any old worktree;
- do not point a service or dashboard at the canonical checkout.

Imported snapshot commits are reverted only by new commits. Published
provenance manifests are not rewritten. A failed offline monorepo stage is
marked rejected and retained or removed only under the existing sealed-stage
policy; it never changes current release authority.

## Acceptance decisions

The first plan is `GO FOR MONOREPO RELEASE AUTHORITY V2` only when:

- Phase 4B is installed and independently verified;
- `/home/thenam176/projects/trading-agent` is a standalone, clean, single Git
  root;
- the old repositories remain unchanged at their checkpoint identities;
- component manifests reproduce exact approved source snapshots;
- no forbidden runtime, secret, dependency or generated-build paths are
  tracked;
- all core, backend and dashboard verification commands pass;
- contract drift and absolute source-path scans pass;
- paper mode, both false live gates and no-order invariants remain true.

Otherwise the decision is `NO-GO — CANONICAL SOURCE OR EQUIVALENCE BLOCKERS
REMAIN`.

The second plan is `GO FOR A SEPARATE MONOREPO PRODUCTION CUTOVER PLAN` only
when release-authority v2, a fresh offline stage and independent verifier all
pass without changing installed authority. Otherwise it is `NO-GO — MONOREPO
RELEASE AUTHORITY OR OFFLINE STAGING BLOCKERS REMAIN`.

No outcome of the first or second plan enables live trading or authorizes
production cutover.
