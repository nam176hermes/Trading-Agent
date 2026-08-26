# P1-U04 X6 stable logical-stage implementation plan

> Packet circuit breaker: maximum two implementation/review rounds.

## Round 1

1. Freeze the failed Build A/B receipts, wheel/artifact hashes, exact member-difference classification, process identities, source identities, and unchanged rollback projection in ignored SDD evidence.
2. Add one RED regression in `tests/nautilus_upgrade/test_v1231_candidate_closure.py` requiring `_candidate_stage_token()` to return `stage-0000000000000000` consistently and match the reviewed `stage-[0-9a-f]{16}` policy shape.
3. Run only that regression and require an assertion failure caused by the current random token.
4. Change `_candidate_stage_token()` in `scripts/build_nautilus_engine.py` to return the fixed logical token. Do not change physical staging, FD handoff, process/source identity checks, environment authority, comparison rules, or publication.
5. Run the focused regression, related sandbox/build tests, then the full portable candidate-closure suite with `/tmp` and frozen/offline `uv`.
6. Commit exact source bytes and obtain fresh spec and security/replay reviews. Any Critical or Important finding blocks progression.
7. After review approval, record and recoverably remove only the failed task-owned Build A/B roots so X4 can prove an absent build parent. Preserve 1.227 byte-for-byte.
8. Re-run X4 preflight on the new exact HEAD/tree, reseal canonical and complete receipts, and obtain fresh X4 spec/security reviews.
9. Run one new receipt-bound Build A, review it, then one new separate-process Build B. Do not use forensic retention.
10. Accept X6 only if production comparison publishes final artifacts with raw wheel equality and native inventory equality.

## Round 2 fallback

If the fixed logical path is rejected or a fresh A/B pair still differs, diagnose the exact remaining bytes before editing. The only permitted fallback design is an exact policy-bound Rust `--remap-path-prefix` change with new RED controls and fresh reviews. No third build of the same candidate and no semantic exclusion of native bytes is permitted.

## Stop conditions

Stop immediately on unexplained executable/native drift, authority fallback, network access, partial publication, 1.227 mutation, review Critical/Important finding, or exhaustion of Round 2. The terminal circuit-breaker verdict is `P1_U04_ARCHITECTURE_ESCALATION_REQUIRED`.
