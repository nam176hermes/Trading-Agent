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
| P1-U04 | P1-U03 | ARCHITECTURE_ESCALATION_REQUIRED | Add live schema-6 rollback recomputation to the X4 production boundary under a new explicitly authorized packet. |
| P1-U05 | P1-U04 | NOT_STARTED | Pass generated direct-API and native callback compatibility probes. |
| P1-U06 | P1-U05 | NOT_STARTED | Pass the release-regression and exact execution/accounting semantics campaign. |
| P1-U07 | P1-U06 | NOT_STARTED | Produce three deterministic candidate runs and zero unexplained semantic drift. |
| P1-U08 | P1-U07 | NOT_STARTED | Produce exact promotion evidence, then stop for operator decision: PROMOTE_1_231 | HOLD_1_227. |

P1-U tasks advance only in dependency order. P1 product work remains blocked
until P1-U08 evidence is accepted and the operator explicitly approves
promotion. Until then, 1.227 stays active rollback and 1.231 stays
`CANDIDATE_CONTEXT_ONLY`.

The approved split-process architecture and receipt-finalization follow-up are
implemented through `ddbecbbdbb6f2f7ffd2389fc33b8e665faeddd7c`. Read-only
X4 replay passed U02 provenance, U03 toolchain, real Bubblewrap host authority,
exact schema-6 rollback, root absence/disjointness, and fresh spec review.
Fresh security/replay review nevertheless found one Important: the production
phase-A receipt validator does not recompute live rollback authority, so a
previously valid receipt can be replayed after 1.227 rollback drift. The two
implementation/review rounds are exhausted. The terminal outcome is
`P1_U04_ARCHITECTURE_ESCALATION_REQUIRED`. Receipt `dcc6f73422f12800a9167c1e1a00a3ef6ef165d0017398a0dababa168ec64d5a`
is rejected and grants no Build A authority. Native Build A/B, X5, activation,
and promotion remain unauthorized.
