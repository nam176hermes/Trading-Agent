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
| P1-U04 | P1-U03 | X4_REPREFLIGHT_REQUIRED | Re-run exact external/native authority preflight and fresh reviews on the current HEAD/tree before any Build A. |
| P1-U05 | P1-U04 | NOT_STARTED | Pass generated direct-API and native callback compatibility probes. |
| P1-U06 | P1-U05 | NOT_STARTED | Pass the release-regression and exact execution/accounting semantics campaign. |
| P1-U07 | P1-U06 | NOT_STARTED | Produce three deterministic candidate runs and zero unexplained semantic drift. |
| P1-U08 | P1-U07 | NOT_STARTED | Produce exact promotion evidence, then stop for operator decision: PROMOTE_1_231 | HOLD_1_227. |

P1-U tasks advance only in dependency order. P1 product work remains blocked
until P1-U08 evidence is accepted and the operator explicitly approves
promotion. Until then, 1.227 stays active rollback and 1.231 stays
`CANDIDATE_CONTEXT_ONLY`.

The approved split-process architecture and receipt-finalization follow-up are
implemented through `ddbecbbdbb6f2f7ffd2389fc33b8e665faeddd7c`. The final
fresh implementation reviews report Critical 0, Important 0, and one
non-blocking fail-closed Minor. The current outcome is
`P1_U04_X4_REPREFLIGHT_REQUIRED`: X4 may now re-run on the exact current
HEAD/tree. Native Build A/B, X5, activation, and promotion remain unauthorized
until the new receipt and fresh X4 spec plus security/replay reviews pass.
