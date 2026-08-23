# P1 task ledger

Gate 0 consumes canonical inventory commit
`c174ba9de21c91dcda53ebcb825fdd1597e8800c` and begins the remaining upgrade
qualification program at P1-U01. No row below is artifact qualification,
promotion, production, network-trading, or live authority.

| Task | Depends on | Status | Next acceptance |
|---|---|---|---|
| P1-U01 | P1-G0 | NOT_STARTED | Complete the v1.227-to-v1.231 release, API, build, and semantic delta with no unexplained direct surface. |
| P1-U02 | P1-U01 | BLOCKED | Bind immutable v1.231 release/source provenance and pass offline mutation checks. |
| P1-U03 | P1-U02 | BLOCKED | Define candidate-only hermetic toolchain and dependency policies without changing active 1.227 policy. |
| P1-U04 | P1-U03 | BLOCKED | Build and attest a sealed v1.231 candidate beside the unchanged rollback. |
| P1-U05 | P1-U04 | BLOCKED | Pass generated direct-API and native callback compatibility probes. |
| P1-U06 | P1-U05 | BLOCKED | Pass the release-regression and exact execution/accounting semantics campaign. |
| P1-U07 | P1-U06 | BLOCKED | Produce three deterministic candidate runs and zero unexplained semantic drift. |
| P1-U08 | P1-U07 | BLOCKED | Produce exact promotion evidence, then stop for operator decision: PROMOTE_1_231 | HOLD_1_227. |

P1-U tasks advance only in dependency order. P1 product work remains blocked
until P1-U08 evidence is accepted and the operator explicitly approves
promotion. Until then, 1.227 stays active rollback and 1.231 stays
`CANDIDATE_CONTEXT_ONLY`.
