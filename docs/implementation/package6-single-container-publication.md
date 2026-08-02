# Package 6 Single-Container Controller Publication

## Authority boundary

The controller publishes one private regular file named
`package6-controller-final.json`. It remains paper-only evidence with
`production_authority_status=TEST_ONLY`; production release authority is
unchanged and unavailable. The logical `controller-final-decision.json` and
`index.json` documents are never materialized as authoritative paths.
No separate human-readable views are emitted. Any future view must carry the
literal `NON_AUTHORITATIVE_VIEW` classification and cannot participate in seal
or commit authority.

## Canonical schema

The outer document is strict canonical UTF-8 JSON with the exact fields
`schema_version`, `container_kind`, and `entries`. `schema_version` is `"1"`,
`container_kind` is `PACKAGE6_CONTROLLER_FINAL_PUBLICATION`, and `entries` is
the sorted, exact two-entry inventory. Each entry has exact `path`,
`size_bytes`, lowercase SHA-256, and canonical padded Base64 content. Decode
rejects duplicate, missing, extra, reordered, oversized, malformed,
noncanonical, or incorrectly bound content without changing the runtime
evidence container format.

## Publication and lifecycle

The finalizer opens trusted directory authority, creates one unnamed
`O_TMPFILE` under the native `FdOwner`, writes and fsyncs all bytes, then twice
reads the same retained descriptor. Between reads it decodes and verifies the
container, verifies final-decision/index semantics, and revalidates the exact
runtime input snapshot. Byte equality, SHA-256, size, and stable metadata must
remain unchanged.

Exactly one `_link_owned_tmpfile` call is the commit point. Directory fsync and
descriptor-relative no-follow path identity confirmation follow; no semantic
validation follows the link. `FinalPublicationAuthority` retains directory and
canonical file custody, identity, digest, size, name, commit state, and explicit
close/recovery state. Consumers read logical entries through the retained
descriptor and must prove `close()` before exit. Pathname `open`,
`Path.read_bytes`, `Path.read_text`, and runtime-snapshot reopening are
forbidden for final-output validation.

Post-link durability or identity failure raises `FinalPublicationFailure` with
the still-retained `FinalPublicationAuthority`. An interruption after the link
syscall begins but before Python records success sets
`publication_commit_uncertain`; no fallible probe is attempted while unwinding.
The explicit `recover()` operation classifies the final name through retained
directory and file descriptors, retries directory durability, re-reads canonical
bytes with exact `st_nlink == 1`, and confirms device/inode identity. Every
fallible canonical-file and output-directory syscall runs through generation
custody. `EBADF` or asynchronous interruption consumes ownership without closing
a possibly recycled descriptor number and leaves recovery unresolved. This
applies before and after the final link, including both file and directory
`fsync`.

Pre-link reads require exact `st_nlink == 0`. Every exception after the final
link attempt, including genuine or post-success `FileExistsError`, carries
`FinalPublicationAuthority` with uncertain commit state instead of closing its
descriptors. Recovery remains sticky across `close()` and can only clear after
all recovery proofs succeed. The CLI always attempts or explicitly defers
recovery and verifies descriptor closure before returning a failure.

Retained reads bind canonical bytes, SHA-256, size, and the confirmed metadata
generation. The review identity uses `PACKAGE6_GOAL2_PATCH_V1` so a Goal 2
single-container candidate cannot be confused with the broader historical
Package 6 patch contract.

The supporting publishers use the same boundary discipline. `_write_files`
rejects more than one name, and the runtime evidence container remains unnamed
until its complete bytes have been fsynced, decoded, verified, and re-read.
Pre-link failure never invokes pathname rollback. Post-link failure preserves
one complete file rather than attempting an identity-racy unlink.

## Crash and recovery matrix

| Boundary | Visible result after crash |
| --- | --- |
| unnamed open through second retained read | no output name |
| final `linkat(AT_EMPTY_PATH)` | one complete canonical file or no name |
| directory fsync or identity confirmation | exactly one complete canonical file |

Pre-link failure closes owned descriptors and the unnamed inode disappears.
Post-link recovery never rewrites or pathname-reopens semantic bytes; retained
authority identifies the complete committed inode. No partial prefix is ever
reachable by an authoritative pathname.

## Verification

```bash
uv run pytest -q tests/foundation/test_package6_controller_closure.py -k 'controller_result'
uv run pytest -q tests/foundation/test_package6_controller_closure.py
uv run pytest -q tests/foundation/test_package6_runtime_approval.py
uv run pytest -q tests/foundation/test_package6_custodian_contract.py
make -C native/package6_custodian test
python3 -m py_compile services/paper_runtime/evidence.py services/paper_runtime/__init__.py scripts/finalize_package6_controller_evidence.py tests/foundation/test_package6_controller_closure.py
git diff --check
```

The eight `controller_result` tests run finalization in real child processes,
inject `SIGKILL` at every publication boundary, and let the parent inspect the
real output directory. Additional authority tests cover post-link recovery,
input-snapshot close failure, and retained metadata mutation.
