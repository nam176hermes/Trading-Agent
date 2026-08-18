# P1-U00 Git Object Authority Design

**Date:** 2026-08-17  
**Status:** Approved architecture amendment; implementation not started  
**Program:** P1-U00 — freeze NautilusTrader 1.227 rollback authority and inventory every engine pin  
**Accepted code baseline:** `bc025c09631333510a89677bb5d2e09ec56e17bf`  
**Accepted baseline tree:** `c2704420cc6e34949fae65b80f2fab9887876dfd`

## 1. Decision

P1-U00 will stop treating a mutable worktree pathname as the publication
authority for the generated pin inventory. Source discovery, inventory
generation, review, and final acceptance will instead bind to exact Git blob,
tree, and commit object IDs, with independent SHA-256 receipts for every
generated or reviewed byte stream.

The uncommitted pathname publisher based on
`renameat2(RENAME_EXCHANGE)` will not be integrated. Its two remaining RED
tests and retained report are historical NO-GO evidence, not qualification
tests to skip or mark expected-failure.

This amendment preserves the original P1-U00 objective and acceptance
semantics:

- inventory every rollback and candidate-context pin exhaustively;
- keep NautilusTrader 1.227.0 as immutable rollback authority;
- keep NautilusTrader 1.231.0 context-only until P1-U08;
- reject every unknown identity;
- make review evidence reproducible and immutable;
- make concurrent mutation fail closed rather than overwrite authority.

## 2. Why the pathname design is rejected

Linux `renameat2(RENAME_EXCHANGE)` atomically exchanges two names, but it has
no operand for an expected inode or descriptor. A same-UID namespace writer
can replace either name after the final userspace identity check and before the
syscall resolves the path. Rollback has the same gap.

The frozen NO-GO candidate proves both limits:

```text
37 passed, 2 failed
```

The failures are:

```text
candidate replacement in the final exchange syscall gap
target replacement in the final restoration syscall gap
```

Adding more pre- or post-syscall checks can detect many outcomes but cannot
turn pathname exchange into inode-conditional compare-and-swap. Continuing
that design would add state-machine complexity without satisfying the stated
authority contract.

References:

- https://man7.org/linux/man-pages/man2/renameat2.2.html
- https://git-scm.com/docs/gitdatamodel.html
- https://git-scm.com/docs/git-update-ref.html

## 3. Authority and threat model

### 3.1 Authoritative values

The following values are authoritative for a P1-U00 candidate:

```text
expected_parent_commit_oid
source_tree_oid
inventory_blob_oid
inventory_sha256
final_tree_oid
candidate_commit_oid
```

The worktree pathname, temporary index pathname, generated scratch file, and
branch name are never standalone authority.

### 3.2 Covered concurrent mutations

The design must fail closed under:

- worktree ancestor, parent, leaf, symlink, hard-link, mode, owner, and content
  replacement;
- generated-file replacement before staging;
- temporary-index replacement or corruption;
- Git object deletion or byte corruption;
- branch or ref movement before final acceptance;
- index entries that do not reproduce the reviewed candidate tree;
- a candidate commit whose parent or tree differs from the reviewed receipt;
- post-review worktree drift.

### 3.3 Explicit boundary

The design does not claim protection against an actor that can alter the
publisher process memory, replace the Git executable after executable custody
has been established, or obtain a cryptographic collision for both the Git
object ID and the independent SHA-256 receipt. That stronger threat requires a
distinct-identity execution authority and is outside repository-local U00.

An actor with same-UID filesystem access may cause denial of service by
deleting objects or moving refs. Such action must produce a nonzero result; it
must not produce a false accepted inventory.

## 4. Architecture

### 4.1 Modules

```text
scripts/nautilus_pin_inventory/git_source.py
    Resolve one exact commit/tree and read regular blobs by exact object ID.

scripts/nautilus_pin_inventory/git_candidate.py
    Build pre-inventory and final candidate trees in a private temporary index.

scripts/nautilus_pin_inventory/engine.py
    Run typed extractors over immutable GitBlobSnapshot values and serialize v4.

scripts/nautilus_pin_inventory/cli.py
    Resolve/check/generate candidate receipts without updating a branch ref.

scripts/inventory_nautilus_pins.py
    Thin stable compatibility entry point.
```

The uncommitted 632-line pathname publisher will be replaced, not extended.
`git_source.py` and `git_candidate.py` remain separate because source reading
and candidate-tree construction have different authority and failure models.

### 4.2 Git process boundary

All Git subprocesses must:

