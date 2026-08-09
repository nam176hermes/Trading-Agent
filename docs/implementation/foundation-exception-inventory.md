# Foundation exception inventory

## Scope and method

Package 05 covers tracked production Python that can affect paper safety, research correctness, durable state, or externally visible status.

The P9 machine-readable inventory uses every `git ls-files '*.py'` path and
parses each file with Python `ast`. A broad handler catches bare `except`,
`Exception`, `BaseException`, `builtins.Exception`, or
`builtins.BaseException`, including those names inside nested tuples. Tests and
tooling are classified explicitly rather than excluded.

Track B P9 current measurement, refreshed on 2026-08-02 after the bounded
`execute_live.py` remediation:

| Metric | Count |
|---|---:|
| Tracked Python files | 413 |
| Broad handlers | 418 |
| Files containing broad handlers | 117 |
| Parse errors | 0 |
| `Exception` handlers, including tuples | 346 |
| `BaseException` handlers, including tuples | 72 |
| Bare handlers | 0 |
| First control-flow marker: re-raise | 116 |
| First control-flow marker: return | 114 |
| First control-flow marker: pass | 36 |
| First control-flow marker: continue | 15 |
| Other or log-only handler | 137 |

The control-flow marker is an inventory aid, not a semantic verdict. Package 05 does not require zero broad handlers. It requires every safety or correctness handler to be remediated or assigned a boundary, owner, and closure condition.

## Classification taxonomy

Mechanical inventory is classified by boundary and semantic intent before a
handler is changed:

| Class | Review question |
|---|---|
| `PRODUCTION_CRITICAL` | Can the handler hide authority, invariant, transaction, execution, broker, sizing, or durable-state failure? |
| `INTENTIONAL_CONTAINMENT` | Does the outer boundary preserve non-success status, structured evidence, cancellation, and process semantics? |
| `RETRY_PROVIDER_ADAPTER` | Does the adapter retry only controlled failures and expose final provider unavailability? |
| `TRANSACTION_REPOSITORY` | Does failure roll back and re-raise or return a typed non-success result? |
| `EXECUTION_BROKER` | Can any failure be confused with accepted, filled, persisted, or completely monitored execution? |
| `CLI_BACKGROUND_WORKER` | Does the process or job exit nonzero or publish a typed failed result? |
| `TEST_ONLY` | Is the handler reachable only from test code? |
| `TOOLING_MIGRATION` | Is the handler isolated from production authority and durable runtime state? |

P9 selected one reviewable `EXECUTION_BROKER` boundary rather than mechanically
rewriting the repository-wide inventory.

## Package 05 ownership counts

The earlier Package 05 read-only Codex audit manually classified its then-current
source. These are historical semantic counts, not a claim about the expanded P9
inventory:

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
| `execute_live.py:188,200,212` | External dependency, order submission, and observability wrappers | ARCHIVE | REMEDIATED FOR P9 | Track B maintainers. Retain immediate conversion to typed `UNAVAILABLE` or `PARTIAL`; malformed or unreadable sizing input fails closed, mixed execution is partial, and optional enrichment failure is returned. Release Authority v2 remains separately required before activation. |
| `enforce_stops.py:332` | Stop batch exception boundary | LEGACY | REMEDIATED | Foundation maintainers. Retain `UNAVAILABLE/PAPER_BATCH_EXECUTION_FAILED`. |
| `portfolio_manager.py:571` | Swarm decision boundary | LEGACY | REMEDIATED | Foundation maintainers. Retain re-raise and typed caller rejection. |
| `portfolio_optimizer.py:41,457,477,495,515` | Covariance and CLI/report boundaries | ARCHIVE | JUSTIFIED | Release Authority v2. Covariance failure cannot manufacture an allocatable matrix before activation. |
| `risk_personas.py:440` | Persona provider and parser boundary | LEGACY | REMEDIATED | Foundation maintainers. Retain typed unavailable, rejection, and zero size. |
| `set_mode.py:79,96` | Mode status | ARCHIVE | JUSTIFIED | Release Authority v2. Invalid mode or kill-switch state must deny activation. |
| `trading_agent.py:273,359,371,426,441,456,484,509,664,709,916,942,1061` | Autonomous loop, gates, sizing, execution, latency | ARCHIVE | UNRESOLVED | Release Authority v2. Complete live and autonomous-path review before packaging or activation. |

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

`allocation_engine.py`, `portfolio_optimizer.py`, `execute_live.py`, and
`trading_agent.py` are absent from the canonical paper projection.
`execute_live.py` and `trading_agent.py` are explicitly forbidden artifact
paths. P9 closes the selected `execute_live.py` exception-taxonomy boundary,
but does not close the remaining autonomous-agent, exchange, broker, monitoring,
packaging, or activation review. Those remain Release Authority v2 debt and
block any future live activation.

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

## P9 machine-readable broad-handler inventory

