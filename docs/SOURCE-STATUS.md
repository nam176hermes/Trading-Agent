# Source status

The referenced ChatGPT conversation named two generated archives:

- `nta-1-blueprint.zip`
- `nta-1-upgrade-plan.zip`

Their links used the session-local `sandbox:/mnt/data/...` scheme. Those files
were not present in `/home/thenam176`, the Windows Downloads folder, `/mnt/data`,
or `/tmp` when this workspace was prepared. A session-local sandbox link is not
a public download URL and cannot be recovered from the conversation identifier.

The documentation in this repository is therefore a transparent
reconstruction from the full conversation text supplied by the user. It
captures the architecture, safety constraints, contracts, phases, acceptance
gates, and the task dependency chain needed to begin Phase 0. It is not claimed
to be a byte-for-byte copy of either original ZIP.

The original conversation said the upgrade archive contained 69 backlog items,
but their complete rows were not included in the shared text. The local
`BACKLOG.csv` is intentionally a bootstrap subset and labels itself as such.

If the original ZIP files are recovered later, place them outside Git, compare
their checksums and contents, then merge missing material through a reviewed
documentation change. Do not overwrite audit results or implementation history.

## Current canonical source authority

The AUTH0/R0 source consolidation uses the following reviewed lineage:

- canonical base: `e2aca4b6dd6a02ca3a8db86c9c22bcb51573e59e`;
- equivalent recovery base: `e7141221423cc8d4fb3acfd757275e6d9eb69140`;
- shared base tree: `b81625a58f307b7ae5503f6d56f87e21d5f1776b`;
- reviewed job-plane authority head: `e6c95fd5302a0dfcbae44322160c8992b832fb3f`; <!-- gitleaks:allow -->
- reviewed authority tree: `1f553f9a80693e39229899049a56948d2d637784`. <!-- gitleaks:allow -->

The identical base trees were proven before recording the recovery ancestry.
The staged authority import was then required to equal the reviewed authority
tree exactly. The secret-risk example `ops/systemd/job-api.env.example` was not
imported.

After the reviewed AUTH0/R0 integration line is merged, this repository is the
single source authority for the control plane, job plane, research backend and
dashboard. Source authority does not create runtime authority. Release
installation, PostgreSQL recovery or migration, service changes, scheduler
changes, paper-production promotion and live trading remain separate,
approval-gated operations.

## Current source status (2026-08-03)

The checkout contains a paper-only, unsealed working candidate after P10
canonical-market-data and Packet 9 coverage work. It includes migration `0009`
and its source tests, but no source change creates runtime authority. In
particular, this does not build or activate Release Authority v2, modify the
historical production baseline, change `promotion-status.json` to `GO`, run a
production PostgreSQL migration, restart a service, enable a scheduler, or
enable live execution/trading.

Dashboard ownership is recorded in
[the route inventory](production/dashboard-route-inventory.md). The inventory
is a static source map; an endpoint marked `typed unavailable` is deliberately
not a canonical data source.
