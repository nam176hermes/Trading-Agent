# Foundation maintainability evidence

## Candidate scope

Package 05 removes false-success fallbacks from foundation paper and research paths, removes three warning families at their causes, and assigns residual broad boundaries. It does not authorize live trading, provider calls, deployment, runtime mutation, or strategy changes.

Current decision remains `NO-GO` until the final canonical CI command and independent re-review pass on the unchanged candidate.

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
| Production Python files | 241 |
| Broad handlers | 365 |
| Affected files | 97 |
| Parse errors | 0 |

Fresh read-only Codex ownership audit independently reproduced those totals:

| Class | Count |
|---|---:|
| `SAFETY_CRITICAL` | 59 |
| `DATA_CORRECTNESS` | 41 |

Observability-only alert catches are excluded from those counts. The safety audit-consumer finding was not a broad handler and did not change the mechanical totals. The targeted source and regressions now fail closed for incomplete paper audit evidence; final independent confirmation remains pending. Archive residuals remain assigned in `foundation-exception-inventory.md`.

## Live residual boundary

`allocation_engine.py`, `portfolio_optimizer.py`, `execute_live.py`, and `trading_agent.py` are excluded from the canonical paper projection. `execute_live.py` and `trading_agent.py` are explicit forbidden artifact paths.

Their sizing, order, and monitoring fallbacks remain unresolved Release Authority v2 debt. Any live activation invalidates this deferral and requires a separate reviewed plan and approval.

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
| Preserved snapshot contract | `uv run pytest -q tests/consolidation/test_backend_snapshot.py` | 2 passed in 0.67s |
| Warning governance and generation tests | Root focused pytest | 6 passed in 8.48s |
| Contract generation | `make check-contracts` | Exit 0 |
| Dashboard tests | `npm test` | 158 passed, 0 failed |
| Dashboard build | `npm run build` | Exit 0 |
| Dependency delta | Parsed `HEAD:uv.lock` against candidate | 3 added, 0 removed, 0 version-changed |
| Diff whitespace | `git diff --check` | Exit 0 before final docs; rerun required |
| Root canonical gate | `make ci` | Pending fresh final run |
| Independent ownership review | Read-only exact-source Codex audit | Two earlier high findings and the later safety audit-consumer finding are remediated; fresh post-safety re-review pending |

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
