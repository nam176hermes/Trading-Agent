# Foundation fallback policy

## Purpose

Package 05 replaces silent fallback with explicit failure semantics. A fallback is allowed only when consumers can distinguish unavailable input from valid input and policy explicitly allows continuation.

## Canonical statuses

| Status | Meaning | May look successful? | Continuation policy |
|---|---|---|---|
| `COMPLETED` or `AVAILABLE` | Required work and required coverage completed | Yes | Yes |
| `PARTIAL` | Base output exists, but a named optional source, coverage item, persistence step, or observability step is incomplete | No | Only under an explicit owning policy |
| `UNAVAILABLE` | Required dependency, source, state, or result is unavailable | No | Never for a safety or correctness gate |
| `UNKNOWN` | Evidence is insufficient for pass or fail | No | Never for an approval gate |
| `SKIPPED` | Explicit policy prevented execution, such as an active kill switch | No | Only for the caller that owns the policy |

Operation-level values such as `filled` and `rejected` remain nested under a job or pipeline status that reflects complete coverage.

## Required error envelope

Every typed failure crossing a module boundary contains:

```text
status
reason_code
trace_id
```

Coverage-sensitive results also name bounded affected items, such as `missing_price_symbols`, `unavailable_symbols`, or `execution_failures`. Structured logs use the same trace ID as the returned or persisted result.

Logs contain bounded identifiers and exception type names. They exclude credentials, provider response bodies, order secrets, and raw runtime configuration.

## Safety and correctness rules

1. Catch the narrowest repository-controlled exception.
2. A broad catch at an uncontrolled provider boundary must immediately return a typed error or re-raise to a typed caller boundary.
3. Preserve original bytes on state read or parse failure.
4. Never manufacture a clean portfolio, clean kill-switch state, zero price, neutral risk score, benchmark, synthetic history, completed job, safe coverage result, or valid correlation matrix.
5. Never translate failure into `PASS`, `FRESH`, `filled`, `COMPLETED`, approval, or nonzero position size.
6. Mandatory execution dependencies fail closed.
7. A CLI returns nonzero for `PARTIAL` or `UNAVAILABLE`, unless explicit policy classifies the operation as a successful `SKIPPED` result.

## Execution dependency policy

| Dependency or failure | Required behavior |
|---|---|
| Portfolio Manager provider or validation failure | Typed `PORTFOLIO_DECISION_UNAVAILABLE`; action becomes `HOLD`; no original BUY or SELL signal continues |
| Portfolio Manager swarm failure | Re-raise to the typed pipeline boundary; do not preserve an actionable primary decision |
| Portfolio context or correlation state is corrupt | Propagate failure to the typed pipeline boundary; do not substitute empty portfolio or static success |
| Portfolio compatibility fallback is invoked | Reject, `HOLD`, zero position size, zero conviction |
| RiskDebate provider or parser failure | Typed `RISK_ASSESSMENT_UNAVAILABLE`; reject signal; zero size |
| RiskDebate failure in research-only mode | Aggregate report may continue only as `PARTIAL`; no legacy risk approval is generated |
| Strategy-risk state is corrupt | `StrategyRiskStateUnavailable`; strategy denied; original bytes preserved |
| Backtest gate is unavailable | Typed `BACKTEST_GATE_UNAVAILABLE`; action becomes `HOLD` |
| Paper executor fails | Typed `PAPER_EXECUTION_FAILED`; no successful pipeline state |
| Broker fails after paper evidence exists | Preserve paper evidence, mark broker result `UNAVAILABLE`, and mark aggregate execution path `PARTIAL`. The secondary broker route exists only after a schema-valid, fully audited paper fill. Dryrun or live primary confirmation never invokes it. |
| Broker result schema or status is invalid | Normalize to `BROKER_RESULT_INVALID`; never infer success. A lifecycle status alone is insufficient: successful order states require a bounded order ID, symbol, side, and quantity; partial or complete fills also require bounded fill quantity and price; non-fill terminal states require a bounded reason or fully identified order. |
| Existing sizing-returns input cannot be read or parsed | `EXECUTION_DEPENDENCY_UNAVAILABLE`; submit no order; do not substitute valid-looking confidence sizing |
| Symbol or equity return values, or CVaR sizing output, are malformed | `EXECUTION_DEPENDENCY_RESULT_INVALID` with the originating operation and trace ID; submit no order and do not coerce NaN, infinity, booleans, containers, or out-of-domain values |
| Dryrun/live report, single-signal input, or controller execution input is malformed | Reject with a typed traceable result before coercion, sizing, preflight, or submission. Price, confidence, gate modifier, optional stop/target, and rationale are validated as raw values; booleans are never converted to numeric execution inputs. A zero position modifier is an explicit non-trading denial. |
| Broker order ID is malformed | `ORDER_RESULT_INVALID`; do not infer a fill, persist an order, or retry/resubmit; only bounded safe scalar IDs are public or durable |
| Live positions or tiered exit durable state is malformed or contains an out-of-domain quantity | `UNAVAILABLE/EXECUTION_STATE_UNAVAILABLE`; preserve bytes and do not size, submit, save, or mutate state |
| Tiered exit strategy result is malformed, or its sell delta is below `MIN_ORDER_USD` or above the bounded notional ceiling | `EXECUTION_DEPENDENCY_RESULT_INVALID` for the originating validator; do not submit a sell and do not mutate tier state |
| Optional GARCH or allocation enrichment fails validation after valid base sizing, including non-zero subnormal values | Preserve unchanged valid base sizing, retain bounded failure evidence, and mark aggregate execution `PARTIAL`; raw exception text is excluded. Explicit zero remains valid for both enrichment contracts. |
| One batch item fails before a later item executes | Preserve both the first typed failure and later execution evidence; aggregate status is `PARTIAL`, never `COMPLETED` or `UNAVAILABLE` |
| Execution mode lookup raises or returns an invalid mode | Typed `EXECUTION_DEPENDENCY_UNAVAILABLE` or `EXECUTION_DEPENDENCY_RESULT_INVALID`; no raw exception, sizing, preflight, or order submission |
| Ticker price, balance, sizing, preflight, durable-state, or fill evidence is NaN, infinite, subnormal, boolean, unbounded, or creates an out-of-domain quantity or notional | `EXECUTION_DEPENDENCY_RESULT_INVALID` or the boundary-specific typed invalid result; perform no arithmetic that can authorize an order and submit no order. Zero remains valid only at contracts that explicitly permit zero. |
| Broker fill exists but persistence or observability is incomplete | Authoritative result is `PARTIAL`; retain a complete validated fill envelope either in canonical nested `execution_evidence` or in the producer's complete outer observability envelope; sparse evidence is invalid and downstream callers must not invoke another broker |
| Primary confirmation claims `filled` but lacks bounded symbol, side, shares, fill price, audit state, or required order evidence | `EXECUTION_RESULT_INVALID`; expose an effective `UNAVAILABLE` status and do not invoke the secondary broker |

