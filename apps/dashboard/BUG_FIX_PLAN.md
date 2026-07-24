# Trading Agent — Deep Bug Audit & Fix Plan
Audit date: 2026-05-19. 80 bugs across pages, components, API routes, data layer.
Severity: 11 CRITICAL · 17 HIGH · 28 MEDIUM · 24 LOW.

Build status: passes. TypeScript clean. All bugs are runtime / logical, not type errors.

---

## Workstream A — Frontend (pages + components)
**Owner: DeepSeek v4 Pro** · Scope: `src/app/dashboard/`, `src/components/`

### CRITICAL (10) — null/string crashes on .toFixed
A1. `src/app/dashboard/execution/page.tsx:198` — `rec.slippage_pct.toFixed(3)` no guard
A2. `src/components/trading/benchmark-comparison.tsx:215,282` — `row.sharpe_ratio.toFixed(2)` no guard
A3. `src/components/trading/benchmark-comparison.tsx:227,234` — `row.total_return_pct.toFixed(1)` no guard
A4. `src/components/trading/performance-metrics.tsx:126` — `metrics.drawdownPct.toFixed(2)` no guard
A5. `src/app/dashboard/history/page.tsx:301` — `.toFixed(0)` on reduce that may be NaN
A6. `src/components/trading/sentiment-trend-tracker.tsx:81` — `currentScore.toFixed(3)` no guard
A7. `src/components/trading/benchmark-comparison.tsx:237` — `(row.win_rate * 100).toFixed(0)` win_rate may be string
A8. `src/app/dashboard/page.tsx:159` — `exec.realizedPnl.toFixed(2)` may be string from Python JSON
A9. `src/app/dashboard/page.tsx:160` — `exec.unrealizedPnl.toFixed(2)` same risk
A10. `src/components/trading/price-ticker.tsx:136` — `change.toFixed(2)` no `Number.isFinite` check

### HIGH (11) — stale closures, wrong active state, partial SSE wipes
A11. `src/app/dashboard/page.tsx:42-43` — `avgConf` reduce → NaN when confidence is string
A12. `src/components/trading/auth-guard.tsx:18-50` — stale closure; no AbortController; needs 5s safety timeout
A13. `src/components/trading/price-ticker.tsx:46-86` — reconnectTimer not cleared on unmount
A14. `src/components/trading/sentiment-trend-client.tsx:111-113` — `??` without `Number.isFinite`
A15. `src/components/trading/trading-sub-nav.tsx:23-24` — Hub tab `startsWith` matches all `/dashboard/*`
A16. `src/app/dashboard/page.tsx:119` — yields literal "NaN%" in UI
A17. `src/components/trading/sentiment-trend-tracker.tsx:68` — `(trendStrength * 100).toFixed(0)` on NaN
A18. `src/components/trading/sentiment-trend-tracker.tsx:120-132` — momentum `.toFixed(3)` on possibly-string
A19. `src/components/trading/price-ticker.tsx:57-62` — `setPrices(data)` wipes other tickers on partial SSE
A20. `src/components/trading/signal-card.tsx:34-37` — `prices[symbol]` may be HealthData object, not number
A21. `src/components/trading/news-feed.tsx:162` — `article.sentiment_score.toFixed(2)` may be string

### MEDIUM (13) — dead code, false-zero color logic
A22-A33. **Delete 12 unused components** (zero imports found):
adaptive-optimizer-status, agent-collective-feed, agent-roster-card, capability-score,
cost-tracker, data-source-health, dataops-pipeline-tracker, multi-source-sentiment,
phase1-metrics, portfolio-correlation-heatmap, system-modules-card, ta-validation-card
A34. `src/app/dashboard/execution/page.tsx:254-255` — `pos.unrealized_pnl||0` breaks breakeven coloring

### LOW (6) — nested buttons, SPA links
A35. `src/components/trading/trade-journal.tsx:94-101` — button-in-button via stopPropagation
A36. `src/components/trading/pnl-tracker-card.tsx:94-101` — same nested-button pattern
A37. `src/components/trading/portfolio-card.tsx:301-328` — nested Export CSV + collapsible toggle
A38. `src/app/dashboard/page.tsx:99` — PipelineStatus loading flash
A39. `src/components/trading/quick-actions.tsx:145-162` — `<a>` instead of `<Link>` for SPA nav
A40. `src/components/trading/system-status-banner.tsx:76-85` — race on `alive` flag in fetchMode

**Pattern rules for Workstream A:**
- Replace `x != null && x.toFixed(...)` with `Number.isFinite(x) ? x.toFixed(...) : '—'`
- All formatters must accept `number | string | null | undefined` → coerce via `Number(x)` then `Number.isFinite()`
- SSE state merges must be `setState(prev => ({...prev, ...delta}))`, never replace
- Use `pathname === href || pathname.startsWith(href + '/')` for nav active-state, never raw startsWith

---

## Workstream B — Backend (API routes + data layer)
**Owner: GLM-5.1** · Scope: `src/app/api/trading/*/route.ts`, `src/lib/trading/`

