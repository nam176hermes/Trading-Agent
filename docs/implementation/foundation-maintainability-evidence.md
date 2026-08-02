# Foundation maintainability evidence

## Candidate scope

Package 05 removes false-success fallbacks from foundation paper and research paths, removes three warning families at their causes, and assigns residual broad boundaries. Track B P9 also closes review-found consumer, mode, numeric sizing, and native cgroup-recovery gaps. It does not authorize live trading, provider calls, deployment, runtime mutation, or strategy changes.

Current decision remains `NO-GO` until the final canonical CI command and independent re-review pass on the unchanged candidate.

## Track B P9 exception-taxonomy evidence

P9 selected the archive `execute_live.py` execution boundary from the broader
inventory. It did not perform a repository-wide broad-catch rewrite.

Recorded RED before the final source correction:

```text
4 failed, 10 deselected
```

The regressions cover an early batch dependency failure followed by a later
fill, unreadable sizing returns, GARCH degradation, and allocation degradation.
The resulting contracts preserve typed failure evidence, make mixed execution
`PARTIAL`, fail closed before order submission when required sizing input is
unreadable, and expose optional sizing degradation without raw exception text.

Fresh controller verification on the post-remediation source:

```text
focused policy suite: 152 passed
focused exception, authority, safety, and Phase 4 suites: 403 passed
full legacy suite: 495 passed, 2 skipped
```

`execute_live.py` now has three broad handlers, down from twelve. They are
restricted by an AST regression to `_call_execution_dependency`,
`_call_order_submission`, and `_call_observability_boundary`; each immediately
re-raises or returns a typed failure and none contains `pass`.

The bounded comprehensive P9 remediation additionally validates CCXT order
identifiers, CVaR returns and sizing output, dryrun/live signal input, durable
position and tier state, and tiered-exit decisions before arithmetic or order
submission. The focused policy suite mechanically compares every tracked broad
handler against the machine-readable inventory, including test and tooling
classifications. These controls preserve paper-only authority and do not
authorize a live call, deployment, or cutover.

The paid exact-candidate review found three additional live-execution gaps. Eight
RED parameter cases reproduced raw mode lookup exceptions, finite-input sizing
overflow, and loss of authoritative `PARTIAL` status at the downstream caller.
After remediation, the exact group passed. `main.py` now preserves bounded
execution evidence and does not invoke the secondary broker for `PARTIAL`.

A final controller audit found that direct preflight and tiered-exit submission
did not independently enforce the quantity cap. Four RED cases covered NaN,
infinity, a finite out-of-domain amount, and an oversized durable position.
The exact group now passes; durable state, preflight, and the final tiered sell
boundary each reject malformed or unbounded quantities before submission.

The independent rereview then identified non-zero subnormal floats as a gap in
the shared numeric predicate. Four RED cases proved reachability through direct
preflight, single execution, batch execution, and tiered durable state. The
central validator now rejects every non-zero float below the minimum normal
value before sizing or submission; the exact group and prior finite-overflow
regression pass together.

The next exact-candidate rereview verified candidate identity and the raw diff
seal, then found four remaining consumer contracts: ticker, balance, and fill
evidence did not all use the normal-float predicate; controller price and
confidence were coerced before validation; a status-only primary fill could
reach the secondary broker; and a status-only secondary broker result was
preserved. Eight adversarial cases recorded seven failures and one already-safe
rationale case. After remediation, the expanded exact boundary group recorded
42 passes. Controller inputs are now validated before conversion, primary fill
acceptance returns an effective safe status, and secondary execution requires
per-status bounded order evidence. Eight additional field-level corruptions for
primary and secondary numeric evidence pass, bringing the Phase 4 suite to 227
passes on that V4 source.