- use an exact executable resolved during process initialization;
- run with `cwd` set to the verified repository root;
- pass arguments as an argv tuple without a shell;
- set `GIT_CONFIG_NOSYSTEM=1`;
- set `GIT_CONFIG_GLOBAL=/dev/null`;
- set `GIT_OPTIONAL_LOCKS=0` for read-only operations;
- use `--no-replace-objects` or `GIT_NO_REPLACE_OBJECTS=1`;
- reject repository alternates, grafts, replace refs, and unexpected object
  format changes;
- bound stdout, stderr, entry count, path length, per-blob size, aggregate
  scanned bytes, and execution time;
- make no network call and accept no URL, remote, credential, or external
  object authority.

The current repository object format is `sha1`. Code must query
`git rev-parse --show-object-format` and support only explicit `sha1` or
`sha256` formats. Every blob also receives a SHA-256 receipt independent of the
Git object format.

## 5. Immutable source model

### 5.1 Types

```python
@dataclass(frozen=True)
class GitBlobSnapshot:
    path: str
    mode: int
    blob_oid: str
    sha256: str
    data: bytes


@dataclass(frozen=True)
class GitTreeSnapshot:
    commit_oid: str | None
    tree_oid: str
    object_format: str
    blobs: tuple[GitBlobSnapshot, ...]
```

`GitTreeSnapshot.from_commit(repo_root, commit_oid, limits=...)` accepts only a
full commit OID and resolves it to one exact tree.
`GitTreeSnapshot.from_tree(repo_root, tree_oid, limits=...)` accepts only a full
tree OID and sets `commit_oid=None`; this is the constructor used for T0 and
T1 before a candidate commit exists. Both constructors list the exact tree
with `git ls-tree -r -z --full-tree` and read every selected blob with
`git cat-file --batch` by object ID.

For every blob it must verify:

- the listed type is `blob`;
- the tracked mode is an allowed regular-file mode;
- the path is strict NFC-normalized UTF-8, relative POSIX form, with no NUL,
  backslash, empty/absolute/`.`/`..` component, surrogate, or control code;
- the returned object ID, type, and size equal the `ls-tree` record;
- the Git object ID recomputed from `blob <size>\0<data>` equals `blob_oid`;
- the independent SHA-256 equals the stored receipt;
- the bytes satisfy configured per-file and aggregate limits.

Tracked symlinks, submodules, non-blob objects in scan scope, duplicate paths,
duplicate records, malformed batch responses, missing objects, and unexpected
object modes are stable failures.

No extractor receives a repository pathname. Extractors receive only
`GitBlobSnapshot.path` and immutable `GitBlobSnapshot.data`.

## 6. Candidate-tree construction

### 6.1 Types

```python
@dataclass(frozen=True)
class CandidatePathReceipt:
    path: str
    mode: int
    blob_oid: str
    sha256: str


@dataclass(frozen=True)
class CandidateTreeReceipt:
    expected_parent_commit_oid: str
    expected_parent_tree_oid: str
    source_tree_oid: str
    final_tree_oid: str
    inventory_blob_oid: str
    inventory_sha256: str
    paths: tuple[CandidatePathReceipt, ...]
```

`GitCandidateTreeBuilder` consumes:

```text
verified repository root
full expected parent commit OID
exact allowed path set
immutable path -> bytes updates
```

It creates a private `0600` temporary index under an already-validated private
temporary parent, loads the expected parent tree with `git read-tree`, writes
each supplied byte stream with `git hash-object -w --stdin`, updates only the
explicit allowed entries with `git update-index --cacheinfo`, and obtains the
tree with `git write-tree`.

It never updates `HEAD`, a branch, a tag, the main worktree index, or a worktree
file.

### 6.2 Two-tree generation

P1-U00 generation uses two immutable trees:

```text
T0 = accepted Task 5 tree
     + Task 6 engine/CLI/tests/baseline/doc changes
     - generated pin-inventory.json

inventory_bytes = InventoryEngine.scan(T0)

T1 = T0 + pin-inventory.json blob
```

The v4 inventory records `source_tree_oid=T0`. The generated inventory file is
not scanned into itself. The external task report binds `T1` and the final
candidate commit; those values are not embedded into the inventory and cannot
create a recursive hash dependency.

The builder must independently reopen and verify T0 and T1 through
`GitTreeSnapshot` before issuing a receipt.

## 7. Schema v4 and engine behavior

Each inventory entry contains:

```text
stable_id
path
carrier
family
full_value
role
syntax
owner
classification
spans
source_blob_oid
source_blob_sha256
source_tree_oid
```

Stable IDs derive from:

```text
(path, carrier, family, full_value, syntax)
```

Spans are compared separately so an added same-line occurrence changes the
inventory without changing the semantic identity.

Generation fails before creating a final-tree receipt when:

- any extracted value is unregistered;
- any required rollback or candidate-context identity is absent;
- an occurrence cannot be assigned an exact carrier-aware span;
- two extractors claim incompatible ownership of the same span;
- a source object or tree changes identity;
- the independent test oracle finds an occurrence missing from production
  output.

