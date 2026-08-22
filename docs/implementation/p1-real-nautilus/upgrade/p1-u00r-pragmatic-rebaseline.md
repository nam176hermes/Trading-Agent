# P1-U00R Pragmatic Rebaseline

**Status:** authority overlay for implementation and review

## Authority

P1-U00R replaces the blocked P1-U00 publication design with the bounded
threat model `U00R_TRUSTED_HOST_COOPERATIVE_GIT_V1`.

The only accepted authority chain is:

```text
A = f15b1985215ef4d018f48c712221920502379a48
A -> S (reviewed source tip; inventory absent)
S -> I (one parent, only pin-inventory.json mode 100644 added)
```

`A` is the accepted Task 1 commit. Its tree is
`5b43de3c0b0b7bb9c76f6e179f7d419a6c0bd87a`. Remote main is anchored at
`c8fb6f694b11c065d5b819614532e9a77aa8da4b`; the integration ref begins at
`A`. Snapshot `326e97faf63c4f4ddf8fcb8e6a4087bc40c99bf2` remains immutable
historical NO-GO evidence and must not enter this ancestry. Rejected design
commits `dffee00621b8bc92c53eb3fcdbec63283ad8a198` and
`9aa855a573df668267c1e34c124072bcebfcaf34` are not promotable.

Normal `git add` and `git commit` are allowed: the ordinary index is staging,
not provenance authority. Private or alternate indexes, `GIT_INDEX_FILE`,
custom tree builders, and custom ref publishers are forbidden.

This overlay supersedes every prior R0--R16 packet, private-index or
index-free publisher, descriptor/memfd custody claim, hostile-same-UID
completion claim, and old completion verdict. It does not edit, reinterpret,
or replace imported historical plans or the task matrix. Task 1 and the
immutable NO-GO snapshot remain historical evidence only.

## Scope and non-claims

Rollback Nautilus 1.227 is the active rollback authority. Nautilus 1.231 is
`CANDIDATE_CONTEXT_ONLY`. Upstream assets are
`PRESENT_NOT_CRYPTOGRAPHICALLY_VERIFIED`; local artifact/runtime qualification
is `DEFERRED_TO_P1_U02`. No separate candidate release policy is created here:
`engines/nautilus/v1.231-provenance-policy.json` remains U02 scope.

U00R establishes exact-source inventory and review evidence only. It does not
claim resistance to a malicious same-UID raw writer, ptrace, root, kernel
compromise, lock-bypassing CAS writers, or cryptographic upstream-attestation
verification. It does not build, import, semantically qualify, promote, run,
deploy, backtest, paper-trade, or live-trade Nautilus 1.231.

The approved successor path is
`U00R -> U01 -> U02 -> U03 -> U04 -> U05 -> U06 -> U07 -> U08 -> P1-00`.
The removed `P1_U00_COMPLETE` verdict cannot be used.

## Exact-source portable authority recovery amendment

For U00R R3--R7, an explicit `--portable` invocation in a fresh standalone
clone with a real `.git` directory selects embedded source authority without
consulting the ambient availability of the absolute external authority paths.
External repositories that happen to exist on the host cannot silently switch
or reject this explicit mode. Strict audit remains the only mode that resolves
those repositories and still fails unless all declared repositories and exact
Git objects are available and valid.

This narrowly supersedes the T-G01 requirement that portable mode accept only
when every external authority repository is absent. It does not alter the
sealed authority document, component manifests, introduction checks, immutable
evidence checks, Git-history requirements, or any tamper/schema failure. There
is no strict-to-portable fallback and `--release --portable` remains invalid.

No path masking, mount namespace, chroot, authority-root rename/removal, or
alternate checkout boundary may be used to obtain the portable result. Source
edits and commits remain normal Git operations in the approved source
worktree; exact-source smoke, inventory generation and verification, reviews,
and final gates use fresh standalone clones with real `.git` directories.

## T through T9 narrow repair exception

The Task 1 protected-path guard has one exact, reviewable exception for the
repair sequence `T..T9`: only
`scripts/nautilus_pin_inventory/git_source.py`,
`tests/governance/nautilus_pin_inventory/test_git_source.py`, and this overlay
may differ from accepted base `f007624191077edd0ba01e42b421e8bff12cbbf0`.
T2 is the review correction inside that same exception; exact `T` was rejected
and is superseded, not certified, by T2.  This is not separate authority or a
general release of protected-path equality.  The four R3 paths remain excluded
from every repair commit and retain their independent dirty-work status.

T, T2, and T3 are rejected immutable history, not verified behavior
boundaries. T3's exact reviews found capture-primary clean-EOF descendant
escape, lost reader/capture double-fault aggregation, exact-reap cleanup
bypass, clean-drain nonzero pre-reap group escape, and missing cross-bootstrap
CPU receipt evidence. T4's exact reviews then rejected its real
reader-close/capture-close double fault: a failed retained descriptor close
lost externally reachable cleanup custody. T5's exact reviews then rejected
duplicate pending ownership after a failed retry and self-cycling exception
causes. T6 was an incomplete intermediate and T7 was rejected for implicit
exception-context cycles. T8 was rejected because its bounded back-edge
detachment silently left large retained-descriptor graphs cyclic. T9 is the
review candidate only. T9 traverses the complete finite incoming cleanup graph
by identity before re-raising its owner, with real multi-prefix receipts above
the former threshold. Local receipts do not verify its behavior or authority.
Its public
`GitAuthorityCleanupPendingError` retains one private capture owner for bounded
explicit retry under the cooperative-host model; it does not guarantee that a
host which keeps denying `close(2)` will release the descriptor. No reader lifecycle, publication, retained-owner
cleanup, or semantic-equivalence claim in this repair sequence is certified
until fresh exact-T9 specification and authority reviews pass.