The V4 exact-candidate review passed identity and scope checks but found one
remaining HIGH boundary: positive subnormal GARCH forecasts could expand order
sizing and positive subnormal allocation targets could trim it. Two focused RED
cases reproduced `COMPLETED` for GARCH and a shares mutation from `1.0` to `0.5`
for allocation. Both validators now use the shared normal-float predicate with
explicit zero semantics. The two exploit cases and two zero-contract cases pass
together.

The V5 exact-candidate review returned `FAIL` with four findings. One was a seal
terminology ambiguity: it compared the semantic candidate-identity payload with
the raw Git diff even though both independently matched their declared
algorithms. Subsequent review packets label those algorithms separately and
list excluded pre-existing untracked roots. The three substantive findings were
reproduced by four RED cases: the producer's canonical persistence `PARTIAL`
envelope was rejected, confirmed dryrun and live fills could reach the secondary
broker route, and a tiered exit below `MIN_ORDER_USD` could reach submission.
The consumer now validates complete nested or complete outer fill evidence,
secondary broker execution is paper-only after a complete paper audit, and
tiered exits enforce bounded minimum notional before submission. The exact
remediation group is 5 passed. Current totals are 152 focused policy passes,
230 Phase 4 passes, 403 controller passes, and 495 full-backend passes with two
skips. `_execute_asset` complexity decreased from 8 to 7 after the broker-route
predicate was extracted and independently ratcheted at complexity 3.

The same review required resolution of a Package 6 native authority failure.
A stress run reproduced `restart_removal_intent_replacement_rejected` at
iteration 66 under `umask 0002`. The removal-intent journal binds only device
and inode, which can be reused after deletion. Restart recovery now fails closed
whenever either the canonical or quarantine cgroup path remains present; only
an already-absent path can finalize automatically. This trades restart cleanup
availability for protection against deleting a same-name foreign replacement.

## Test-first evidence

Recorded RED baselines:

| Scope | Result before remediation |
|---|---:|
| Initial failure-mode suite | 10 failed in 1.12s |
| Initial warning-governance suite | 4 failed in 3.07s |
| Backtest and paper execution regressions | 2 failed in 1.35s |
| Broker regression | 1 failed in 1.36s |
| Earnings and strategy-state residuals | exit 1 |
| Portfolio state, correlation, and fallback residuals | 3 failed |
| P9 paid-review execution boundary group | 8 failed |
| P9 final quantity-boundary group | 4 failed |
| P9 independent-rereview subnormal group | 4 failed |
| P9 V3 consumer and broker-schema group | 7 failed, 1 passed in 2.56s |
| P9 V4 enrichment subnormal group | 2 failed in 1.14s |
| P9 V5 consumer, routing, and tiered-notional group | 4 failed in 2.26s |
| Package 6 replacement stress | Failed at iteration 66, `test_authority.c:5797` |

An earlier portfolio and risk residual group of seven regressions passed after remediation:

```text
7 passed in 0.31s
```

After merging Package 05 coverage into the tracked preserved test file:

```text
208 passed in 11.19s
```

## Implemented controls

- Reflection provider, price, benchmark, and persistence outcomes are typed.
- Benchmark absence is `None` with unavailable metadata.
- Risk persona failure rejects with zero position size.
- RiskDebate failure never creates legacy approval, including research-only mode.
- Corrupt incubation and strategy-risk state close their gates and preserve bytes.
- Portfolio Manager validation, swarm, context, and correlation failures reach the typed pipeline boundary.
- Portfolio Manager compatibility fallback is reject, `HOLD`, zero size, and zero conviction.
- Corrupt paper portfolio state is not converted into a clean portfolio.
- Missing market prices do not fall back to average cost.
- Safety and stop sweeps require complete price coverage for complete status.
- Batch, persistence, and audit failures retain typed non-success status.
- Risk validation does not create synthetic history or pseudo walk-forward evidence.
- Optional research loss marks both the dependency and aggregate report partial or unavailable.
- Portfolio Manager, backtest, paper executor, and broker failures cannot preserve successful execution state.
- Reflection batches remain `PARTIAL` when any stored reflection has unavailable benchmark coverage.
- Paper portfolio mutation is persisted before secondary order and journal evidence. Audit failure preserves the fill, marks aggregate status `PARTIAL`, and blocks the secondary broker. Failed portfolio persistence writes no fill audit.
- Safety stop execution requires `audit_status=COMPLETED` before the safety job can complete. Partial, missing, or invalid audit status preserves the durable fill in `executed_symbols`, records a typed failure and trace, returns `PAPER_STOP_AUDIT_INCOMPLETE`, and makes the CLI nonzero.

