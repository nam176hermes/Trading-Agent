# P1-U04 read-only namespace root implementation plan

Packet limit: two implementation/review rounds. Native build remains locked
until fresh X3 and X4 reviews PASS.

1. Freeze exact HEAD/tree, current task-owned diff, and the three reviewed
   external symlink path-target pairs.
2. Add RED portable tests for exact three-link admission, rejection of a fourth
   external link, final `--remount-ro /` ordering, and the exact negative and
   positive write probes.
3. Make the minimum implementation change: admit only the three reviewed dead
   links, add all three target probes, prove stage writability, and place final
   non-recursive `--remount-ro /` after the stage bind.
4. Run focused GREEN tests, adjacent sandbox/authority tests, and complete
   governed X3 portable acceptance.
5. Obtain fresh exact-byte X3 spec and security/replay reviews. Apply at most
   one bounded correction round, then repeat the affected verification and
   both reviews.
6. Only after X3 PASS, materialize and verify real external native authority,
   regenerate X4 preflight/receipt for the accepted exact commit/tree, and
   obtain fresh X4 spec and security/replay reviews.
7. Only after X4 PASS, run X5 Build A and X6 Build B as independent processes,
   then complete X7-X9 evidence and verdict without activation or promotion.

Stop with `P1_U04_ARCHITECTURE_ESCALATION_REQUIRED` if the packet exceeds two
implementation/review rounds.
