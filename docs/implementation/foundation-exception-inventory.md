# Foundation exception inventory

## Scope and method

Package 05 covers tracked production Python that can affect paper safety, research correctness, durable state, or externally visible status.

The inventory uses `git ls-files '*.py'`, excludes paths containing a `tests` component and excludes `scripts/`, then parses each file with Python `ast`. A broad handler catches bare `except`, `Exception`, or `BaseException`, including those names inside tuples.

Current post-remediation measurement:

| Metric | Count |
|---|---:|
| Tracked production Python files | 241 |
| Broad handlers | 365 |
| Files containing broad handlers | 97 |
| Parse errors | 0 |
| First control-flow marker: re-raise | 91 |
| First control-flow marker: return | 101 |
| First control-flow marker: pass | 31 |
| First control-flow marker: continue | 16 |
| Other or log-only handler | 126 |

The control-flow marker is an inventory aid, not a semantic verdict. Package 05 does not require zero broad handlers. It requires every safety or correctness handler to be remediated or assigned a boundary, owner, and closure condition.

## Ownership counts

A fresh read-only Codex audit independently reproduced the AST totals and manually classified the exact current source.

| Class | Count | Alert catches included? |
|---|---:|---|
| `SAFETY_CRITICAL` | 59 | No |
| `DATA_CORRECTNESS` | 41 | No |

Observability-only Telegram and secondary telemetry handlers are excluded from those two counts. Examples include `enforce_stops.py:426` and `safety_engine.py:478,516`.

## Required behavior

| Class | Required behavior |
|---|---|
| `SAFETY_CRITICAL` | Fail closed with typed status, reason code, and trace ID. Never manufacture approval, nonzero sizing, completed execution, complete stop coverage, or a safe result. |
| `DATA_CORRECTNESS` | Preserve source bytes and expose failure. Never manufacture clean state, a benchmark, a successful write, or valid evidence. |
| `RESEARCH_QUALITY` | Continue only when the dependency and aggregate report are explicitly `PARTIAL` or `UNAVAILABLE`. |
| `OBSERVABILITY_ONLY` | Preserve authoritative action status and emit a bounded warning. |
| `BENIGN_BOUNDARY` | Convert uncontrolled external exceptions to typed results or re-raise. |

## Package 05 remediations

| Boundary | Current behavior | Owner |
|---|---|---|
| `reflection_engine.py` provider, price, benchmark, persistence, and batch paths | Typed unavailable or partial results; no zero price; alpha is `None` when benchmark evidence is unavailable; failed persistence does not mark a decision reflected; aggregate status remains `PARTIAL` when any stored item is partial | Foundation maintainers |
| `risk_personas.py:RiskDebate._persona_turn` | Provider or parser failure returns `UNAVAILABLE`, rejects the signal, and sets size to zero | Foundation maintainers |
| `risk_personas.py:apply_stock_risk_rules` | Unexpected earnings-date failure is no longer swallowed and reaches the typed RiskDebate boundary | Foundation maintainers |
| `strategy_risk_manager.py` state load | Corrupt state raises `StrategyRiskStateUnavailable`; `is_strategy_allowed` denies the strategy and preserves bytes | Foundation maintainers |
| `portfolio_manager.py` decision boundary | Validation, swarm, corrupt portfolio, and corrupt correlation state propagate to the typed pipeline boundary; compatibility fallback is reject, `HOLD`, and zero size | Foundation maintainers |
| `incubation_tracker.py` state paths | Corrupt or unavailable state closes the gate and is not overwritten | Foundation maintainers |
| `paper_trader.py` state, valuation, stop, batch, and audit paths | Typed portfolio errors, price coverage, stop result, batch status, and atomic persistence; portfolio state is persisted before secondary order/journal evidence; audit failure preserves the durable fill and makes aggregate status `PARTIAL`; failed portfolio persistence writes no fill audit | Foundation maintainers |
| `risk_validation.py` | No synthetic history; unavailable history is `UNAVAILABLE`; missing walk-forward evidence is `PARTIAL/UNKNOWN` | Foundation maintainers |
| `safety_engine.py` | `safe=true` only for `COMPLETED`, full coverage, and no triggered stop; a durable paper stop fill requires `audit_status=COMPLETED`, while partial or invalid audit evidence preserves the fill and makes the safety job `PARTIAL` | Foundation maintainers |
| `enforce_stops.py` | State, report, threshold, batch, and persistence failures are typed non-success results; CLI returns nonzero | Foundation maintainers |
| `main.py` Portfolio Manager, RiskDebate, backtest, paper, and broker boundaries | Mandatory dependency failure blocks execution; research-only RiskDebate failure may continue only as `PARTIAL` without approval | Research pipeline owner |
| `assembly.py` optional sources | Source availability, reason, trace, and aggregate partial status are explicit | Research pipeline owner |