The T2 production draft existed before its mandatory behavioral RED matrix.
That ordering error is retained as disclosed recovery history: the draft was
preserved in its named path-specific stash, behavioral tests were established
against the committed base, and only then was the reconciliation resumed.  It
does not convert a passing test into independent review or release authority.

`U00R_TRUSTED_HOST_COOPERATIVE_GIT_V1` is unchanged.  This exception does not
claim resistance to a malicious same-UID raw writer, ptrace, root, kernel
compromise, lock-bypassing CAS writer, or upstream cryptographic compromise.

## Governed comparison interpretation

Python literal pins are required occurrences. Dynamic checks and governed
relations are evidence only and never satisfy a required literal pin.

A governed root is accepted only after whole-module, document-kind-aware
binding proof. The closed roots are `policy`, `manifest`,
`closure_manifest`, `closure_policy`, plus conditionally `specification` and
`expected_identity`; a root spelling alone has no authority. Rebinding,
shadowing, deletion, mutation, or an unproved origin fails closed.

Governed field families are keyed by `(document_kind, field)`, not the raw
field spelling. The supported document kinds are:

```text
nautilus_engine_build_policy
nautilus_runtime_closure_policy
nautilus_closure_manifest
nautilus_base_runtime_manifest
```

LLVM, wheel-cache, and other unrelated structural schemas are ordinary data.
In particular `policy.source_commit` and `closure_manifest.source_commit` are
`selected_source`, while `manifest.source_commit` and every
`engine_upstream_commit` are `upstream_commit`.

Same-family direct subscript `==`/`!=` checks form dynamic-guard evidence.
Approved cross-family comparisons form typed `cross_family_consistency_guard`
relations; the source/upstream comparison is one such relation. Relation
extraction includes terminal failure-predicate semantics only where the
whole-module proof establishes the semantic `!=` relation. Any root, field,
family, binding, operator, syntax, or span drift makes the inventory stale.
Aliases, calls, attributes, computed keys, chains, and similar derived forms
can never create pin occurrences; only proven single-assignment relation
evidence may retain them. One-ended derived/format predicates are outside the
relation inventory.

## Fix6 exact-module authority amendment

Fix6 is one direct child of rejected Fix5
`94ac5ba2647f1a418e786741b8fc340b05406b63`. It may change only this
overlay, `scripts/materialize_nautilus_runtime_closure.py`,
`scripts/nautilus_pin_inventory/python_extractor.py`,
`tests/foundation/test_nautilus_runtime_closure.py`, and
`tests/governance/nautilus_pin_inventory/test_python.py`. Every earlier
candidate remains immutable rejected history.

The governed production modules are exactly
`scripts/materialize_nautilus_runtime_closure.py` and
`services/job_worker/nautilus_closure.py`. Their binding, caller, helper,
origin-map, control-flow, and namespace authority is the normalized full-module
AST fingerprint of the exact reviewed source. A finite reflection blacklist is
not authority. Any AST structure or coordinate change outside an approved
direct governed comparison's raw `==`/`!=` operator fails closed and requires
a new reviewed module fingerprint. Raw-only source drift which leaves that AST
fingerprint unchanged remains inventory-stale through the schema-v4 source blob
OID and SHA-256 fields.

Normalization replaces only the raw operator node of a direct two-subscript
governed comparison with `Eq` before hashing. It does not normalize roots,
fields, families, document kinds, bindings, occurrence count, control flow,
call routes, source positions, or spans. The emitted guard or relation retains
the raw operator syntax fingerprint and exact source span, so operator drift
changes inventory bytes even when structural admission remains valid.

The selected-artifact manifest is ordinary structural data, not a governed
root. Fix6 may rename its local `manifest` binding to `artifact_manifest`,
prove the five previously optional identity keys are present, and replace the
corresponding `.get(...)` comparisons with direct subscripts. Missing keys must
still raise `RuntimeClosureMaterializationError` with the existing message,
extra keys must remain accepted, and valid inputs must return byte-identical
results.

The rollback policy and source commit remain unchanged. The sole working-source
exception is `scripts/materialize_nautilus_runtime_closure.py`; it is bound by
the exact Fix6 commit, the literal Fix6 source SHA-256, the normalized AST
fingerprint, and later schema-v4 source blob OID and SHA-256 fields.

Ordinary additions to either governed production module are not admitted by
Fix6 merely because they do not mention a governed root. They require a new
exact fingerprint and review. This restriction applies only to the two named
modules; generic Python extraction remains available for every other path.

Fix6 does not modify Task 1, R3, inventory, the integration ref, rollback
authority, Nautilus 1.231 status, runtime qualification, or any live/production
boundary. Only dual fresh exact PASS may resume R3.

## Fix7 operator-normalization amendment

Fix7 is one operator-authorized direct child of rejected Fix6
`26402d1bf14a448f56fa41fe152f4f786065114d`. It may change only this overlay,
`scripts/nautilus_pin_inventory/python_extractor.py`, and
`tests/governance/nautilus_pin_inventory/test_python.py`.

Normalization may exempt a direct comparison's raw operator only when both
fields map to governed identity families and the comparison can emit typed
guard or relation evidence. Unsupported identity fields, including
`engine_name` and `python_identity`, remain exact-module-bound; their operator
drift requires a new exact module fingerprint and fails closed. Dual fresh
exact review remains required before R3 may resume.