`--generate` never blesses an unknown value.

### 7.1 Dynamic governed checks are audited separately

The accepted structured-extractor commit
`bc025c09631333510a89677bb5d2e09ec56e17bf` deliberately rejects a governed field
compared with a dynamic mapping value. The exact accepted repository contains
runtime consistency guards of that form, including the materializer and job
worker comparisons between policy, specification, closure manifest, and
expected-identity mappings. They are runtime checks, not static pin values.

The Git-tree engine must not solve this conflict with a path allowlist, a broad
ignore, or by treating a dynamic value as an identity. It introduces a second
typed result:

```python
@dataclass(frozen=True)
class DynamicGovernedCheck:
    path: str
    left_root: str
    left_field: str
    operator: str
    right_root: str
    right_field: str
    syntax_fingerprint: str
    span: SourceSpan
```

Only direct `==` and `!=` comparisons between two statically resolved
governed mapping fields are admitted. Both roots and both literal field names
must survive the same scope, alias, rebinding, mutation, and source-origin
analysis used for literal expectations. Calls, attributes, computed keys,
chained comparisons, mixed operators, unknown roots, and dynamic expressions
remain stable errors.

Schema v4 serializes these records in a separate `dynamic_guards` collection.
They do not receive an `AllowedIdentity`, are not counted as rollback or
candidate pins, and cannot satisfy a required-identity occurrence. Removal,
addition, operator change, root/field change, or normalized-AST fingerprint
change makes the inventory stale and requires review.

## 8. Review candidate and local commit

### 8.1 Candidate creation

The final candidate packet runs in a dedicated local task branch and worktree
created from the last accepted implementation commit. After T1 passes focused
checks, the implementer stages only the explicit final-packet paths.
`git write-tree` must equal the reviewed `final_tree_oid=T1`.

The implementer creates the candidate commit:

```bash
git commit-tree "$FINAL_TREE_OID" \
  -p "$EXPECTED_PARENT_COMMIT_OID" \
  -m "docs(p1u): freeze Nautilus 1.227 rollback baseline"
```

The returned commit OID, rather than the task branch name, is reviewed in a
fresh detached worktree and standalone qualification environment. Candidate
creation does not move any ref. Only after both task reviewers PASS is the task
branch advanced from the exact expected parent with a three-argument
`git update-ref`; the recovery branch remains unchanged.

### 8.2 Review and promotion

The detached candidate must pass:

- focused P1-U00 tests;
- canonical inventory checker;
- independent occurrence oracle;
- provenance verification;
- P0 baseline and maintainability gates;
- exact diff and authority/live-flag checks;
- full `make ci-portable NONINTERACTIVE=1` at the final checkpoint;
- fresh spec review;
- fresh security/code-quality review.

Only after both reviewers PASS may the recovery branch fast-forward to the
already-reviewed candidate commit. Because the recovery branch is checked out
in its own worktree, that clean worktree first detaches at the candidate. The
now-unattached branch is then updated by CAS and reattached, preventing a stale
index/worktree without using merge, cherry-pick, or reset:

```bash
git switch --detach "$CANDIDATE_COMMIT_OID"
git update-ref \
  refs/heads/task/p1-u00-recovery \
  "$CANDIDATE_COMMIT_OID" \
  "$EXPECTED_PARENT_COMMIT_OID"
git switch task/p1-u00-recovery
```

The three-argument `update-ref` is the commit-point compare-and-swap. A branch
movement causes rejection without changing the ref.

After the CAS, acceptance requires:

```text
HEAD == candidate_commit_oid
HEAD^ == expected_task5_parent
HEAD^{tree} == final_tree_oid
git write-tree == final_tree_oid
git diff-files --quiet == 0
git diff-index --cached --quiet HEAD == 0
tracked status clean
live/network flags false
```

No push, merge, PR, deployment, service mutation, production mutation, broker
access, exchange access, or live execution is part of this design.

## 9. Failure semantics

| Failure | Result |
|---|---|
| Worktree file changes after capture | Candidate tree keeps captured bytes; materialized worktree becomes dirty and cannot pass acceptance |
| Temporary index changes | Tree/OID mismatch; no receipt |
| Blob missing or corrupt | Object-ID/SHA-256 verification failure |
| Ref moves before acceptance | `update-ref` CAS failure |
| Candidate parent differs | Candidate rejected before review |
| Candidate tree differs | Candidate rejected before review |
| Unknown identity appears | Generation exits nonzero; no T1 receipt |
| Reviewer returns FAIL | Branch ref remains at accepted parent |
| Portable CI exits nonzero | Branch ref remains at accepted parent |
| Git object/ref disappears after evidence | Program status is blocked; exact accepted OID remains the evidence key, never replaced by a moving branch name |

