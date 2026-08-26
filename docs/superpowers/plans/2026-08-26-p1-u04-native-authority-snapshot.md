# P1-U04 immutable native-authority snapshot implementation plan

Packet limit: two implementation/review rounds.

1. Freeze RED evidence: production phase-B replay fails on the exact live
   `/usr/lib/x86_64-linux-gnu` inventory after libcurl 10.13 replacement.
2. Add focused portable tests for fixed source/destination mappings, receipt
   canonicality, sealed-tree rules, path escape, and mapped Bubblewrap mounts.
   Include a RED case where source-before equals source-after but a projected
   snapshot file differs; three-way equality must reject it.
3. Add the smallest materializer/verifier that copies only existing admitted
   native authority to the fixed external root, publishes atomically, and
   accepts no caller-supplied authority.
4. Change candidate mount construction from same-path mounts to exact
   source/destination pairs; use the sealed snapshot for CPython, stdlib,
   headers, libraries, GCC support, and admitted binutils.
5. Materialize the real-host snapshot offline, seal it, and bind its canonical
   receipt/tree digests into candidate engine policy and generated toolchain
   inputs.
6. Rerun complete X3 acceptance on exact governed bytes: focused/full U04
   tests, pin-inventory and governance reconciliation, `make ci-portable
   NONINTERACTIVE=1`, U03 verifier, source/policy diff checks, and fresh
   exact-byte spec plus security/replay reviews.
7. Run host authority verification and rollback projection, then obtain fresh
   round-1 host-authority spec and security/replay reviews. Fix only concrete
   findings in round 2 if required.
8. After both reviews PASS, recoverably remove only the stale replacement
   `build-a`, re-preflight/reseal X4 for the new exact commit/tree, and obtain
   fresh X4 reviews before any native build.
9. Redo X5 Build A and X6 Build B as separate processes. Publish final artifacts
   only if raw wheel and authoritative native inventory equality both PASS.

Forbidden: network, package install/downgrade, live `/usr` fallback, activation,
promotion, broker access, push, merge, deployment, live trading, or U05 work.
