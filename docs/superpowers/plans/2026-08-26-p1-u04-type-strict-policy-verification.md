# P1-U04 type-strict policy verification plan

Packet limit: two implementation/review rounds.

1. Freeze exact commit/tree and the accepted type-confusion mutation ledger.
2. Add RED tests through `_verify_policies()` for Python boolean/integer and
   float/integer substitutions, plus a native snapshot schema boolean mutation.
3. Add one recursive type-strict JSON equality primitive and apply it only to
   the exact Python-policy and native-snapshot comparisons in scope.
4. Regenerate toolchain inputs using the repository generator and run focused,
   full U04, host-authority, and available portable gates.
5. Obtain fresh exact-byte X3 spec and isolation/replay reviews. Apply at most
   one bounded correction round.
6. Only after X3 PASS, re-preflight and obtain fresh X4 reviews before any
   native build.