### CRITICAL (10) — injection, traversal, unhandled JSON.parse, stderr bleed
B1. `src/lib/trading/data.ts:293` — `.get('total_realized_pnl') or 0` masks None (verify direction)
B2. `src/lib/trading/data.ts:296` — `2>&1` contaminates stdout in `getExecutionSummary`
B3. `src/app/api/trading/execution/route.ts:48` — `2>&1` stderr bleed
B4. `src/app/api/trading/keys/route.ts:91,165` — `2>&1` stderr bleed in keys protocol
B5. `src/app/api/trading/reports/route.ts:24` — **path traversal** via `?name=../../../etc/passwd`
B6. `src/app/api/trading/close-position/route.ts:25` — `execSync` insufficient symbol sanitization (`-`, `.` shell-significant)
B7. `src/app/api/trading/kill-switch/route.ts:55` — `execSync` interpolates user `reason`, only escapes `"`
B8. `src/app/api/trading/execution/route.ts:52` — `JSON.parse` no try/catch
B9. `src/app/api/trading/close-position/route.ts:33` — `JSON.parse` no try/catch
B10. `src/app/api/trading/keys/route.ts:37` — `JSON.parse` no try/catch

### HIGH (10) — non-atomic writes, equity-curve inflation, hardcoded paths
B11. `src/app/api/trading/run/route.ts:28,37,40` — `writeFileSync` no atomic rename
B12. `src/app/api/trading/update-stop/route.ts:55` — `writeFileSync` no atomic rename
B13. `src/app/api/trading/watchlist/route.ts:59` — `writeFileSync` no atomic rename
B14. `src/app/api/trading/mode/route.ts:22` — `writeFileSync` no atomic rename
B15. `src/lib/trading/equity.ts:50` — `priceMap[sym] ?? o.fill_price` uses latest price for historical MTM
B16. `src/app/api/trading/equity-curve/route.ts:75` — same inflated MTM pattern
B17. `src/app/api/trading/history/route.ts:36` — `computeEquityCurve` called with latest, not historical prices
B18. `src/app/api/trading/execution/route.ts:9` — hardcoded `/home/thenam176/...`
B19. `src/app/api/trading/keys/route.ts:6` — hardcoded path
B20. `src/app/api/trading/close-position/route.ts:7` — `python3` not `.venv/bin/python` (inconsistent)

### MEDIUM (15) — missing force-dynamic, no auth on mutating routes, wrong storage paths
B21. **All 49 routes** — none declare `export const dynamic = 'force-dynamic'`; add to every route reading fs/exec
B22. `execution/route.ts` — no auth guard on portfolio/PnL exposure
B23. `close-position/route.ts` — no auth on POST that forces sell orders
B24. `kill-switch/route.ts` — no auth on POST
B25. `run/route.ts` — no auth on POST that spawns pipeline
B26. `keys/route.ts` — `shellEscape` misses backticks, `$()`, newlines
B27. `correlation/route.ts:93` — error returns `{}` instead of `{error: ...}`
B28. `sentiment/route.ts:187` — same `{}` error pattern
B29. `backtest/route.ts:120` — inconsistent `{status, message}` vs `{error}`
B30. `equity-curve/route.ts:52` — unbounded `accumulatedShares` object growth
B31. `summary/route.ts:50` — `parseFloat(b.confidence)` on `'low'|'medium'|'high'` strings → NaN sort
B32. `prices-stream/route.ts:6` — hardcoded path
B33. `optimizer/route.ts:3` — `memory/typed_decisions.jsonl` path mismatch (actual: `decisions/*.json`)
B34. `position-sizing/route.ts:46` — `DATA_DIR/decisions/decisions.jsonl` path mismatch (actual: `memory/decisions.jsonl`)
B35. `summary/route.ts:4` — hardcoded path

### LOW (5) — fragile path interpolation, masked errors
B36. `sentiment/route.ts:88` — string template instead of `path.join`
B37. `go-nogo/route.ts:3` — same
B38. `optimizer/route.ts:103` — same
B39. `data.ts:298` — `getExecutionSummary` swallows all errors → silent "paper mode" facade
B40. `summary/route.ts:4` — covered above

**Pattern rules for Workstream B:**
- All `execAsync`/`execSync` user input → strict allowlist regex `^[A-Z0-9]{2,12}$` (symbols), bounded length, no `--`
- All `JSON.parse(stdout)` → wrap in try/catch, log raw stdout on failure, return `{error}`
- Stderr suppression: Python side uses `logging.getLogger('...').setLevel(logging.ERROR)`. NEVER `2>&1` into a JSON-parsing context.
- All file writes → `writeFileSync(tmp, data); renameSync(tmp, final)` for atomicity
- All routes touching fs/exec → `export const dynamic = 'force-dynamic'` + `export const revalidate = 0`
- Replace hardcoded `/home/thenam176/...` with `path.join(os.homedir(), '.hermes', 'crypto-research')` (or env var `CRYPTO_RESEARCH_DIR`)
- Auth guard: reusable `requireMasterKey()` helper that 401s if `TRADING_MASTER_KEY` header/cookie missing on POST mutating routes

---

## Verification (after each workstream completes)
1. `cd ~/.hermes/trading-agent && npx tsc --noEmit` — must be clean
2. `npx next build 2>&1 | tail -10` — must succeed
3. `grep -rn "\.toFixed" src/app src/components | grep -v Number.isFinite | wc -l` should approach 0 (Workstream A)
4. `grep -rn "2>&1" src/app/api/trading | wc -l` should be 0 (Workstream B)
5. `grep -rn "writeFileSync" src/app/api | grep -v renameSync` should be 0 (Workstream B)
6. `grep -rn "/home/thenam176" src/ | wc -l` should be 0 (Workstream B)

## Greenlight Gate
Workstreams write files only. No npm publish, no git push, no service restart, no API calls with cost.
Build verification runs locally. Safe to dispatch.