Scope is every `git ls-files '*.py'` path, parsed with Python `ast` in repo-relative lexical path and line order. A broad handler is bare, `Exception`, `BaseException`, `builtins.Exception`, `builtins.BaseException`, or a nested tuple containing either. `TESTS` covers any path component named `tests`; `TOOLING_MIGRATION` covers `scripts/`; `INTENTIONAL_CONTAINMENT` is restricted to the three typed wrappers in `legacy/research-backend/execute_live.py`; all other tracked runtime paths remain conservatively `PRODUCTION_CRITICAL`. Each row is `path|line|exception_form|classification|disposition`. Disposition is the first lexical handler marker among `RAISE`, `RETURN`, `PASS`, and `CONTINUE` in the handler's executable scope, or `OTHER` when none exists; nested functions, classes, lambdas, and handlers are excluded. It is an inventory aid, not a safety verdict. `scripts/check_broad_handler_inventory.py` recomputes every field and rejects stale, missing, extra, or duplicate rows; `--write` updates only this marked block.

<!-- P9_BROAD_HANDLER_INVENTORY_START -->
```text
apps/control_api/control_api/repositories/status.py|41|Exception|PRODUCTION_CRITICAL|RETURN
apps/control_api/trading_control/phase3b_writer.py|424|Exception|PRODUCTION_CRITICAL|RAISE
apps/control_api/trading_control/real_import.py|938|Exception|PRODUCTION_CRITICAL|RAISE
apps/control_api/trading_control/writer.py|425|Exception|PRODUCTION_CRITICAL|RAISE
apps/job_api/app.py|173|Exception|PRODUCTION_CRITICAL|OTHER
apps/job_api/app.py|267|Exception|PRODUCTION_CRITICAL|RETURN
apps/job_api/app.py|274|Exception|PRODUCTION_CRITICAL|RETURN
apps/job_api/app.py|405|Exception|PRODUCTION_CRITICAL|RAISE
apps/job_api/app.py|418|Exception|PRODUCTION_CRITICAL|RAISE
apps/job_api/app.py|439|Exception|PRODUCTION_CRITICAL|RETURN
apps/job_api/app.py|452|Exception|PRODUCTION_CRITICAL|RAISE
apps/job_api/config.py|67|Exception|PRODUCTION_CRITICAL|RAISE
apps/job_api/config.py|116|Exception|PRODUCTION_CRITICAL|RAISE
engines/nautilus/launcher/import_probe.py|158|Exception|PRODUCTION_CRITICAL|RAISE
engines/nautilus/launcher/import_probe.py|272|Exception|PRODUCTION_CRITICAL|RAISE
engines/nautilus/launcher/nautilus_backtest.py|512|BaseException|PRODUCTION_CRITICAL|RAISE
legacy/research-backend/alert_manager.py|98|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/allocation_engine.py|161|Exception|PRODUCTION_CRITICAL|CONTINUE
legacy/research-backend/allocation_engine.py|187|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/allocation_engine.py|475|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/alpha_arena.py|80|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/alpha_backtest.py|80|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/alpha_backtest.py|409|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/alpha_backtest.py|420|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/alpha_backtest.py|751|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/analysts.py|314|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/analysts.py|351|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/analysts.py|376|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/assembly.py|85|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/assembly.py|99|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/assembly.py|804|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/assembly.py|836|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/assembly.py|867|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/assembly.py|877|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/assembly.py|914|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/assembly.py|1003|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/audit_lookahead.py|46|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/backtest_engine.py|178|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/backtest_engine.py|833|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/backtest_runner.py|592|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/broker.py|94|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/broker.py|150|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/broker.py|164|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/broker.py|178|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/broker.py|192|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/collector_utils.py|40|Exception|PRODUCTION_CRITICAL|CONTINUE
legacy/research-backend/collector_utils.py|76|Exception|PRODUCTION_CRITICAL|CONTINUE
legacy/research-backend/cra_tracker.py|49|Exception|PRODUCTION_CRITICAL|CONTINUE
legacy/research-backend/cra_tracker.py|53|Exception|PRODUCTION_CRITICAL|PASS
legacy/research-backend/daily_report.py|111|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/daily_report.py|177|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/data_collector.py|167|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/data_collector.py|228|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/data_collector.py|278|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/data_vendors.py|243|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/data_vendors.py|283|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/data_vendors.py|326|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/data_vendors.py|369|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/data_vendors.py|438|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/data_vendors.py|480|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/data_vendors.py|522|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/data_vendors.py|605|Exception|PRODUCTION_CRITICAL|CONTINUE
legacy/research-backend/db/repository.py|41|Exception|PRODUCTION_CRITICAL|RAISE
legacy/research-backend/debate.py|513|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/debate.py|582|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/derivatives_collector.py|140|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/derivatives_collector.py|195|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/derivatives_collector.py|218|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/dl_predictor.py|367|Exception|PRODUCTION_CRITICAL|RAISE
legacy/research-backend/dl_predictor.py|543|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/dl_predictor.py|831|Exception|PRODUCTION_CRITICAL|CONTINUE
legacy/research-backend/enforce_stops.py|332|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/enforce_stops.py|426|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/event_bus.py|82|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/event_bus.py|129|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/event_bus.py|146|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/event_bus.py|171|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/event_bus.py|202|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/event_bus.py|222|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/event_hub.py|158|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/event_hub.py|177|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/exchange/adapter.py|250|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/exchange/adapter.py|332|Exception|PRODUCTION_CRITICAL|RAISE
legacy/research-backend/exchange/adapter.py|343|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/exchange/ccxt_bridge.py|131|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/exchange/ccxt_bridge.py|150|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/exchange/ccxt_bridge.py|196|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/exchange/ccxt_bridge.py|215|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/exchange/ccxt_bridge.py|231|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/exchange/ccxt_bridge.py|241|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/exchange/ccxt_bridge.py|281|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/exchange/ccxt_bridge.py|304|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/exchange/executor.py|120|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/exchange/secrets.py|94|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/exchange/secrets.py|211|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/exchange/ws_feed_old.py|99|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/exchange/ws_feed_old.py|116|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/exchange_health.py|57|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/exchange_health.py|63|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/exchange_health.py|128|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/exchange_health.py|139|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/exchange_health.py|149|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/exchange_health.py|190|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/execute_live.py|188|Exception|INTENTIONAL_CONTAINMENT|RAISE
legacy/research-backend/execute_live.py|200|Exception|INTENTIONAL_CONTAINMENT|RAISE
legacy/research-backend/execute_live.py|212|Exception|INTENTIONAL_CONTAINMENT|RETURN
legacy/research-backend/fallback.py|37|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/fallback.py|73|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/garch_vol.py|77|Exception|PRODUCTION_CRITICAL|PASS
legacy/research-backend/job_attribution.py|163|BaseException|PRODUCTION_CRITICAL|RAISE
legacy/research-backend/job_attribution.py|169|BaseException|PRODUCTION_CRITICAL|RAISE
legacy/research-backend/job_attribution.py|192|BaseException|PRODUCTION_CRITICAL|RAISE
legacy/research-backend/job_attribution.py|462|BaseException|PRODUCTION_CRITICAL|RAISE
legacy/research-backend/kalshi_collector.py|60|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/kalshi_collector.py|87|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/kalshi_collector.py|92|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/kalshi_collector.py|98|Exception|PRODUCTION_CRITICAL|PASS
legacy/research-backend/kalshi_collector.py|125|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/live_data.py|42|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/live_data.py|90|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/live_data.py|158|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/live_data.py|264|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/local_artifacts.py|168|Exception|PRODUCTION_CRITICAL|RAISE
legacy/research-backend/local_artifacts.py|197|Exception|PRODUCTION_CRITICAL|RAISE
legacy/research-backend/macro.py|34|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/macro.py|67|Exception|PRODUCTION_CRITICAL|CONTINUE
legacy/research-backend/macro.py|259|Exception|PRODUCTION_CRITICAL|PASS
legacy/research-backend/macro_data.py|98|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/macro_data.py|165|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/macro_data.py|197|Exception|PRODUCTION_CRITICAL|CONTINUE
legacy/research-backend/macro_data.py|257|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/main.py|199|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/main.py|206|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/main.py|222|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/main.py|297|Exception|PRODUCTION_CRITICAL|PASS
legacy/research-backend/main.py|398|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/main.py|422|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/main.py|669|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/main.py|687|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/main.py|709|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/main.py|731|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/main.py|753|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/main.py|882|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/main.py|973|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/main.py|1052|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/main.py|1174|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/main.py|1240|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/main.py|1292|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/main.py|1647|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/main.py|1721|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/main.py|1740|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/main.py|1968|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/main.py|2180|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/main.py|2200|Exception|PRODUCTION_CRITICAL|CONTINUE
legacy/research-backend/main.py|2207|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/main.py|2309|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/main.py|2345|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/main.py|2441|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/main.py|2450|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/main.py|2456|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/main.py|2462|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/main.py|2467|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/main.py|2473|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/memory.py|421|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/ml_predictor.py|100|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/ml_predictor.py|115|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/ml_predictor.py|146|Exception|PRODUCTION_CRITICAL|PASS
legacy/research-backend/ml_predictor.py|254|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/ml_predictor.py|493|Exception|PRODUCTION_CRITICAL|PASS
legacy/research-backend/ml_predictor.py|594|Exception|PRODUCTION_CRITICAL|CONTINUE
legacy/research-backend/ml_regime.py|85|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/ml_regime.py|123|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/ml_regime.py|268|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/ml_toolkit.py|155|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/ml_toolkit.py|261|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/ml_toolkit.py|417|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/onchain_collector.py|60|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/onchain_collector.py|133|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/onchain_collector.py|199|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/onchain_collector.py|211|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/onchain_collector.py|241|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/orderflow_collector.py|228|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/orderflow_collector.py|235|Exception|PRODUCTION_CRITICAL|PASS
legacy/research-backend/orderflow_collector.py|284|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/pairs_trader.py|172|Exception|PRODUCTION_CRITICAL|CONTINUE
legacy/research-backend/pairs_trader.py|355|Exception|PRODUCTION_CRITICAL|PASS
legacy/research-backend/pairs_trader.py|362|Exception|PRODUCTION_CRITICAL|PASS
legacy/research-backend/polymarket_collector.py|38|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/polymarket_collector.py|94|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/polymarket_collector.py|115|Exception|PRODUCTION_CRITICAL|PASS
legacy/research-backend/portfolio_manager.py|129|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/portfolio_manager.py|181|Exception|PRODUCTION_CRITICAL|PASS
legacy/research-backend/portfolio_manager.py|571|Exception|PRODUCTION_CRITICAL|RAISE
legacy/research-backend/portfolio_optimizer.py|41|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/portfolio_optimizer.py|457|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/portfolio_optimizer.py|477|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/portfolio_optimizer.py|495|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/portfolio_optimizer.py|515|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/reconciliation.py|42|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/reconciliation.py|82|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/reflection_engine.py|371|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/reflection_engine.py|469|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/reflection_engine.py|513|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/regime_detector.py|97|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/regime_detector.py|182|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/regime_detector.py|235|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/regime_detector.py|431|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/regime_detector.py|496|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/risk_personas.py|440|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/rl_agent.py|117|Exception|PRODUCTION_CRITICAL|PASS
legacy/research-backend/rl_agent.py|137|Exception|PRODUCTION_CRITICAL|CONTINUE
legacy/research-backend/rl_agent.py|153|Exception|PRODUCTION_CRITICAL|CONTINUE
legacy/research-backend/run_arena_round.py|25|Exception|PRODUCTION_CRITICAL|PASS
legacy/research-backend/run_arena_round.py|60|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/run_arena_round.py|189|Exception|PRODUCTION_CRITICAL|CONTINUE
legacy/research-backend/run_arena_round.py|230|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/run_arena_round.py|241|Exception|PRODUCTION_CRITICAL|PASS
legacy/research-backend/run_arena_round.py|249|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/safety_engine.py|478|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/safety_engine.py|516|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/sentiment_collector.py|56|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/sentiment_collector.py|68|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/sentiment_collector.py|181|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/sentiment_filter.py|221|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/sentiment_filter.py|327|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/set_mode.py|79|Exception|PRODUCTION_CRITICAL|PASS
legacy/research-backend/set_mode.py|96|Exception|PRODUCTION_CRITICAL|PASS
legacy/research-backend/signal_parser.py|266|Exception|PRODUCTION_CRITICAL|RAISE
legacy/research-backend/strategy_optimizer.py|369|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/ta_engine.py|202|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/ta_engine.py|210|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/ta_validation.py|150|Exception|PRODUCTION_CRITICAL|CONTINUE
legacy/research-backend/tests/test_integration.py|86|Exception|TESTS|OTHER
legacy/research-backend/tests/test_integration.py|174|Exception|TESTS|OTHER
legacy/research-backend/trading_agent.py|273|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/trading_agent.py|359|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/trading_agent.py|371|Exception|PRODUCTION_CRITICAL|PASS
legacy/research-backend/trading_agent.py|426|Exception|PRODUCTION_CRITICAL|PASS
legacy/research-backend/trading_agent.py|441|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/trading_agent.py|456|Exception|PRODUCTION_CRITICAL|PASS
legacy/research-backend/trading_agent.py|484|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/trading_agent.py|509|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/trading_agent.py|664|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/trading_agent.py|709|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/trading_agent.py|916|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/trading_agent.py|942|Exception|PRODUCTION_CRITICAL|PASS
legacy/research-backend/trading_agent.py|1061|Exception|PRODUCTION_CRITICAL|PASS
legacy/research-backend/train_ensemble.py|168|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/walk_forward.py|91|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/weekly_report.py|248|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/ws_stream.py|78|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/ws_stream.py|98|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/ws_stream.py|161|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/ws_stream.py|198|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/ws_stream.py|253|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/ws_stream.py|267|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/ws_stream.py|324|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/ws_stream.py|337|Exception|PRODUCTION_CRITICAL|PASS
legacy/research-backend/ws_stream.py|346|Exception|PRODUCTION_CRITICAL|OTHER
legacy/research-backend/yfinance_collector.py|111|Exception|PRODUCTION_CRITICAL|PASS
legacy/research-backend/yfinance_collector.py|144|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/yfinance_collector.py|164|Exception|PRODUCTION_CRITICAL|RETURN
legacy/research-backend/yfinance_collector.py|196|Exception|PRODUCTION_CRITICAL|OTHER
ops/phase4b/verify-release.py|91|Exception|PRODUCTION_CRITICAL|OTHER
ops/phase4b/verify-release.py|138|Exception|PRODUCTION_CRITICAL|OTHER
ops/phase4b/verify-release.py|339|Exception|PRODUCTION_CRITICAL|RETURN
ops/release-v2/verify-stage.py|1271|Exception|PRODUCTION_CRITICAL|RETURN
packages/data_catalog/parquet.py|244|Exception|PRODUCTION_CRITICAL|RAISE
packages/data_catalog/parquet.py|395|Exception|PRODUCTION_CRITICAL|RAISE
packages/data_catalog/parquet.py|418|Exception|PRODUCTION_CRITICAL|RAISE
packages/data_catalog/parquet.py|431|Exception|PRODUCTION_CRITICAL|RAISE
packages/data_catalog/parquet.py|466|Exception|PRODUCTION_CRITICAL|RAISE
packages/data_catalog/parquet.py|480|Exception|PRODUCTION_CRITICAL|RAISE
packages/nautilus_backtest/result.py|227|Exception|PRODUCTION_CRITICAL|RAISE
packages/nautilus_backtest/runtime_process.py|47|BaseException|PRODUCTION_CRITICAL|OTHER
packages/nautilus_backtest/runtime_process.py|94|BaseException|PRODUCTION_CRITICAL|RAISE
packages/research_validation/producers.py|891|BaseException|PRODUCTION_CRITICAL|RAISE
packages/research_validation/producers.py|970|Exception|PRODUCTION_CRITICAL|RAISE
packages/research_validation/producers.py|1419|BaseException|PRODUCTION_CRITICAL|RAISE
packages/restore_proof_failure_codes.py|103|Exception|PRODUCTION_CRITICAL|RETURN
packages/runtime_release/config.py|236|Exception|PRODUCTION_CRITICAL|RAISE
packages/runtime_release/config.py|351|Exception|PRODUCTION_CRITICAL|RAISE
packages/runtime_release/config.py|366|Exception|PRODUCTION_CRITICAL|RAISE
packages/runtime_release/config.py|456|Exception|PRODUCTION_CRITICAL|RAISE
packages/runtime_release/config.py|488|Exception|PRODUCTION_CRITICAL|RAISE
packages/runtime_release/job_plane.py|31|Exception|PRODUCTION_CRITICAL|RAISE
packages/runtime_release/job_plane.py|41|Exception|PRODUCTION_CRITICAL|RAISE
packages/runtime_release/job_plane.py|54|Exception|PRODUCTION_CRITICAL|RAISE
packages/runtime_release/manifest.py|350|Exception|PRODUCTION_CRITICAL|RAISE
packages/runtime_release/manifest.py|681|Exception|PRODUCTION_CRITICAL|RAISE
packages/runtime_release/paper_application/command_registry.py|214|Exception|PRODUCTION_CRITICAL|RAISE
packages/runtime_release/paper_application/command_registry.py|227|Exception|PRODUCTION_CRITICAL|OTHER
packages/runtime_release/paper_application/command_registry.py|260|Exception|PRODUCTION_CRITICAL|RAISE
packages/runtime_release/paper_application/results.py|107|BaseException|PRODUCTION_CRITICAL|RAISE
packages/runtime_release/paper_application/results.py|116|BaseException|PRODUCTION_CRITICAL|RAISE
packages/runtime_release/paper_application/results.py|337|BaseException|PRODUCTION_CRITICAL|RAISE
packages/runtime_release/paper_application/runtime_release_config.py|175|Exception|PRODUCTION_CRITICAL|RAISE
packages/runtime_release/paper_application/runtime_release_config.py|289|Exception|PRODUCTION_CRITICAL|RAISE
packages/runtime_release/paper_application/runtime_release_config.py|304|Exception|PRODUCTION_CRITICAL|RAISE
packages/runtime_release/paper_application/runtime_release_job_plane.py|18|Exception|PRODUCTION_CRITICAL|RAISE
packages/runtime_release/paper_application/runtime_release_job_plane.py|28|Exception|PRODUCTION_CRITICAL|RAISE
packages/runtime_release/paper_backend/paper_main.py|110|Exception|PRODUCTION_CRITICAL|RETURN
packages/runtime_release/provisioning.py|44|Exception|PRODUCTION_CRITICAL|RAISE
packages/runtime_release/provisioning.py|87|Exception|PRODUCTION_CRITICAL|RAISE
packages/runtime_release/provisioning.py|128|Exception|PRODUCTION_CRITICAL|RAISE
packages/runtime_release/provisioning.py|141|Exception|PRODUCTION_CRITICAL|RAISE
packages/runtime_release/provisioning.py|207|Exception|PRODUCTION_CRITICAL|RAISE
packages/runtime_release/staging_v2.py|837|Exception|PRODUCTION_CRITICAL|RAISE
packages/runtime_release/staging_v2.py|868|Exception|PRODUCTION_CRITICAL|RETURN
packages/runtime_release/staging_v2.py|947|Exception|PRODUCTION_CRITICAL|RAISE
packages/runtime_release/staging_v2.py|1010|Exception|PRODUCTION_CRITICAL|RAISE
packages/runtime_release/v2.py|357|Exception|PRODUCTION_CRITICAL|RAISE
packages/runtime_release/v2.py|396|Exception|PRODUCTION_CRITICAL|RAISE
packages/runtime_release/v2.py|422|Exception|PRODUCTION_CRITICAL|RAISE
packages/runtime_release/v2.py|478|Exception|PRODUCTION_CRITICAL|RAISE
packages/runtime_release/v2.py|499|Exception|PRODUCTION_CRITICAL|RAISE
packages/runtime_release/v2.py|521|Exception|PRODUCTION_CRITICAL|RAISE
packages/runtime_release/v2.py|539|Exception|PRODUCTION_CRITICAL|RAISE
packages/runtime_release/v2.py|596|Exception|PRODUCTION_CRITICAL|RAISE
packages/runtime_release/v2.py|764|Exception|PRODUCTION_CRITICAL|RAISE
packages/runtime_release/v2.py|821|Exception|PRODUCTION_CRITICAL|RAISE
packages/runtime_release/v2.py|1000|Exception|PRODUCTION_CRITICAL|RAISE
packages/runtime_release/v2.py|1076|Exception|PRODUCTION_CRITICAL|RAISE
packages/runtime_release/v2.py|1109|Exception|PRODUCTION_CRITICAL|RAISE
packages/runtime_release/v2.py|1273|Exception|PRODUCTION_CRITICAL|RAISE
packages/runtime_release/v2.py|1315|Exception|PRODUCTION_CRITICAL|RAISE
packages/runtime_release/v2.py|1465|Exception|PRODUCTION_CRITICAL|RAISE
packages/runtime_release/v2.py|1726|Exception|PRODUCTION_CRITICAL|RAISE
packages/runtime_release/v2.py|1755|Exception|PRODUCTION_CRITICAL|RAISE
packages/runtime_release/v2.py|1911|Exception|PRODUCTION_CRITICAL|RAISE
packages/runtime_release/v2.py|1924|Exception|PRODUCTION_CRITICAL|RAISE
packages/runtime_release/v2.py|2238|Exception|PRODUCTION_CRITICAL|RAISE
packages/runtime_release/v2.py|2487|Exception|PRODUCTION_CRITICAL|RAISE
packages/runtime_release/v2.py|2593|Exception|PRODUCTION_CRITICAL|RAISE
packages/runtime_release/v2.py|2631|Exception|PRODUCTION_CRITICAL|RAISE
packages/runtime_release/v2.py|2667|Exception|PRODUCTION_CRITICAL|RAISE
packages/runtime_release/v2.py|2756|Exception|PRODUCTION_CRITICAL|RETURN
scripts/audit_canonical_repo.py|506|BaseException|TOOLING_MIGRATION|RETURN
scripts/build_phase4_semantic_manifest.py|282|Exception|TOOLING_MIGRATION|RAISE
scripts/build_phase4_semantic_manifest.py|520|Exception|TOOLING_MIGRATION|OTHER
scripts/build_phase4_semantic_manifest.py|796|Exception|TOOLING_MIGRATION|RAISE
scripts/diagnose_nautilus_v12_runtime_failure.py|628|BaseException|TOOLING_MIGRATION|RAISE
scripts/generate_phase4_command_manifest.py|63|Exception|TOOLING_MIGRATION|RETURN
scripts/generate_phase4_runtime_authority.py|109|Exception|TOOLING_MIGRATION|RETURN
scripts/import_component_snapshot.py|291|BaseException|TOOLING_MIGRATION|RAISE
scripts/import_component_snapshot.py|393|BaseException|TOOLING_MIGRATION|RAISE
scripts/import_component_snapshot.py|403|BaseException|TOOLING_MIGRATION|RAISE
scripts/import_component_snapshot.py|703|BaseException|TOOLING_MIGRATION|RAISE
scripts/import_component_snapshot.py|755|BaseException|TOOLING_MIGRATION|RETURN
scripts/prepare_nautilus_input_cache.py|171|BaseException|TOOLING_MIGRATION|RAISE
scripts/prepare_nautilus_input_cache.py|450|BaseException|TOOLING_MIGRATION|OTHER
scripts/prepare_nautilus_input_cache.py|454|BaseException|TOOLING_MIGRATION|OTHER
scripts/prepare_nautilus_input_cache.py|463|BaseException|TOOLING_MIGRATION|RAISE
scripts/prepare_nautilus_llvm_toolchain.py|458|BaseException|TOOLING_MIGRATION|RAISE
scripts/prepare_nautilus_llvm_toolchain.py|642|BaseException|TOOLING_MIGRATION|RAISE
scripts/prepare_runtime_release_wheelhouse.py|230|Exception|TOOLING_MIGRATION|RAISE
scripts/smoke_phase4_backend_release.py|105|Exception|TOOLING_MIGRATION|RAISE
scripts/validate_package6_runtime_approval.py|1071|Exception|TOOLING_MIGRATION|OTHER
scripts/verify_component_snapshot.py|337|BaseException|TOOLING_MIGRATION|RETURN
scripts/verify_job_plane_authority.py|47|Exception|TOOLING_MIGRATION|RAISE
scripts/verify_nautilus_v12_r3_parity.py|483|BaseException|TOOLING_MIGRATION|OTHER
scripts/verify_nautilus_v12_r3_parity.py|627|BaseException|TOOLING_MIGRATION|OTHER
scripts/verify_nautilus_v12_r3_parity.py|851|BaseException|TOOLING_MIGRATION|OTHER
scripts/verify_nautilus_v12_r3_parity.py|975|BaseException|TOOLING_MIGRATION|OTHER
scripts/verify_nautilus_v12_r3_parity.py|1060|BaseException|TOOLING_MIGRATION|OTHER
scripts/verify_nautilus_v12_r3_parity.py|1317|BaseException|TOOLING_MIGRATION|OTHER
scripts/verify_nautilus_v12_r3_parity.py|1460|BaseException|TOOLING_MIGRATION|OTHER
scripts/verify_nautilus_v12_r3_parity.py|1763|BaseException|TOOLING_MIGRATION|OTHER
scripts/verify_nautilus_v12_r3_parity.py|2016|BaseException|TOOLING_MIGRATION|OTHER
services/job_scheduler/main.py|48|Exception|PRODUCTION_CRITICAL|RETURN
services/job_scheduler/scheduler.py|92|Exception|PRODUCTION_CRITICAL|RETURN
services/job_store/config.py|55|Exception|PRODUCTION_CRITICAL|RAISE
services/job_store/config.py|113|Exception|PRODUCTION_CRITICAL|RAISE
services/job_store/config.py|199|Exception|PRODUCTION_CRITICAL|RAISE
services/job_store/worker_repository.py|139|Exception|PRODUCTION_CRITICAL|RAISE
services/job_worker/artifacts.py|66|BaseException|PRODUCTION_CRITICAL|RAISE
services/job_worker/artifacts.py|87|BaseException|PRODUCTION_CRITICAL|RAISE
services/job_worker/artifacts.py|118|BaseException|PRODUCTION_CRITICAL|RAISE
services/job_worker/artifacts.py|143|BaseException|PRODUCTION_CRITICAL|RAISE
services/job_worker/command_registry.py|238|Exception|PRODUCTION_CRITICAL|RAISE
services/job_worker/command_registry.py|253|Exception|PRODUCTION_CRITICAL|RAISE
services/job_worker/command_registry.py|271|Exception|PRODUCTION_CRITICAL|OTHER
services/job_worker/command_registry.py|284|Exception|PRODUCTION_CRITICAL|RAISE
services/job_worker/command_registry.py|308|Exception|PRODUCTION_CRITICAL|RAISE
services/job_worker/command_registry.py|361|Exception|PRODUCTION_CRITICAL|RAISE
services/job_worker/command_registry.py|374|Exception|PRODUCTION_CRITICAL|OTHER
services/job_worker/command_registry.py|407|Exception|PRODUCTION_CRITICAL|RAISE
services/job_worker/engine_spawn.py|294|BaseException|PRODUCTION_CRITICAL|PASS
services/job_worker/engine_spawn.py|953|BaseException|PRODUCTION_CRITICAL|RAISE
services/job_worker/engine_spawn.py|972|BaseException|PRODUCTION_CRITICAL|RAISE
services/job_worker/engine_spawn.py|1062|BaseException|PRODUCTION_CRITICAL|RAISE
services/job_worker/engine_spawn.py|1163|BaseException|PRODUCTION_CRITICAL|RAISE
services/job_worker/process_runner.py|83|BaseException|PRODUCTION_CRITICAL|RAISE
services/job_worker/process_runner.py|557|BaseException|PRODUCTION_CRITICAL|RAISE
services/job_worker/process_runner.py|600|BaseException|PRODUCTION_CRITICAL|RAISE
services/job_worker/process_runner.py|640|BaseException|PRODUCTION_CRITICAL|OTHER
services/job_worker/process_runner.py|651|BaseException|PRODUCTION_CRITICAL|OTHER
services/job_worker/process_runner.py|670|BaseException|PRODUCTION_CRITICAL|OTHER
services/job_worker/process_runner.py|682|BaseException|PRODUCTION_CRITICAL|RAISE
services/job_worker/process_runner.py|748|BaseException|PRODUCTION_CRITICAL|RAISE
services/job_worker/process_runner.py|795|BaseException|PRODUCTION_CRITICAL|OTHER
services/job_worker/process_runner.py|826|BaseException|PRODUCTION_CRITICAL|OTHER
services/job_worker/process_runner.py|845|BaseException|PRODUCTION_CRITICAL|OTHER
services/job_worker/process_runner.py|860|BaseException|PRODUCTION_CRITICAL|OTHER
services/job_worker/process_runner.py|870|BaseException|PRODUCTION_CRITICAL|OTHER
services/job_worker/process_runner.py|911|BaseException|PRODUCTION_CRITICAL|OTHER
services/job_worker/process_runner.py|929|BaseException|PRODUCTION_CRITICAL|RETURN
services/job_worker/process_runner.py|943|BaseException|PRODUCTION_CRITICAL|OTHER
services/job_worker/results.py|113|BaseException|PRODUCTION_CRITICAL|RAISE
services/job_worker/results.py|122|BaseException|PRODUCTION_CRITICAL|RAISE
services/job_worker/results.py|423|BaseException|PRODUCTION_CRITICAL|RAISE
services/job_worker/safety_state.py|151|Exception|PRODUCTION_CRITICAL|OTHER
services/job_worker/safety_state.py|351|Exception|PRODUCTION_CRITICAL|OTHER
services/job_worker/worker.py|267|Exception|PRODUCTION_CRITICAL|OTHER
services/job_worker/worker.py|447|Exception|PRODUCTION_CRITICAL|RAISE
services/job_worker/worker.py|457|Exception|PRODUCTION_CRITICAL|RAISE
services/job_worker/worker.py|488|Exception|PRODUCTION_CRITICAL|RAISE
services/market_data/ingestion.py|53|Exception|PRODUCTION_CRITICAL|RAISE
services/market_data/ingestion.py|58|Exception|PRODUCTION_CRITICAL|RAISE
services/market_data/ingestion.py|81|Exception|PRODUCTION_CRITICAL|RAISE
services/paper_runtime/controller.py|785|BaseException|PRODUCTION_CRITICAL|RAISE
services/paper_runtime/controller.py|854|BaseException|PRODUCTION_CRITICAL|OTHER
services/paper_runtime/controller.py|877|BaseException|PRODUCTION_CRITICAL|OTHER
services/paper_runtime/controller.py|901|BaseException|PRODUCTION_CRITICAL|OTHER
services/paper_runtime/controller.py|914|BaseException|PRODUCTION_CRITICAL|OTHER
services/paper_runtime/controller.py|939|BaseException|PRODUCTION_CRITICAL|OTHER
services/paper_runtime/controller.py|1421|BaseException|PRODUCTION_CRITICAL|RAISE
services/paper_runtime/evidence.py|406|BaseException|PRODUCTION_CRITICAL|RAISE
services/paper_runtime/evidence.py|622|BaseException|PRODUCTION_CRITICAL|RAISE
services/paper_runtime/evidence.py|634|BaseException|PRODUCTION_CRITICAL|PASS
services/paper_runtime/evidence.py|677|BaseException|PRODUCTION_CRITICAL|RAISE
services/paper_runtime/evidence.py|737|BaseException|PRODUCTION_CRITICAL|PASS
services/paper_runtime/evidence.py|775|BaseException|PRODUCTION_CRITICAL|RAISE
services/paper_runtime/evidence.py|1278|BaseException|PRODUCTION_CRITICAL|PASS
services/paper_runtime/evidence.py|1320|BaseException|PRODUCTION_CRITICAL|RAISE
services/paper_runtime/evidence.py|1369|BaseException|PRODUCTION_CRITICAL|RAISE
services/paper_runtime/evidence.py|1486|BaseException|PRODUCTION_CRITICAL|RAISE
services/paper_runtime/evidence.py|1520|BaseException|PRODUCTION_CRITICAL|RAISE
services/paper_runtime/evidence.py|1624|BaseException|PRODUCTION_CRITICAL|RAISE
services/paper_runtime/evidence.py|1665|BaseException|PRODUCTION_CRITICAL|RAISE
services/paper_runtime/evidence.py|2703|BaseException|PRODUCTION_CRITICAL|RAISE
services/paper_runtime/evidence.py|2793|BaseException|PRODUCTION_CRITICAL|RAISE
services/paper_runtime/integration.py|290|BaseException|PRODUCTION_CRITICAL|RETURN
services/paper_runtime/integration.py|304|BaseException|PRODUCTION_CRITICAL|OTHER
services/paper_runtime/integration.py|378|BaseException|PRODUCTION_CRITICAL|OTHER
services/paper_runtime/integration.py|408|BaseException|PRODUCTION_CRITICAL|OTHER
services/paper_runtime/integration.py|474|BaseException|PRODUCTION_CRITICAL|RETURN
services/paper_runtime/integration.py|498|BaseException|PRODUCTION_CRITICAL|OTHER
services/paper_runtime/integration.py|718|BaseException|PRODUCTION_CRITICAL|OTHER
services/paper_runtime/integration.py|726|BaseException|PRODUCTION_CRITICAL|OTHER
services/paper_runtime/integration.py|751|BaseException|PRODUCTION_CRITICAL|OTHER
services/semantic_input_refresher/main.py|61|Exception|PRODUCTION_CRITICAL|RAISE
services/semantic_input_refresher/main.py|116|Exception|PRODUCTION_CRITICAL|RAISE
services/semantic_input_refresher/main.py|164|Exception|PRODUCTION_CRITICAL|RAISE
services/semantic_input_refresher/main.py|212|Exception|PRODUCTION_CRITICAL|RETURN
tests/foundation/test_package6_controller_closure.py|632|BaseException|TESTS|OTHER
tests/jobs/_postgres.py|171|Exception|TESTS|RAISE
tests/jobs/test_job_transition_restore.py|1685|Exception|TESTS|OTHER
tests/jobs/test_repository_queries.py|340|BaseException|TESTS|OTHER
tests/market_data/test_repository.py|85|BaseException|TESTS|RAISE
tests/research_validation/test_producers.py|158|BaseException|TESTS|RAISE
```
<!-- P9_BROAD_HANDLER_INVENTORY_END -->
