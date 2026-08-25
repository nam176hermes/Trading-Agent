# P1-U04 X4 Build-Boundary Architecture Escalation

## Status and scope

This design resolves the single load-bearing X4 review finding: the current
candidate entry point performs two builds in one Python process and cannot
consume the exact frozen X4 authority receipt. The packet is limited to two
implementation/review rounds. It must not perform a native build, activate or
promote 1.231, modify the active 1.227 authority, open U05, use network/package
fallback, or accept arbitrary caller-selected compiler/cache/toolchain paths.

The existing X4 receipts are evidence of the old source tree only. Any source
change makes them stale. After this packet is committed, X4 authority preflight
must run again on the new exact HEAD/tree and fresh spec plus security/replay
reviews must pass before X5 may execute.

## Root cause

`build_candidate_engine()` calls `_build_candidate_once()` twice and publishes
one combined reproducibility receipt. The `--build-candidate` CLI accepts no X4
receipt and offers no stopping point after Build A. Separate temporary stages
inside that one process do not satisfy X6's separate fresh process-tree gate.

## Chosen design

Replace the combined candidate action with two explicit, mutually exclusive
actions:

```text
--build-candidate-a --offline --authority-receipt PATH --authority-receipt-sha256 HEX
--build-candidate-b --offline --authority-receipt PATH --authority-receipt-sha256 HEX
```

Both actions continue to obtain every build path and tool from the committed
candidate policies. The new receipt arguments identify and authenticate the X4
decision; they are not authority-path overrides.

Before either action can enter Bubblewrap, it must:

1. require a regular, single-link, task-owned, read-only receipt;
2. verify the caller-supplied SHA-256 against the exact receipt bytes;
3. validate the X4 receipt schema and `X4_READY_FOR_BUILD_A` verdict;
4. verify its bound Git HEAD/tree against the current clean tracked checkout;
5. verify the committed policy/toolchain hashes and live external-authority
   identities named by the receipt;
6. rerun the existing internal candidate-authority verification;
7. reject any arbitrary legacy policy, Python, artifact, cache, Cargo, LLVM, or
   sandbox arguments.

Git identity verification uses the fixed repository root and an absolute
system Git executable with an empty/sanitized environment. Git is used only to
bind reviewed source bytes; it is never compiler, package, cache, or runtime
authority.

## Build A boundary

Build A runs exactly one `_build_candidate_once()` call in its CLI process. It
closes the retained source descriptor before publication and atomically
publishes a sealed `build-a` directory below the policy-owned private build
parent. The directory contains only:

```text
candidate wheel
artifact core manifest
Build A receipt
```

The Build A receipt binds the X4 receipt digest, source HEAD/tree, policy and
toolchain digests, wheel/native manifest, source descriptor identity, process
identity, and sanitized environment digest. Build A does not create final
`artifacts`, Build B, forensic, or runtime-closure roots.

## Build B and reproducibility boundary

Build B is a second CLI invocation and therefore a separate process tree. It
requires the same X4 receipt and the fixed policy-derived sealed `build-a`
directory. Before building, it verifies the complete Build A file set and
receipt, checks that Build A used the same X4 receipt, and rejects a Build A
process identity equal to its current process identity. Process identity is
the Linux boot ID plus PID plus `/proc/self/stat` start time, so a reused PID
cannot make two invocations appear identical.

Build B runs exactly one fresh `_build_candidate_once()` call, using its own
physical staging and source descriptor identity. It atomically publishes a
sealed `build-b` directory, compares raw wheel bytes, artifact core/native
content, source/tool/environment authority, and then publishes the existing
final `artifacts` layout only when every required comparison passes. The final
reproducibility receipt records two builds and both distinct process/source
identities. Any mismatch leaves final artifacts absent; bounded forensic
retention remains explicit and never selects a convenient pair.

The policy-derived children are:

```text
candidate_build_root/build-a
candidate_build_root/build-b
candidate_build_root/artifacts
```

They must all be absent at X4 preflight and pairwise disjoint from each other,
the schema-7 runtime root, forensic root, input/toolchain caches, and 1.227
rollback root.

## Failure handling

All validation is fail-closed. Missing receipt authority is `DEFERRED` only in
the X4 preflight lane; a build command missing its required receipt is an
error. A supplied receipt with wrong bytes, schema, verdict, HEAD/tree, policy
hash, authority identity, mode/owner/link count, or root state is `FAIL`.

No partial Build A/B or final artifact directory may survive a failed atomic
publication. A failed Build B must never alter the sealed Build A record or
1.227. No action may weaken assertions, skip tests, use xfail, install packages,
contact the network, or fall back to ambient compiler/package/cache authority.

## TDD and verification

RED tests must first prove that the current combined entry point cannot satisfy
the contract. Focused behavior tests then cover:

- Build A calls the native build primitive once and never creates Build B or
  final artifacts.
- Build B is a separate action, calls the primitive once, and rejects matching
  process/root/source identities.
- Both actions reject missing, stale, malformed, mutable, foreign, or
  digest-mismatched X4 receipts.
- Both actions reject caller-selected authority paths and non-offline mode.
- Build B rejects a different X4 receipt, modified Build A bytes, raw/native/
  manifest differences, and partial publication.
- Descriptor closure occurs on every success and failure path.
- The accepted final artifact and schema-7 consumer contract retain the exact
  two-build reproducibility fields required downstream.

Only portable/focused tests run during implementation. After the exact code
bytes are committed, the integration lead reruns X4's U02 provenance, U03
toolchain regeneration, real Bubblewrap host lane, exact schema-6 rollback
attestation, root absence/disjointness scan, and generates a new hash-bound X4
receipt. X5 remains blocked until fresh spec and security/replay reviews both
approve that exact HEAD/tree and receipt.

## Alternatives rejected

1. Keep the current combined function and document its two temporary stages:
   rejected because it has no X5 review stop and no separate X6 process tree.
2. Add one wrapper that spawns both builds sequentially: rejected because the
   wrapper still crosses X5 into X6 without the required external review gate.
3. Accept a plan exception for same-process builds: rejected because it weakens
   the authoritative X5/X6 isolation requirement that triggered escalation.

## Completion condition

The architecture packet completes only when RED-to-GREEN evidence, focused
regression tests, and its exact code review pass within two rounds. That result
authorizes X4 re-preflight, not a native build. X4 then generates a new receipt
on the new exact HEAD/tree and obtains fresh spec plus security/replay reviews.
Only the subsequently reviewed `X4_READY_FOR_BUILD_A` receipt authorizes X5.