## Independent review findings and closure

The first final read-only review returned `NO-GO` with two high findings:

1. Partial reflection items were promoted to aggregate `COMPLETED`.
2. Paper fill and stop audit writes had ambiguous ordering around authoritative portfolio persistence.

Six RED regressions reproduced both failures. After remediation, the same exact command recorded:

```text
6 passed in 1.28s
```

The combined Package 05 and paper-trader suites then recorded:

```text
229 passed in 11.14s
```

A second read-only review closed those two findings but found one additional high blocker: `safety_engine.py` accepted a durable paper fill with incomplete secondary audit evidence as `COMPLETED`. Two parameterized safety cases reproduced the defect while direct `execute_batch()` propagation passed:

```text
2 failed, 1 passed in 0.99s
```

After remediation, the exact group recorded:

```text
3 passed in 0.61s
```

The tracked Package 05 file and full legacy suite then recorded:

```text
208 passed in 11.19s
337 passed, 2 skipped in 16.05s
```

A fresh independent re-review on the post-safety candidate is required before GO.

## Exception inventory

Fresh controller AST result:

| Metric | Result |
|---|---:|
| Tracked Python files | 413 |
| Broad handlers | 416 |
| Affected files | 116 |
| Parse errors | 0 |
| `Exception` handlers | 346 |
| `BaseException` handlers | 70 |
| Bare handlers | 0 |

The earlier Package 05 read-only Codex ownership audit classified its
then-current source as follows; these are retained as historical semantic
evidence and are not substituted for the refreshed P9 mechanical totals:

| Class | Count |
|---|---:|
| `SAFETY_CRITICAL` | 59 |
| `DATA_CORRECTNESS` | 41 |

Observability-only alert catches were excluded from those historical counts.
P9 adds an explicit boundary-intent taxonomy and updates the selected archive
execution boundary in `foundation-exception-inventory.md`.

## Live residual boundary

`allocation_engine.py`, `portfolio_optimizer.py`, `execute_live.py`, and `trading_agent.py` are excluded from the canonical paper projection. `execute_live.py` and `trading_agent.py` are explicit forbidden artifact paths.

P9 closes the selected `execute_live.py` exception-taxonomy boundary and the
bounded review-found Package 6 restart-removal identity defect only.
Remaining autonomous-agent, exchange, broker, monitoring, packaging, and
activation work remains Release Authority v2 debt. Any live activation
invalidates this deferral and requires a separate reviewed plan and approval.

Supporting evidence:

- `docs/implementation/foundation-live-path-inventory.md`
- `docs/implementation/foundation-live-boundary-evidence.md`
- `docs/implementation/foundation-paper-release-exclusion.md`

## Fresh verification matrix

