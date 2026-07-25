# ADR: Canonical paper release boundary

- Status: Accepted
- Date: 2026-07-25
- Decision owner: Release and security review
- Artifact class: `CANONICAL_PAPER_V1`
- Authority schema: 3

## Context

The repository preserves live-capable legacy research code for audit and rollback. Runtime flags do not make those bytes appropriate for a canonical paper release. A configuration mistake must not reveal a broker adapter, credential loader, generic mode graph, alternate entrypoint, migration command, or dashboard runtime.

## Decision

The canonical stage uses a positive allowlist with two authority components:

```text
application
backend
```

Generated service units are bound metadata, not a third source component. The canonical stage does not contain the dashboard, Node, npm, the broad repository application tree, migration tooling, or live-capable legacy execution paths.

### Application component

The application is projected from `PAPER_APPLICATION_SOURCE_MAPPING`. The mapping contains only the Job API, job store, worker, safety evidence, runtime authority, and job-contract modules needed for the `SNAPSHOT` job plane.

The command catalog contains one job type:

```text
SNAPSHOT
```

The job lifecycle retains the states needed to claim, run, cancel, fail, time out, block, and complete that job. This lifecycle does not add another job type or execution mode.

Application dependencies come from:

```text
packages/runtime_release/paper_application/pyproject.toml
packages/runtime_release/paper_application/uv.lock
packages/runtime_release/paper_application/dependency-manifest.json
```

The direct dependency set is `fastapi`, `psycopg`, `psycopg-pool`, `pydantic`, and `uvicorn`. The frozen closure excludes Alembic, Mako, SQLAlchemy, and Greenlet. Installation is hash-bound, binary-only, offline, no-index, no-cache, and copy-only into the projected interpreter.

The builder accepts only `uv 0.11.7` with SHA-256 `cd952ca51e2c730e848a45c4e0dfb58926d79d90550b6a5feb5543b43d3248b4`. It copies those verified bytes into a private build root and runs only that copy. After installation, the builder restores each wheel-owned `RECORD`, removes the three allowed installer metadata files, and requires the physical `site-packages` file set to equal the source-owned dependency manifest. The authority and standalone verifier bind the resulting 546-file set independently of the stage digest.

### Backend component

The backend non-runtime source set is exactly:

```text
job_attribution.py
paper_main.py
paper_runtime_manifest.json
research_semantics.py
```

The fixed command is:

```text
<backend-python3.11> -I -B paper_main.py
```

The entrypoint accepts no command arguments. The backend dependency policy is `PYTHON_STDLIB_ONLY`, and backend `site-packages` must be empty.

### Runtime authority

Both components receive independently projected CPython runtimes with the same code-owned provenance:

```text
implementation=CPython
version=3.11.15
upstream=astral-sh/python-build-standalone
release=20260414
platform=x86_64-unknown-linux-gnu
variant=install_only_stripped
archive_sha256=b702a19b26cbd007abf9ccbaa45dfdff99e9dbd646d89c9f3c9bb7b501aea44f
normalized_core_sha256=39632162b32a97b4ccd3f3dd5f79d0735137f9247401835d1287b433dc83dcf7
```

The builder verifies the archive digest before extraction. Extraction rejects traversal, duplicate normalized names, hard links, special files, escaping links, PAX metadata, sparse metadata, and bounded-size violations. The projected runtime inspector verifies the normalized core before executing the interpreter.

Official internal runtime links may be materialized as regular files. If an inspector receives internal links, every link must resolve strictly inside the runtime root. Application source outside `.venv` cannot use symlinks.

### Child boundary

The child process starts from an empty environment allowlist and forces:

```text
TRADING_MODE=paper
LIVE_EXECUTION_ENABLED=false
LIVE_TRADING_APPROVED=false
LIVE_TRADING_ENABLED=false
```

The reviewed process runner uses a fixed executable, fixed argv, fixed cwd, `shell=false`, retained process identity, bounded output, and fail-closed safety rechecks.

### Source and relocation proof

Release Authority v2 binds every staged file, the exact projected source blobs, the complete Git source tree, both runtime cores, both interpreter identities, the application lock, dependency manifest provenance, pinned `uv` digest, backend manifest, generated units, and the standalone verifier.

A non-root relocation test must use the verifier's explicit content-copy fixture mode. Production verification additionally requires the root-owned canonical installation path. Content-copy mode still checks the complete entry manifest, source proof, dependency policy, runtime probes, units, command catalog, and authority digest.

## Consequences

- Changing live flags cannot reveal absent live modules or commands.
- The canonical release has no dashboard, Node, npm, migration graph, broker adapter, exchange adapter, credential loader, or generic mode selector.
- Application dependency changes require a dedicated lock, sealed wheelhouse, regenerated wheel-RECORD manifest, and verifier pin review.
- Any source mapping or enum contract change must pass isolated projected import-closure testing.
- Legacy live-capable files remain in Git with no staged runtime path.
- No statement in this ADR approves live trading, production promotion, service restart, or database mutation.

## Rejected alternatives

- Flags around a broad artifact: packaged bytes retain unnecessary authority.
- A copied host virtual environment: host provenance is not release provenance and relocation is unreliable.
- Root project lock reuse: it includes migration tooling outside the paper application closure.
- `uv sync` against the projected runtime: it can replace the projected interpreter with a host-managed environment.
- Keyword-only scanning: exact mappings, AST closure, physical file inspection, and runtime verification are stronger.
