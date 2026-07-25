# Foundation paper-release exclusion

## Canonical contract

The canonical artifact class is `CANONICAL_PAPER_V1`, authority schema 3. Its authority components are exactly `application` and `backend`.

The command contract is:

```text
job_type=SNAPSHOT
entrypoint=paper_main.py
shell=false
environment_policy=CANONICAL_PAPER_CHILD_V1
dependency_policy=PYTHON_STDLIB_ONLY
argv=<backend-python3.11> -I -B paper_main.py
```

The stage also contains generated service units bound by the authority. It does not contain a dashboard component, Node runtime, npm metadata, migration graph, or another executable catalog.

## Construction path

1. `build-stage.sh` accepts an exact clean Git commit, pinned CPython archive, dedicated sealed wheelhouse, pinned `uv`, and prior authority digest.
2. It verifies safe metadata and code-owned digests for every external input before reading or executing it. The verified `uv` bytes are copied into the private build root.
3. `project-paper-application` and `project-paper-backend` apply exact positive mappings. No broad source export becomes runtime authority.
4. `project-python-runtime-archive` verifies the archive SHA-256 before safe extraction and projects CPython 3.11.15 into both components.
5. The private pinned `uv` runs `export --frozen` against the dedicated paper-application lock.
6. The same private binary runs `pip sync` into the projected application interpreter with hashes, strict resolution, binary-only wheels, no index, no cache, no Python download, and copy link mode.
7. The canonicalizer restores source wheel `RECORD` bytes, removes allowed installer metadata, and compares every installed file with `dependency-manifest.json`.
8. The builder removes every application bin entry except `python3.11`. Backend bin policy is also exactly `python3.11`.
9. Runtime, application, backend, source, unit, command, lock, dependency, and build-tool inspections run before authority composition.
10. All staged directories become `0555`; all regular files become `0444` or `0555`. Links, hard-linked regular files, special files, bytecode, and cache directories are rejected.
11. The standalone verifier checks the built bytes and its independent dependency provenance constant before the builder reports success.

## Runtime provenance

The accepted archive is:

```text
CPython 3.11.15
astral-sh/python-build-standalone release 20260414
x86_64-unknown-linux-gnu
install_only_stripped
archive_sha256=b702a19b26cbd007abf9ccbaa45dfdff99e9dbd646d89c9f3c9bb7b501aea44f
normalized_core_sha256=39632162b32a97b4ccd3f3dd5f79d0735137f9247401835d1287b433dc83dcf7
```

Archive extraction rejects path traversal, duplicate normalized names, hard links, device and special entries, escaping symbolic links, PAX metadata, sparse metadata, oversized members, and oversized aggregate payloads. Projection normalizes directory modes to `0700` before sealing, so the result does not depend on ambient umask.

Identity probes use `-I -B`. The `-B` flag is required because isolated mode ignores `PYTHONDONTWRITEBYTECODE`; omitting it generated runtime bytecode after cleanup and correctly caused sealed-stage composition to fail.

## Application dependency authority

The dedicated lock is:

```text
path=packages/runtime_release/paper_application/uv.lock
sha256=a4fac2d6f0587c534555e6d8c3ca9c22460ba18b09e5eb684c7b38409ce2d759
resolved_packages=19
```

The sealed wheelhouse checkpoint is:

```text
wheel_count=16
aggregate_sha256=6871c43d484d58d6fd3b17c10357830fa4284cdcb6489968eaf3d4e348fc311d
```

The build-tool and installed-file checkpoints are:

```text
uv_identity=uv 0.11.7 (x86_64-unknown-linux-gnu)
uv_sha256=cd952ca51e2c730e848a45c4e0dfb58926d79d90550b6a5feb5543b43d3248b4
dependency_manifest_sha256=a98d670fe49964f71aabb9be3daaeb062412452329a72a6616e0f4f40681cba6
installed_file_count=546
installed_file_set_sha256=d5e97e6843205315334f0665badfd75e58ef6893af033ca9cbdd7155df89b1aa
provenance_file_set_sha256=687b409c91b40ed2293e09bca8bab1d53779fb58425c8ab56d29e459ec209603
```

Direct dependencies:

```text
fastapi
psycopg
psycopg-pool
pydantic
uvicorn
```

Forbidden physical distributions:

```text
alembic
mako
sqlalchemy
greenlet
```

The final application environment contains the exact 16 installed distributions and 546 wheel-owned files resolved for Linux CPython 3.11. The backend contains no installed distribution. An added file fails both the composer and standalone verifier even when all mutable authority digests are recomputed.

## Application import closure

The projected application uses the complete job lifecycle needed by one `SNAPSHOT` job type:

```text
QUEUED
CLAIMED
RUNNING
SUCCEEDED
FAILED
BLOCKED
TIMED_OUT
CANCEL_REQUESTED
CANCELLED
```

A physical relocation smoke exposed an initial mismatch where the reduced enum omitted `CLAIMED` and `TIMED_OUT` while the projected transition graph required them. A RED regression now constructs the exact application projection and imports representative API, store, worker, process-runner, and transition modules with an isolated interpreter. `JobType` remains exactly `SNAPSHOT`.

## Backend boundary

The backend source allowlist contains four files:

```text
job_attribution.py
paper_main.py
paper_runtime_manifest.json
research_semantics.py
```

The backend inspector rejects extra files, unexpected imports, dynamic import primitives, order and credential symbols, changed manifests, argument-bearing entrypoints, and noncanonical commands. A fixed-command smoke without worker attribution must fail before reading semantic inputs or making a public data request.

## Physical proof requirements

A candidate is not releasable until all checks use the same source tree fingerprint:

```text
standalone verifier on the built stage
byte-for-byte content-copy relocation manifest parity
standalone verifier content-copy mode
CPython identity and prefix relocation probes for both components
application projected import closure
backend fixed-command fail-closed smoke
exact top-level and bin inventory
uniform owner and mode checks
zero symbolic links, hard links, and special files
exact application distribution inventory
exact application wheel-owned file inventory and pinned `uv` provenance
empty backend site-packages
zero temporary build-root references
zero dashboard, Node, npm, migration, broker, exchange, credential, or shell surfaces outside the bound runtime
```

Production promotion remains separate. It requires root ownership, the canonical installation path, protected runtime authority placement, and an independently approved release action. Package validation does not publish, activate, restart, migrate, or enable live execution.

## Failure behavior

Builder and verifier errors collapse to sanitized release rejection. Diagnostics remain local and private. There is no fallback to a broad application or legacy backend copy. Any source edit makes previous builder, relocation, seal, review, and artifact evidence stale.