## Remaining SAFETY_CRITICAL handlers

Reachability labels:

- `PAPER`: canonical paper artifact.
- `LEGACY`: preserved noncanonical legacy path.
- `ARCHIVE`: excluded from the canonical paper artifact and command graph.

| File and lines | Symbol or boundary | Reachability | Disposition | Owner and closure condition |
|---|---|---|---|---|
| `allocation_engine.py:161,187,475` | Backtest cache, yfinance, target weight | ARCHIVE | JUSTIFIED | Release Authority v2. Add typed unavailable allocation and remove retained or neutral targets before activation. |
| `broker.py:94,150,164,178,192` | Account and order operations | ARCHIVE | JUSTIFIED | Release Authority v2. Complete broker failure contract before live approval. |
| `exchange/adapter.py:250,332,343` | OCO, retry, connection | ARCHIVE | JUSTIFIED | Release Authority v2. Protective-order and connection failures must be typed. |
| `exchange/ccxt_bridge.py:131,150,196,215,231,241,281` | Balance, ticker, order, cancel, connection, quote | ARCHIVE | JUSTIFIED | Release Authority v2. Complete exchange outcome contract before activation. |
| `exchange/executor.py:120` | `OrderExecutor.execute` | ARCHIVE | JUSTIFIED | Release Authority v2. Failure report must not contain fill or success semantics. |
| `exchange_health.py:57,63,128,139,149,190` | Client and health boundaries | ARCHIVE | JUSTIFIED | Release Authority v2. Unavailable health must block live execution. |
| `exchange/secrets.py:94,211` | Key load and CLI | ARCHIVE | JUSTIFIED | Release Authority v2. Missing or invalid credentials remain explicit denial. |
| `execute_live.py:210,244,291,328,500,531,538,558,622,725,736,850` | Sizing, allocation, execution, summary, monitoring | ARCHIVE | UNRESOLVED | Release Authority v2. Remove valid-looking fallback and add typed outcomes before any live activation. |
| `enforce_stops.py:332` | Stop batch exception boundary | LEGACY | REMEDIATED | Foundation maintainers. Retain `UNAVAILABLE/PAPER_BATCH_EXECUTION_FAILED`. |
| `portfolio_manager.py:571` | Swarm decision boundary | LEGACY | REMEDIATED | Foundation maintainers. Retain re-raise and typed caller rejection. |
| `portfolio_optimizer.py:41,457,477,495,515` | Covariance and CLI/report boundaries | ARCHIVE | JUSTIFIED | Release Authority v2. Covariance failure cannot manufacture an allocatable matrix before activation. |
| `risk_personas.py:440` | Persona provider and parser boundary | LEGACY | REMEDIATED | Foundation maintainers. Retain typed unavailable, rejection, and zero size. |
| `set_mode.py:79,96` | Mode status | ARCHIVE | JUSTIFIED | Release Authority v2. Invalid mode or kill-switch state must deny activation. |
| `trading_agent.py:273,371,426,441,456,484,509,664,709,916` | Autonomous loop, gates, sizing, execution, latency | ARCHIVE | UNRESOLVED | Release Authority v2. Complete live and autonomous-path review before packaging or activation. |

## Remaining DATA_CORRECTNESS handlers

