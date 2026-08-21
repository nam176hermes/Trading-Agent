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

## T through T8 narrow repair exception

The Task 1 protected-path guard has one exact, reviewable exception for the
repair sequence `T..T5`: only
`scripts/nautilus_pin_inventory/git_source.py`,
`tests/governance/nautilus_pin_inventory/test_git_source.py`, and this overlay
may differ from accepted base `f007624191077edd0ba01e42b421e8bff12cbbf0`.
T2 is the review correction inside that same exception; exact `T` was rejected
and is superseded, not certified, by T2.  This is not separate authority or a
general release of protected-path equality.  The four R3 paths remain excluded
from both commits and retain their independent dirty-work status.

T, T2, and T3 are rejected immutable history, not verified behavior
boundaries. T3's exact reviews found capture-primary clean-EOF descendant
escape, lost reader/capture double-fault aggregation, exact-reap cleanup
bypass, clean-drain nonzero pre-reap group escape, and missing cross-bootstrap
CPU receipt evidence. T4's exact reviews then rejected its real
reader-close/capture-close double fault: a failed retained descriptor close
lost externally reachable cleanup custody. T5 is a review candidate only. Its
public `GitAuthorityCleanupPendingError` retains one private capture owner for
bounded explicit retry under the cooperative-host model; it does not guarantee
that a host which keeps denying `close(2)` will release the descriptor. Local
behavior receipts do not verify T5, and no reader lifecycle, publication,
retained-owner cleanup, or semantic-equivalence claim in this repair sequence
is certified until fresh exact-T5 specification and authority reviews pass.

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
