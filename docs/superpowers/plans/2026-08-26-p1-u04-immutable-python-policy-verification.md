# P1-U04 immutable Python policy verification plan

Packet limit: two implementation/review rounds.

1. Freeze exact commit/tree and reproduce the full-generator live `/usr` call.
2. Add RED coverage on the real `_verify_policies()` route plus Python/snapshot
   cross-binding mutations.
3. Replace only the live Python verification call with exact structural and
   snapshot-mapping validation; retain host/runtime `verify_and_open()`.
4. Regenerate toolchain inputs with the repository generator and run focused,
   full U04, host-authority, and available portable gates.
5. Obtain fresh exact-byte X3 spec and isolation/replay reviews. Apply at most
   one bounded correction round.
6. Only after X3 PASS, regenerate/review X4 before any native build.