## Optional enrichment policy

An enrichment is optional only when the base pipeline has an independent valid result and no safety gate depends on the missing source.

Allowed behavior:

- preserve base research output;
- mark the source `PARTIAL` or `UNAVAILABLE`;
- include source, reason code, trace ID, and bounded exception type;
- mark the aggregate report `PARTIAL`;
- retain available sources without fabricating unavailable ones.

Disallowed behavior:

- representing missing input as `{}`, `[]`, `0.0`, `neutral`, or `0.5` without availability metadata;
- upgrading missing input to a valid observation;
- hiding failure in debug-only logs;
- treating a failed mandatory gate as optional;
- executing after a required gate fails.

## Persistence policy

| Failure point | Required result |
|---|---|
| Durable state cannot be read or parsed | `UNAVAILABLE`; preserve bytes; do not initialize replacement state |
| Pre-execution safety state cannot be written | `UNAVAILABLE`; do not execute |
| Authoritative execution completed but secondary audit log cannot be written | At least `PARTIAL`; preserve execution evidence and expose the logging failure |
| Safety engine consumes a durable fill with partial, missing, or invalid audit status | Preserve the fill, record audit reason and trace, return `PAPER_STOP_AUDIT_INCOMPLETE`, and exit nonzero |
| Portfolio write fails | Execution result is non-success; do not fabricate a post-write portfolio |
| Reflection write fails | Do not mark the decision reflected |
| Strategy kill-switch state is corrupt | Deny strategy; preserve state bytes |
| Typed decision or report persistence fails | Caller must propagate failure to aggregate status before the path becomes canonical |

Atomic temporary-file replacement is used where the module owns mutable JSON state and the existing contract permits replacement.

## Native restart custody policy

A durable cgroup removal intent does not prove post-restart physical custody.
Device and inode values can be reused after the original object is removed.
If the canonical or quarantine cgroup path is still present during restart
reconciliation, the result is `RECOVERY_REQUIRED` and no removal adapter is
called. Automatic finalization is allowed only when both paths are absent.

## Boundary examples

| Boundary | Result |
|---|---|
| Position price missing during stop or safety sweep | `PARTIAL` when some positions were checked, otherwise `UNAVAILABLE` |
| Risk persona provider or parser failure | `UNAVAILABLE`, reject signal, zero position size |
| Corrupt incubation state | `UNAVAILABLE`, gate closed, original bytes retained |
| Corrupt strategy-risk state | Explicit denial, original bytes retained |
| Alpha source fails while another source works | `PARTIAL`, available source retained, failed source named |
| Prediction or social report missing | Source `UNAVAILABLE`, aggregate report `PARTIAL` |
| Benchmark disabled or unavailable | Benchmark `UNAVAILABLE`, alpha `None`, reflection `PARTIAL` |
| Telegram alert fails after authoritative result | Authoritative status unchanged; bounded observability warning |

## Warning policy

Warnings are future breakage signals.

- Fix the compatibility cause.
- Do not add broad warning filters.
- Any unavoidable allowlist must be exact, owned, time-bounded, and identify the warning precisely.
- Recurrence of any targeted warning family fails governance.
- Other warning families remain subject to review, but this Package 05 test does not claim a comprehensive warning allowlist.
- Dependency changes remain narrow.

## Archive and live policy

Package 05 and Track B P9 do not authorize live strategy semantics.
`allocation_engine.py`, `portfolio_optimizer.py`, `execute_live.py`, and
`trading_agent.py` are excluded from the canonical paper artifact. P9 makes the
selected `execute_live.py` dependency, submission, persistence, sizing, and
observability failures explicit. Remaining autonomous-agent, exchange, broker,
monitoring, packaging, and activation review remains Release Authority v2 debt.

Any future live activation requires a separate reviewed plan, typed live outcomes, complete monitoring coverage, and explicit approval. Archive classification does not approve the residual behavior.

## Review rule

A reviewer must be able to answer from the result alone:

1. Did required work complete?
2. Which source or state failed?
3. Can the related log be found through the same trace ID?
4. Did any action execute before failure?
5. Does policy explicitly permit continuation?

Ambiguity means the fallback is not accepted.