| File and lines | Symbol or boundary | Reachability | Disposition | Owner and closure condition |
|---|---|---|---|---|
| `alpha_arena.py:80` | Round creation | ARCHIVE | UNRESOLVED | Research pipeline owner. DB failure must not fabricate round identity. |
| `alpha_backtest.py:80,409,420,751` | Time parsing, comparison, CLI | ARCHIVE | UNRESOLVED | Research pipeline owner. Invalid evidence becomes typed partial or unavailable. |
| `daily_report.py:111,177` | Sortino and report boundary | ARCHIVE | UNRESOLVED | Research pipeline owner. Undefined metrics and report-write failure must be explicit. |
| `db/repository.py:41` | Transaction | ARCHIVE | JUSTIFIED | Data-store owner. Retain rollback and re-raise. |
| `dl_predictor.py:367,543` | Checkpoint and evaluation | ARCHIVE | JUSTIFIED | Model owner. Unsafe checkpoint or evaluation failure remains unavailable. |
| `job_attribution.py:163,169,192,462` | Directory and exclusive-write boundaries | PAPER | JUSTIFIED | Job-plane owner. Retain fail-closed path checks, create-only writes, and re-raise. |
| `local_artifacts.py:168,197` | Exclusive and atomic writes | LEGACY | JUSTIFIED | Foundation maintainers. Retain cleanup and re-raise; never persist partial bytes. |
| `main.py:1483,1503,1510,1612,1648,1744,1753,1759,1765,1770,1776` | Report, debate, typed decision, mode snapshot | ARCHIVE | UNRESOLVED | Research pipeline owner. Persistence and invalid state must reach aggregate job status before this path becomes canonical. |
| `memory.py:421` | Typed-decision persistence | ARCHIVE | JUSTIFIED | Research pipeline owner. Every caller must treat `False` as failure. |
| `portfolio_manager.py:129,181` | Correlation fetch and cache write | LEGACY | UNRESOLVED | Foundation maintainers. A failed refresh cannot claim a durable matrix update. |
| `reconciliation.py:42,82` | Walk-forward and live metrics | ARCHIVE | UNRESOLVED | Research pipeline owner. Missing or corrupt evidence must remain unavailable. |
| `reflection_engine.py:365,463,507` | Reflection and price provider boundaries | LEGACY | REMEDIATED | Foundation maintainers. Retain typed unavailable; no zero price or valid reflection. |
| `rl_agent.py:117,137,153` | Q-table, decisions, equity | ARCHIVE | UNRESOLVED | Model owner. Corrupt learned state must differ from clean initial state. |
| `run_arena_round.py:249` | Round runner | ARCHIVE | UNRESOLVED | Research pipeline owner. Persistence failure must fail the run. |
| `strategy_optimizer.py:369` | Optimizer CLI | ARCHIVE | UNRESOLVED | Model owner. Failed optimization cannot publish a valid result. |
| `train_ensemble.py:168` | Training CLI | ARCHIVE | UNRESOLVED | Model owner. Failed training cannot publish a usable ensemble. |

## Reachability verdict

The independent audit found no broad handler in the canonical paper artifact that manufactures `PASS`, `FRESH`, a completed stop sweep, a risk approval, successful execution, or nonzero sizing after failure. Its only handlers in these ownership classes are the four fail-closed `job_attribution.py` boundaries.

The remediated legacy paper and research paths also showed no remaining broad false-success catch. Current regressions cover risk personas, strategy-risk state, Portfolio Manager validation and swarm failures, corrupt portfolio context, corrupt correlation cache, reject-zero compatibility fallback, paper audit persistence ordering, direct batch propagation, and safety-engine audit consumption.

## Archive boundary

`allocation_engine.py`, `portfolio_optimizer.py`, `execute_live.py`, and `trading_agent.py` are absent from the canonical paper projection. `execute_live.py` and `trading_agent.py` are explicitly forbidden artifact paths. Their residual behavior remains unresolved Release Authority v2 debt and blocks any future live activation.

Required live configuration remains:

```text
TRADING_MODE=paper
LIVE_EXECUTION_ENABLED=false
LIVE_TRADING_APPROVED=false
LIVE_TRADING_ENABLED=false
```

Deferral is not approval of live behavior.

## Decision rule

Package 05 can close only when tests, warning gates, contracts, dashboard checks, canonical CI, diff review, and independent review all pass on unchanged source bytes. Any reachable false-success residual keeps the package at `NO-GO`.
