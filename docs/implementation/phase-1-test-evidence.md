# Phase 1 Test Evidence

All timestamps are 2026-07-11 America/Toronto; commands are paper-only and used no production credential.

| Time | Repo | Command | Result |
|---|---|---|---|
| 03:50 | candidate dashboard | `npm test` | PASS; integration fixture selected semantic latest report, skipped invalid JSON, returned STALE, normalized `STRONG_SELL`, total 1, confidence 0.5, capability UNKNOWN |
| 03:50 | candidate dashboard | `npx tsc --noEmit` | PASS |
| 03:50 | candidate dashboard | `npm run lint` | PASS; 0 errors |
| 03:51 | candidate dashboard | `npm run build` | PASS; Next.js 16.2.6, 5 pages and 9 API routes |
| 04:03 | candidate dashboard | `npx tsc --noEmit` after deployment-identity adjustment | PASS |
| 03:50 | legacy dashboard | `npm test` | PASS; 3/3 mutation/auth policy tests |
| 03:50 | legacy dashboard | `npx tsc --noEmit` | PASS |
| 03:50 | legacy dashboard | `npm run lint` | PASS; 0 errors |
| 03:53 | legacy dashboard | `npm run build` | PASS; non-fatal NFT tracing warning retained |
| 03:53 | legacy dashboard | `bash tests/mode-auth.integration.sh` | PASS; missing config 503, missing/wrong auth 401, authorized paper 200, live rejected 403, shared temporary kill-switch round trip |
| 03:53 | backend | `.venv/bin/python tests/test_integration.py` | PASS; 43/43 |
| 04:02 | backend | `.venv/bin/python -m pytest -q -s tests/test_broker.py tests/test_paper_trader.py tests/test_phase1_safety.py tests/test_live_execution_policy.py tests/test_asset_registry.py` | PASS; 85 passed, 2 skipped |
| 03:53 | backend | `.venv/bin/python scripts/verify_phase1_environment.py` | PASS; Python 3.11.15 and 16 direct import modules |
| 03:55 | systemd | `systemd-analyze --user verify ...` | PASS; no unit errors |

## Expected skips and harness note

Two Alpaca connectivity tests were skipped because real broker connectivity is outside Phase 1 and no order-capable credential was used. `tests/test_integration.py` calls `sys.exit(0)` at module scope, so running it under pytest produces a collection internal error after its 43 checks; direct invocation is the canonical command.

## Initial failing evidence

TDD began with 18 Python failures for missing second gate/policy/kill-state resolver and 2 Node failures for split mutation auth. These failures disappeared after the focused patches. One broker assertion expected the legacy reason string; compatibility was preserved while adding `reason_code`.
