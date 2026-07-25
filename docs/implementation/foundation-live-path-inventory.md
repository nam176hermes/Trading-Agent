# Foundation live-path inventory

## Classification rules

| Classification | Meaning |
|---|---|
| `PACKAGED_AND_REACHABLE` | Present in the paper backend and reachable from its fixed job command |
| `PACKAGED_BUT_UNREACHABLE` | Present elsewhere in the release but not resolvable by the paper child command |
| `EXCLUDED_FROM_PAPER_RELEASE` | Git-proven source with no staged artifact path |
| `TEST_ONLY` | Test evidence, never release runtime |
| `ARCHIVE_ONLY` | Preserved legacy implementation, excluded from the paper artifact |

## Canonical reachable surface

| Component | Classification | Evidence |
|---|---|---|
| `packages/runtime_release/paper_backend/paper_main.py` as `paper_main.py` | `PACKAGED_AND_REACHABLE` | Sole command entrypoint, no mode arguments; source remains outside imported snapshot |
| `job_attribution.py` | `PACKAGED_AND_REACHABLE` | Strict worker identity and create-only report write |
| `packages/runtime_release/paper_backend/research_semantics.py` as `research_semantics.py` | `PACKAGED_AND_REACHABLE` | Source-closed canonical reader for the protected semantic snapshot; no legacy `live_data` edge |
| `packages/runtime_release/paper_backend/paper_runtime_manifest.json` as `paper_runtime_manifest.json` | `PACKAGED_AND_REACHABLE` | Exact artifact class, command and deny policy; source remains outside imported snapshot |

## Required live-capable inventory

| Capability | Source components | Classification | Reachability result |
|---|---|---|---|
| Live policy | `live_execution_policy.py` | `ARCHIVE_ONLY` | No staged path and no paper import |
| Live orchestrator | `execute_live.py`, `trading_agent.py` | `ARCHIVE_ONLY` | No staged path and no command entry |
| Direct CCXT execution | `exchange/adapter.py`, `exchange/executor.py` | `ARCHIVE_ONLY` | `create_order` cannot enter artifact |
| CCXT bridge | `exchange/ccxt_bridge.py` | `ARCHIVE_ONLY` | Adapter registry, credentials and `place_order` excluded |
| Other direct CCXT use | `backtest_engine.py`, `exchange_health.py` | `ARCHIVE_ONLY` | Not part of the four-file projection |
| Alpaca and broker paths | `broker.py`, Alpaca routing in `asset_registry.py` | `ARCHIVE_ONLY` | Broker API URLs, credentials and order functions excluded |
| Exchange adapter registry | `exchange/__init__.py`, `exchange/adapter.py` | `ARCHIVE_ONLY` | Package initializer and registry absent |
| Order executor | `exchange/executor.py` | `ARCHIVE_ONLY` | Executor and submission calls absent |
| Credential loaders | `exchange/secrets.py`, `broker._load_env`, `runtime_paths.configured_env_file` | `ARCHIVE_ONLY` | No keystore, dotenv or shared env loader in artifact |
| Mode mutation | `broker.set_mode`, `exchange.ccxt_bridge.set_mode`, `trading_agent._check_mode_switch`, `runtime_paths.mode_file` | `ARCHIVE_ONLY` | No mode file API or transition command packaged |
| Live CLI | multi-mode `main.py`, `execute_live.py`, `trading_agent.py`, `exchange/secrets.py` CLI | `ARCHIVE_ONLY` | Command catalog contains only fixed `paper_main.py` |
| Kill switch dependency | `kill_switch.py` | `ARCHIVE_ONLY` in backend | Retained for legacy audit; no paper command can import it |
| Live risk preflight | `live_execution_policy.py`, `risk_engine.py`, `strategy_risk_manager.py` | `ARCHIVE_ONLY` | Existing gates remain in source but cannot create artifact authority |
| Legacy safety tests | `legacy/research-backend/tests/test_live_execution_policy.py`, `test_broker.py`, `test_phase4_research_only.py` | `TEST_ONLY` | Mocked or denial tests only |
| Control-plane release and child-env gates | `packages/runtime_release/v2.py`, `services/job_worker/environment.py` | `PACKAGED_BUT_UNREACHABLE` from child | Parent authority verifies release and constructs child environment before spawn |

## Import and command trace

```text
JobType.SNAPSHOT
  -> COMMAND_REGISTRY
  -> python3.11 -I -B paper_main.py
  -> job_attribution
  -> research_semantics
  -> Python standard library public HTTPS client
```

There is no edge from this graph to `main.py`, `broker.py`, `execute_live.py`, `exchange`, CCXT, Alpaca, mode files or secret stores.

## Archive policy

Legacy live code is not deleted. The Git commit and tree still bind every archived blob. Source-proof entries under `legacy/research-backend` receive a staged path only when their relative path is in the exact paper allowlist. Every other legacy entry receives `stage_path: null` and therefore `EXCLUDED_FROM_PAPER_RELEASE` at construction time.
