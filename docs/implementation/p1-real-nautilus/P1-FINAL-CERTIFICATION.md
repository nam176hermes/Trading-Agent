# P1 Final Certification

Status: `P1_COMPLETE`

P1-A and P1-B are complete for the qualified local source candidate. The
qualification binds Nautilus `1.231.0`, generation `NT1231-U04-G1`, schema-8
product closure
`97185d4c0b6090353ba51c1aab25ed4ea4dfab08113b655fac623af9e7db2b80`, and
paper protocol `nautilus-paper-session-v2`. Authorized integration produced
remote source `9444a0089a46916811cfddb83fc49eb3d26ae216`, tree
`a083bf4612ec63fd5ac5d6eb29e60670046c548b`, and protected Foundation run
`33348201903` passed on that exact SHA.

## Qualification authority

- Qualification source: `fc34bc52d5af5312303abac417502d7419247ba7` /
  `cc4f3a628b4e7b0faacac47b6ea41ef3065192b3`.
- Integrated remote source: `9444a0089a46916811cfddb83fc49eb3d26ae216` /
  `a083bf4612ec63fd5ac5d6eb29e60670046c548b`.
- Protected Foundation run `33348201903`
  ([GitHub Actions](https://github.com/nam176hermes/Trading-Agent/actions/runs/33348201903))
  passed and published sealed portable evidence.
- Local paper qualification: 69 tests passed, zero skipped; evidence digest
  `9304ece63679d9546dd7570acd93aaf4008e93fc9d65141de21e266a5c63ab06`.
- Qualification receipt digest:
  `eb65e9631fa0cd04a1e10fcc16af2532dd55f3de3759ecae548ef9490349adc3`.
- Exact G1 adversarial qualification: 205 tests passed, zero skipped; the
  native qualification target passed 12 tests, including five exact G1
  backtest/paper parity cases.
- Protected Foundation aggregate: 9,570 passed, two skipped, and 36
  deselected. The backend passed 507 tests with two skips; dashboard passed
  177 tests plus security integration, mode authorization, typecheck, and lint.
- Legacy Phase4 profiles remain Nautilus `1.227.0` / schema 6 and unchanged.
- Independent specification and security/integrity reviews passed with zero
  Critical and zero Important findings.

## Authority boundary

- `PAPER_LOCAL_ONLY`
- `NETWORK_DISABLED`
- `LIVE_NOT_AUTHORIZED`
- `PRODUCTION_NOT_AUTHORIZED`

This certification does not authorize activation, deployment, broker or
exchange access, database mutation, or any production action.
`P1_COMPLETE` is certified for the protected remote source above. The
certification closure commit reports integration metadata and does not change
the reviewed runtime, candidate, schema-8 closure, or qualification receipts.

Release Authority v2 and production cutover remain separate future work and
are not P1 blockers.
