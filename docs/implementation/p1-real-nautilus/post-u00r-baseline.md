# P1 post-U00R baseline receipt

Captured on 2026-08-22 for Gate 0. This receipt establishes the source baseline
for P1-U01; it is not Nautilus artifact qualification or promotion evidence.

## Exact authorities

| Authority | Exact value |
|---|---|
| Accepted Task-1 base A | `f15b1985215ef4d018f48c712221920502379a48` |
| Reviewed source commit S | `3257fb0c9dbff689212ca0a0b011704596477426` |
| Source tree | `2e83bc2e811adf5d51a2a67a448d3efbadae3982` |
| Inventory commit and canonical main I | `c174ba9de21c91dcda53ebcb825fdd1597e8800c` |
| Canonical tree | `5923113c38caf23f888f1d35e316c0d4a241103d` |
| Historical blocked snapshot | `326e97faf63c4f4ddf8fcb8e6a4087bc40c99bf2` |
| Threat model | `U00R_TRUSTED_HOST_COOPERATIVE_GIT_V1` |

Fresh Git verification proved `HEAD == origin/main == I`, `HEAD^{tree}` is the
canonical tree, and the starting index/worktree were clean. The accepted
lineage is exactly A -> S -> I: I has S as its sole parent, S has the recorded
source tree, and S..I adds only mode-`100644`
`docs/implementation/p1-real-nautilus/upgrade/pin-inventory.json`. The blocked
snapshot is not an ancestor of I and is not accepted program ancestry.

The exact-commit inventory verifier passed against S and I. It verified
`nautilus-pin-inventory/v4`, source tree
`2e83bc2e811adf5d51a2a67a448d3efbadae3982`, and threat model
`U00R_TRUSTED_HOST_COOPERATIVE_GIT_V1`; the inventory SHA-256 is
`b9960c1153bfb89b10a0f3783d0afa5eb67af3fea2f805d8d37702b41bd4a0f9`.

## Verification evidence

The exact I tree passed:

- `make check-p0-baseline`
- `make check-p0-maintainability`
- `make check-secrets`
- focused U00R governance: `1318 passed`
- standalone `make ci-portable NONINTERACTIVE=1`: root `7549 passed`; final
  governance `8344 passed, 2 skipped, 29 deselected, 17 approval-blocked, 0
  failed/not-run`; the 29 governed deselections are 17 `runtime_postgres` and
  12 `host_coupled`;
  critical coverage `1361 passed, 44 skipped, 5 deselected`; artifact
  publication passed

The standalone CI evidence retains the expected authority classification:
portable source/root and native capabilities passed, external authorities are
`DEFERRED`, and production PostgreSQL mutation remains `FORBIDDEN`.

GitHub `main` branch protection was freshly re-queried and requires the
GitHub-Actions `verify` check (app ID `15368`); enforcement for administrators
is enabled. Exact canonical-I remote evidence is Foundation run
[`32601863328`](https://github.com/nam176hermes/Trading-Agent/actions/runs/32601863328),
event `push`, conclusion `success`, created `2026-08-22T22:14:06Z`, completed
`2026-08-22T22:36:03Z`. Its `verify` job
[`97101237252`](https://github.com/nam176hermes/Trading-Agent/actions/runs/32601863328/job/97101237252)
ran from `2026-08-22T22:14:09Z` through `2026-08-22T22:36:02Z` and concluded
`success` for exact head SHA I.

## Accepted integration topology

- Integration branch `p1/nautilus-v1231-rebaseline-r2` at
  `/home/thenam176/projects/trading-agent-worktrees/p1-nautilus-v1231-rebaseline-r2`
- Task branch `task/p1-g0-post-u00r-rebaseline` at
  `/home/thenam176/projects/trading-agent-worktrees/p1-g0-post-u00r-rebaseline`

## Engine and safety boundary

NautilusTrader `1.227.0` at
`280ae1762df51a492a4ce71506a40b5c8706def5` remains the active rollback.
NautilusTrader `1.231.0` at
`27a8e54e7ac3c57d6cbf8891f0283dfbaee97317` remains
`CANDIDATE_CONTEXT_ONLY` until later qualification and explicit promotion.

U00R is cooperative-Git source authority only. It does not claim hostile
same-UID resistance, artifact qualification, engine promotion, runtime
authority, production readiness, broker/exchange access, or live trading.
Network trading and both live approvals remain disabled.
