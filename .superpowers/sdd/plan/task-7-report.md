# WS-01C Task 7 — private Clang resource headers

## Outcome

The private LLVM policy now binds every direct regular file in the official
LLVM 22.1.3 `lib/clang/22/include` tree by relative path, byte size, and
SHA-256. The reviewed set contains 305 files totaling 15,744,747 bytes; no
archive symlinks or special entries occur in that tree. The materializer
extracts only those files plus the three previously approved tools beneath the
explicit private root. It seals headers and manifests as `0400`, tools and all
directories as `0500`, and publishes atomically.

Offline verification now requires the exact policy-bound file and directory
sets, direct owner-controlled entries, sealed modes, header hashes, the
policy-bound manifest, and the existing absolute-path compiler identities.
Missing, changed, mutable, symlinked, or unexpected resource entries fail
closed. Task 3 required no environment change: Clang derives
`lib/clang/22` from the already explicit private compiler path, and Task 3
continues to reject ambient `clang`, `clang++`, and `ld.lld` fallback.

## TDD and focused verification

- RED: the resource materialization regression failed with `invalid LLVM
  policy fields` because schema v1 modeled only compiler binaries.
- GREEN: the regression passed after the minimal schema, archive verification,
  materialization, and sealed-tree verification changes.
- RED: the committed-policy regression failed because the reviewed policy was
  still schema v1 and contained no resource records.
- GREEN: the policy regression passed after recording the 305 exact files
  derived from the already verified official archive.
- Focused suites: `TMPDIR=/tmp TEMP=/tmp TMP=/tmp uv run --frozen pytest -q
  tests/foundation/test_nautilus_llvm_toolchain.py
  tests/foundation/test_nautilus_engine_build.py` — `30 passed`.
- `git diff --check` — PASS.
- Ruff was unavailable from the frozen root environment (`uv run --frozen
  ruff ...` failed to spawn because no `ruff` executable is installed).

## Real private toolchain evidence

The existing verified official archive was republished under the schema-v2
policy into a new external sealed cache rather than modifying the prior cache
in place. This was required because adding the resource records changes the
policy digest and therefore the sealed cache and toolchain manifests.

- New sealed cache:
  `/home/thenam176/.cache/trading-agent/nautilus/llvm-22.1.3-resource-cache`.
- New sealed toolchain:
  `/home/thenam176/.cache/trading-agent/nautilus/llvm-22.1.3-resource-toolchain`.
- `--verify-toolchain` reported the pinned Clang, Clang++, and LLD 22.1.3
  identities and `nautilus LLVM toolchain offline verification: PASS`.
- A real compile probe invoked the absolute private `bin/clang` with an empty
  environment, a private compiler temporary directory, and `-nostdlibinc`.
  Its only angle-bracket search directory was the private
  `lib/clang/22/include`; a translation unit using `<stddef.h>`, `size_t`,
  `NULL`, and `max_align_t` compiled successfully. The verbose driver output
  showed `-resource-dir` resolving beneath the private toolchain root. No
  host/global Clang executable or system header directory was used.

The full engine candidate was not rerun in this task window. The direct probe
crossed the exact missing-`stddef.h` compiler boundary using the same private
relative resource-directory discovery that Task 3 receives.

## Safety and scope

No system compiler or package was installed or selected. No network, package
index, broker, exchange, account, order endpoint, database, service, runtime
authority, activation, lockfile, or production dependency was touched. The
private external cache/toolchain paths are build inputs only; no generated
compiler or engine artifact is stored in Git.
