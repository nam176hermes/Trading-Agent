# Job Plane Release Authority v2 Final Candidate

**Evidence date:** 2026-07-16
**Status:** `NO_BUILD — HERMETIC RELEASE STOP CONDITION`

## Result

No Release Authority v2 candidate was built. No application/backend staging
path, authority document, promotion record, release/command/semantic/aggregate
manifest digest, installed path, or tamper-test artifact exists for Part 2.

The current source is not a clean commit, but that is not the only blocker. A
read-only end-to-end builder review proved the current implementation cannot
produce the requested minimal hermetic Python 3.11 artifact even from a future
clean commit.

## Hermetic Python blocker

`ops/release-v2/build-stage.sh` creates ordinary `venv --copies` environments
and then requires `sys.base_prefix`, `sys.base_exec_prefix`, and the stdlib to
resolve below the stage. It never copies or extracts a complete Python base
runtime into the stage first, so its own invariant necessarily rejects the
available build approach.

Host observations:

- `/usr/bin/python3.11` is absent;
- `/usr/bin/python3` is 3.12 and incompatible;
- observed Python 3.11 and `uv` inputs are operator-owned;
- no reviewed CPython runtime archive, wheelhouse, first-party wheel, Hatchling
  closure, or approved file-set digest exists.

Copying or weakening paths to the operator-owned runtime would not meet the
reviewed authority requirements and was not attempted.

## Artifact-selection and provenance blockers

The existing builder/verifier mandate all three components and the complete
Git export. They require dashboard Node/npm/cache, `node_modules`, and `.next`
even though the requested runtime release is Job API/worker plus the approved
research backend command surface. App/backend-only selection is unsupported.

Additional blockers include:

- the builder requires a real `.git` directory, while the candidate is a linked
  worktree with a `.git` file;
- first-party non-editable wheel construction requires
  `hatchling==1.27.0`, whose build closure is not in either runtime lock;
- UV/build/cache identities and native-library closure are not authority
  inputs;
- final `.pth`, `pyvenv.cfg`, path-bound metadata/shebang, build/cache path,
  and post-move `sys.path` escape checks are incomplete;
- no successful real `build-stage.sh` test, relocation smoke, two-build byte
  equality proof, or network-denied build proof exists;
- runtime v2 loading and installed-tree attestation deliberately reject all
  candidates.

## Known source identities

```text
root lock:       5b8be053379eb1b5525c3f6a382e30c35237da961fd18becda4ea1208da26561
backend lock:    d09ea2e716a9635663dfc0366661080ad61032527bdbc9b01965f0f5287035bd
dashboard lock:  4729ace1014543f511a4045b3e802840118da322af4d373ca16d9c0fb47cf447
verifier:        43527dd2c0f0c11c722c93c0cc28e1c92637d275489ed4925d338ca5534747cd
```

The verifier digest matches the current provisioner pin. That is source
consistency only, not a release manifest or candidate digest.

## Required future build authority

A future phase needs a reviewed builder/schema revision with a pinned complete
CPython 3.11 archive, SHA-manifested wheel-only inputs and build toolchain,
app/backend allowlisted source proof, final-tree external-path rejection,
post-move execution, two-build equality, tamper failure/rebuild success, and
runtime activation/attestation design. Those new artifact/tool hashes must be
reviewed inputs before a build, not values invented after output exists.

Promotion state for Part 2 is:

```text
NO_CANDIDATE
NOT_INSTALLED
NOT_RUNNING
```
