# WS-01C Task 6 — private compiler temporary directory

## Outcome

The offline Bubblewrap build contract now creates `stage/compiler-tmp` with
mode `0700` and passes that directory as `TMPDIR`, `TEMP`, and `TMP` through
Bubblewrap's cleared environment. The host root is still `--ro-bind / /`, the
stage is still the only writable bind, and `--unshare-net` remains required.
The explicit private LLVM/Cargo tool paths and no-ambient-fallback policy are
unchanged.

## TDD evidence

- RED: `uv run --frozen pytest -q
  tests/foundation/test_nautilus_engine_build.py::test_bubblewrap_build_boundary_uses_a_private_writable_compiler_tempdir`
  failed with `AttributeError` because `_stage_compiler_temp_environment` did
  not yet exist.
- GREEN: the same regression passed after the minimal implementation.
- Final focused suite: `TMPDIR=/tmp TEMP=/tmp TMP=/tmp uv run --frozen pytest
  -q tests/foundation/test_nautilus_engine_build.py` — `14 passed`.

The new regression runs the real Bubblewrap boundary. It writes a simulated
compiler object below `stage/compiler-tmp`, verifies all three temporary
environment variables select that path, and verifies an attempted write below
host `/tmp` raises an OS error. It would fail if the stage temp directory,
one of the environment variables, or the read-only host-root contract were
removed.

## Offline input and rebuild evidence

Before the rebuild, these existing private inputs were verified offline:

- Task 2 source/Cargo cache: PASS.
- Task 4 wheel cache, approved manifest SHA-256
  `0a7693f27a384925698dde2818abd70b894524bae341a62de0ef8f17500d108b`: PASS.
- Task 5 LLVM cache and materialized toolchain: PASS.
- Private Cargo and Rustc reported 1.95.0.

The real `make build-nautilus-engine` run used only those inputs, explicit
CPython 3.12, and `/usr/bin/bwrap`; it used the existing offline mode and a
new external artifact destination. It reached `ring@0.17.14` compilation and
did not contain the former `clang: error: unable to make temporary file:
Read-only file system` failure. The run then failed at the separate existing
private-LLVM sysroot limitation, `fatal error: 'stddef.h' file not found`.
No artifact destination was published. This task intentionally does not alter
the toolchain or host read-only contract to address that unrelated failure.

## Safety and scope

No network, package index, broker, exchange, account, database, service,
runtime authority, lockfile, or production dependency was touched. The failed
external build stage was automatically removed by the builder.
