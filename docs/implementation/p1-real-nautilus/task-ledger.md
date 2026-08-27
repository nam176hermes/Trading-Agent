# P1 task ledger

Gate 0 consumes canonical inventory commit
`c174ba9de21c91dcda53ebcb825fdd1597e8800c` and begins the remaining upgrade
qualification program at P1-U01. No row below is artifact qualification,
promotion, production, network-trading, or live authority.

| Task | Depends on | Status | Next acceptance |
|---|---|---|---|
| P1-U01 | P1-G0 | IMPLEMENTED_UNACCEPTED | Re-accept the committed release/API/semantic delta on the exact U04 recovery tree. |
| P1-U02 | P1-U01 | IMPLEMENTED_UNACCEPTED | Re-accept the committed immutable v1.231 release/source provenance on the exact U04 recovery tree. |
| P1-U03 | P1-U02 | IMPLEMENTED_UNACCEPTED | Re-accept the committed candidate-only toolchain and dependency policies on the exact U04 recovery tree. |
| P1-U04 | P1-U03 | X3_EVIDENCE_RECONCILED_RE_REVIEW_REQUIRED | Re-review the exact evidence-only ledger update, then reseal X4 authority against the resulting commit/tree. |
| P1-U05 | P1-U04 | NOT_STARTED | Pass generated direct-API and native callback compatibility probes. |
| P1-U06 | P1-U05 | NOT_STARTED | Pass the release-regression and exact execution/accounting semantics campaign. |
| P1-U07 | P1-U06 | NOT_STARTED | Produce three deterministic candidate runs and zero unexplained semantic drift. |
| P1-U08 | P1-U07 | NOT_STARTED | Produce exact promotion evidence, then stop for operator decision: PROMOTE_1_231 | HOLD_1_227. |

P1-U tasks advance only in dependency order. P1 product work remains blocked
until P1-U08 evidence is accepted and the operator explicitly approves
promotion. Until then, 1.227 stays active rollback and 1.231 stays
`CANDIDATE_CONTEXT_ONLY`.

The split-process, live rollback revalidation, immutable native snapshot,
read-only namespace, strict candidate ELF, and pinned-dependency ELF boundary
repairs are implemented through `5fd2e4ec406fa54967c47fbac0876e925754ec0a`
(tree `594f098353c18a894ee078a6b081b166ca83d63a`). Canonical portable attempt 25
passed and its sealed artifact passed the production firewall. The fresh X3
security review passed with no findings; the fresh spec review found only stale
acceptance ledgers. A disposable exact-candidate promotion inventory now
verifies with 174 entries, six narrow path aliases, and zero unclassified
identities. This tracked update reconciles that evidence and must itself receive
a fresh evidence-only review before X3 closes. No X4 receipt or Build A authority
is granted by this ledger. Native Build A/B, X5, activation, and promotion remain
unauthorized until their later gates pass.
