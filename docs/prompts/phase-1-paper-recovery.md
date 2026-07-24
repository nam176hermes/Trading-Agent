# Codex prompt - Phase 1 paper dashboard recovery

Use only after the Phase 0 audit has been reviewed and accepted.

Read the audit, architecture, upgrade plan, acceptance gates, contract catalog,
and related bootstrap backlog rows. Create an isolated branch or worktree in the
approved canonical repo before editing.

## Constraints

- Keep paper mode and live execution disabled.
- Do not retrain or alter strategy/model behavior.
- Do not send an external order or call an order-creation endpoint.
- Do not delete, move, or rewrite legacy data.
- Make small, tested, reversible changes.

## Deliverables

1. Deployment identity API and visible mode/repo/commit/data-root status.
2. Runtime-validated semantic selection of the newest valid market report.
3. Typed `VALID`, `STALE`, and `NO_DATA` behavior with no malformed-data 500.
4. Correct decision totals, timestamps, confidence display, assets, signals,
   loading, empty, stale, and error states.
5. Evidence-based capability states: PASS, FAIL, STALE, UNKNOWN.
6. Canonical deny-by-default asset registry with routing tests for BTC, ETH,
   SOL, TON, DOGE, ADA, AVAX, DOT, LINK, MATIC, and unknown rejection.
7. Safe resolution of duplicate circuit-breaker code with regression tests.
8. Reproducible dependency manifests and applicable CI gates.
9. Implementation summary, test evidence, and rollback document.

Run build, typecheck, lint, targeted frontend tests, backend smoke tests, broker
routing tests, paper-trader tests, and API/browser smoke checks. Finish with Git
diff/status, commit list, fresh results, remaining risks, and proof live remains
disabled. Do not begin Phase 2.