| Gate | Command | Result |
|---|---|---|
| Earlier portfolio and risk residual suite | Seven exact regressions | 7 passed in 0.31s |
| Safety audit-consumer and direct batch regressions | Exact three-case group | 3 passed in 0.61s |
| Package 05 and paper-trader suites | `pytest -q tests/test_phase4_research_only.py tests/test_paper_trader.py` | 229 passed in 11.14s |
| Tracked Package 05 test file | `uv run --frozen --extra test pytest -q tests/test_phase4_research_only.py` | 208 passed in 11.19s |
| Full legacy suite | `uv run --frozen --extra test pytest -q` | 337 passed, 2 skipped in 16.05s |
| P9 V3 remediation boundary group | Exact fill, ticker, balance, raw controller, primary confirmation, and broker-schema regressions | 42 passed in 1.90s |
| P9 field-level primary and secondary evidence group | Exact corrupted shares, fill price, quantity, fill quantity, and fill-price cases plus status-only broker result | 9 passed in 1.19s |
| P9 V4 enrichment remediation group | Exact GARCH/allocation positive-subnormal cases plus explicit zero semantics | 4 passed in 0.89s |
| P9 V5 remediation group | Actual persistence `PARTIAL`, dryrun/live no-secondary routing, paper broker schema, and minimum tiered notional | 5 passed in 1.79s |
| P9 focused policy suite | `uv run --frozen --extra test pytest -q tests/test_live_execution_policy.py` | 152 passed in 3.07s |
| P9 Phase 4 suite | `uv run --frozen --extra test pytest -q tests/test_phase4_research_only.py` | 230 passed in 10.51s |
| P9 focused controller suite | `uv run --frozen --extra test pytest -q tests/test_phase1_safety.py tests/test_phase4_research_only.py tests/test_live_execution_policy.py` | 403 passed in 12.91s |
| P9 full legacy suite | `uv run --frozen --extra test pytest -q` | 495 passed, 2 skipped in 17.84s |
| P9 `execute_live.py` broad-catch ratchet | Deterministic AST scan plus regression | 12 to 3; only named typed wrappers remain |
| Package 6 restart-removal stress | 4 exact cases x 100 iterations x `umask` 0002, 0022, 0077 | 1200 passed in 41.509s |
| Package 6 full native suite | `umask 0002; make --no-print-directory -s -C native/package6_custodian test` | Exit 0; all listed protocol, authority, and publication cases passed |
| Package 6 exact root contract | Root pytest exact node under `umask 0002` | 1 passed in 24.36s |
| Preserved snapshot contract | `uv run pytest -q tests/consolidation/test_backend_snapshot.py` | 2 passed in 0.70s |
| Warning governance and generation tests | Root focused pytest | 6 passed in 8.48s |
| Contract generation | `make check-contracts` | Exit 0 |
| Dashboard tests | `npm test` | 158 passed, 0 failed |
| Dashboard build | `npm run build` | Exit 0 |
| Dependency delta | Parsed `HEAD:uv.lock` against candidate | 3 added, 0 removed, 0 version-changed |
| Diff whitespace | `git diff --check` | Exit 0 before final docs; rerun required |
| Root canonical gate | `make ci` | Required as external candidate-bound evidence after the final documentation bytes; it is not stored as a self-approving permanent `PASS` in this file |
| Independent P9 review | Read-only exact-candidate Codex audit | Required as external evidence after final canonical CI; the report must bind the same candidate digest |

## Dependency delta

Added packages:

- `httpx2==2.9.1`
- `httpcore2==2.9.1`
- `truststore==0.10.4`

No package was removed or version-changed. FastAPI `0.139.0`, Starlette `1.3.1`, `httpx 0.28.1`, Pydantic `2.13.4`, and AnyIO `4.14.1` remain unchanged.

## Final delta requirements

Before GO:

- run fresh `git diff --check`;
- inspect every changed and untracked path;
- review full rewrites of `reflection_engine.py` and `incubation_tracker.py`;
- inspect dependency and generated-contract deltas;
- confirm no credentials, runtime reports, databases, caches, virtual environments, build output, or dependency directories are present;
- run fresh canonical `make ci` after all source and documentation changes;
- retain final independent review verdict.

## Decision rule

Use:

```text
GO: FOUNDATION FAILURE MODES ARE EXPLICIT
```

only when all final gates exit zero on unchanged source bytes, no reachable false-success residual remains, and every archive residual has an owner and closure condition.

Otherwise use:

```text
NO-GO: SILENT FAILURE RISK REMAINS
```