No failure is repaired by skip, xfail, assertion weakening, digest replacement,
fixture substitution, or normalization of unexplained drift.

## 10. Test strategy

### 10.1 Git source RED controls

- worktree leaf/parent/root replacement does not change a tree snapshot;
- symlinked worktree paths are irrelevant because blobs come from the tree;
- malformed NUL/UTF-8 tree paths fail;
- symlink/submodule modes fail;
- missing, truncated, oversized, wrong-type, wrong-OID, and corrupt blobs fail;
- replace refs and alternates are rejected;
- moving `HEAD` after exact commit resolution does not change the scan;
- scanner subprocess timeout and aggregate limits fail closed.

### 10.2 Candidate-tree RED controls

- an update outside the explicit allowed path set fails;
- duplicate paths, wrong modes, wrong parent, and wrong tree fail;
- temporary-index replacement fails receipt verification;
- candidate bytes modified after capture do not alter the object/tree receipt;
- concurrent branch movement makes `update-ref <new> <old>` fail;
- T0/T1 mismatch and inventory self-inclusion fail;
- corrupted object bytes fail both Git OID and SHA-256 verification.

### 10.3 Existing U00 controls

All accepted Task 1–4 tests remain in force, including:

- complete rollback and candidate identity oracle;
- full-token suffix mutation properties;
- Markdown/path carrier boundaries;
- Python closed-comparison grammar;
- JSON duplicate/typed scalar/span handling;
- malformed metadata fail-closed behavior.

The two pathname syscall-gap RED proofs remain in retained reports with exact
hashes. They are removed from the active suite only because the pathname
publisher and its attack surface are removed from production, not because the
assertions are weakened.

## 11. Migration and commit boundaries

### Packet A — Design amendment

```text
docs/superpowers/specs/2026-08-17-p1-u00-git-object-authority-design.md
docs/superpowers/plans/2026-08-17-p1-u00-git-object-authority.md
```

### Packet B — Git source and candidate authority

```text
scripts/nautilus_pin_inventory/git_source.py
scripts/nautilus_pin_inventory/git_candidate.py
tests/governance/nautilus_pin_inventory/test_git_source.py
tests/governance/nautilus_pin_inventory/test_git_candidate.py
tests/governance/nautilus_pin_inventory/test_source_io.py
```

The uncommitted pathname `source_io.py` is not integrated. Any compatibility
surface must re-export only the Git object authority types; it must not retain
pathname publication code.

### Packet C — Schema v4 and candidate tree

```text
scripts/nautilus_pin_inventory/engine.py
scripts/nautilus_pin_inventory/cli.py
scripts/inventory_nautilus_pins.py
docs/implementation/p1-real-nautilus/upgrade/1.227-rollback-baseline.md
docs/implementation/p1-real-nautilus/upgrade/pin-inventory.json
tests/governance/test_nautilus_pin_inventory.py
tests/governance/nautilus_pin_inventory/conftest.py
tests/governance/nautilus_pin_inventory/test_end_to_end.py
```

The current-subject test adapter builds private fixture Git commits and exact
T0/T1 trees. It does not retain the legacy `--fixture-filesystem` production
path. The immutable known-bad adapter remains available only to reproduce the
historical RED candidate by exact commit/blob receipt.

Packet C also makes the narrowly required model/extractor changes for
`DynamicGovernedCheck`:

```text
scripts/nautilus_pin_inventory/model.py
scripts/nautilus_pin_inventory/python_extractor.py
tests/governance/nautilus_pin_inventory/test_python.py
```

### Packet D — Qualification and integration

Evidence only, followed by the reviewed local branch CAS. No remote mutation.

## 12. Acceptance criteria

P1-U00 is complete only when:

1. Task 1–4 accepted commits remain ancestors of the candidate.
2. No production pathname inventory publisher remains.
3. Git source and candidate authority focused tests pass without skip or xfail.
4. Schema v4 contains zero unknown entries and cites every independent-oracle
   occurrence.
5. Every admitted dynamic governed check is serialized separately and matches
   the exact reviewed Git tree; unsupported dynamic expressions still fail.
6. The inventory binds exact T0 blob and tree receipts.
7. The candidate commit binds exact T1 and the expected last implementation
   parent.
8. No active runtime policy, live flag, execution authority, or 1.227 rollback
   closure changes.
9. Provenance, P0 baseline, P0 maintainability, artifact firewall, critical
   coverage, and full portable CI pass.
10. Fresh spec and security/code-quality reviewers return PASS.
11. The Integration Lead verifies the final branch CAS, HEAD, parent, tree,
    index, worktree, diff scope, and retained evidence hashes.

Only then may P1-U01 start automatically.
