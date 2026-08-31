# P1-A final review

Status: `P1_A_COMPLETE`

The reviewed source is commit
`080a0786c4e661bd23c48bbbaa5ec3758c23940c`, tree
`81ebb5c1551b5a1f2d2bcc5a4b5f33baa9849bdf`. Authorized integration produced
remote source `9444a0089a46916811cfddb83fc49eb3d26ae216`, tree
`a083bf4612ec63fd5ac5d6eb29e60670046c548b`; protected Foundation run
`33348201903` passed on that exact SHA. This closes P1-25 as
`P1_A_COMPLETE` without granting paper activation, deployment, production,
broker, exchange, network-trading, or live authority.

## Runtime and lineage

- Engine: Nautilus `1.231.0`, generation `NT1231-U04-G1`.
- G1 generation SHA-256:
  `2ea31eaca9cf19715fe2a73abc8c3d11c7731466e6e84e50e65db4979be46f8c`.
- Sealed G1 closure SHA-256:
  `24f12b58cb0aba145e6d56146a71be874c5d9b214e7426eead9711131eaf1255`.
- Schema-8 P1 product closure SHA-256:
  `b3bbb22552b896612ef93f78a61087d95fb1c061afb6102753e9f4d614b3963b`.
- Direct schema-8 closure attestation passed against the sealed G1 artifact
  directory and `/usr/bin/bwrap`.
- Legacy Phase4 Nautilus `1.227.0` schema-6 policies and the job-worker loader
  remain unchanged. The rollback runtime did not execute the accepted P1-A
  E2E result.

## Fresh exact-source evidence

The disposable Package6/PostgreSQL host lane migrated `0001` through `0017`
and ran the exact schema-8 vertical slice twice: once as the focused pytest
gate and once to capture a canonical receipt. PostgreSQL was stopped and its
approved fixture root was removed after each lifecycle.

| Evidence | Result |
|---|---|
| focused schema-8 E2E | `1 passed in 90.86s` |
| captured E2E receipt SHA-256 | `0b9284875e15ac209ed5120681a2f34c78ecefc730975137797288ef0ef4d588` |
| Package6 runtime authority SHA-256 | `3e465a58220ecd233daebeb8e042740678ea59ad542840ef6d2fa3e435d366b3` |
| result / batch SHA-256 | `30caf806477ad4d234363e5bf7513ee518ed98b88849bf4d8c9e6a4cb179222e` |
| engine event receipt SHA-256 | `3aea9082796620ece92c1ff2ca20cbb83c8e194ae04d3796e3b1795194ef96fa` |
| portfolio parity SHA-256 | `fbc73b40bf435ccc7e7106db3f01391fdd04679330eab6dd86c481ab8a4aadd2` |
| semantic digest | `bc4fdbfc9fbc5de0455a37158d243ae7026fc6cc5ff3e37a74686eee152a0f66` |
| final portfolio state SHA-256 | `de2d8dd92525e8942b92b9ff148405974b7ac0bce90d5c98f3fb2236ae474d99` |
| durable result | 14 events, sequence `2..15`, one worker run, `SUCCEEDED` |

The four real callback fills independently reconcile to a flat position,
fees `2007.791239`, realized PnL `9789.280866`, and final cash
`1007781.489627 USDT`.

The fresh adversarial receipt SHA-256 is
`3767e37e774865440972825d4677ad3b0653fa4ff70d03522918a8c2895b0688`;
its internal evidence SHA-256 is
`78d2996cd735350a44fb9557913f76c561986db71cb8267dfc285a585ec925f9`.
All eight scenarios passed, covering 205 tests with zero skips, failures, or
errors. Three custody-distinct runs retained semantic digest `bc4fdbfc…` and
portfolio digest `250d43c1…`.

The exact-source standalone portable gate exited zero:

- canonical audit, D0, P1 boundaries/lineage, pin inventory, generated
  contracts/protocol, secret hygiene, native custody, and toolchain checks:
  PASS;
- root: `8849 passed, 255 skipped, 31 deselected`;
- research backend: `507 passed, 2 skipped`;
- dashboard: `177 passed`, security integration, mode authorization,
  TypeScript, and ESLint PASS.

## Independent reviews

- Spec and engineering review: `PASS`, Critical `0`, Important `0`, Minor `0`.
- Security and integrity review: `PASS`, Critical `0`, Important `0`.

Both reviewers independently verified the exact commit/tree, schema-8
attestation, E2E and adversarial receipt hashes, durable accounting/custody,
unchanged rollback authority, and false live/network/production flags.

## Acceptance boundary

P1-26 consumed this exact local-source acceptance. Remote canonical
`P1_A_COMPLETE` is certified by the integrated SHA/tree above and Foundation
run `33348201903`
([GitHub Actions](https://github.com/nam176hermes/Trading-Agent/actions/runs/33348201903)).
The acceptance-document closure commit reports metadata and is not part of the
reviewed runtime tree above.
