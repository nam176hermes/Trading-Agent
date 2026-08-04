# Dashboard route inventory

**Snapshot:** 2026-08-03. This static source inventory covers every
`apps/dashboard/src/app/api/trading/**/route.ts` handler. It neither probes a
runtime nor authorizes an endpoint. A frontend caller is a source-level match
outside a handler; `none found` is not caller-removal proof.

All routes pass through the trading proxy. `GET` defaults to `reader`; handlers
that use `checkAuth` enforce that session requirement. Mutation roles are from
the handler's `authorizeMutation` call. `typed unavailable` means the
fail-closed `503` `SOURCE_UNAVAILABLE` envelope, unless a row says otherwise.
No handler is classified `dead`: removal requires caller proof and replacement
tests.

| Route | Method / role | Upstream and response contract | Frontend callers | Class |
|---|---|---|---|---|
| `/agents` | GET / reader | Static module catalog; JSON array | `settings-state` | compatibility |
| `/alerts` | GET / reader | None; `SOURCE_UNAVAILABLE` | `alerts-panel` | typed unavailable |
| `/backtest-results` | GET / reader | None; `SOURCE_UNAVAILABLE` | `backtest-results-card` | typed unavailable |
| `/backtest` | GET / reader | None; `SOURCE_UNAVAILABLE` | `backtest-results-card` | typed unavailable |
| `/bootstrap` | GET / reader | None; `SOURCE_UNAVAILABLE` | `help-modal` | typed unavailable |
| `/capability` | GET / reader | Control API capability envelope or typed unavailable | none found | canonical |
| `/circuit-breaker` | GET / reader | None; `SOURCE_UNAVAILABLE` | `halt-banner`, `circuit-breaker-status` | typed unavailable |
| `/close-position` | POST / operator | No command upstream; `COMMAND_UNAVAILABLE` 503 | none found | typed unavailable |
| `/correlation` | GET / reader | In-process correlation JSON | `portfolio-card`, `correlation-matrix` | compatibility |
| `/costs` | GET / reader | Control API cost envelope or typed unavailable | `settings-state` | canonical |
| `/data-sources` | GET / reader | None; `SOURCE_UNAVAILABLE` | `data-source-state` | typed unavailable |
| `/decisions` | GET / reader | Control API decision page or typed unavailable | `history/page`, `risk-asset-list` | canonical |
| `/decisions/typed` | GET / reader | None; `SOURCE_UNAVAILABLE` | `history/page`, `risk-asset-list` | typed unavailable |
| `/earnings` | GET / reader | None; `SOURCE_UNAVAILABLE` | `earnings-timeline` | typed unavailable |
| `/equity-curve` | GET / reader | None; `SOURCE_UNAVAILABLE` | performance/portfolio pages and components | typed unavailable |
| `/equity` | GET / reader | None; `SOURCE_UNAVAILABLE` | performance/portfolio pages and components | typed unavailable |
| `/exchange-status` | GET / reader | None; `SOURCE_UNAVAILABLE` | `operator-state` | typed unavailable |
| `/execution` | GET / reader | No Control API contract; authenticated typed 503 | `operator-state` | typed unavailable |
| `/export` | GET / reader | None; `SOURCE_UNAVAILABLE` | `trade-journal`, `portfolio-card` | typed unavailable |
| `/fundamentals` | GET / reader | None; `SOURCE_UNAVAILABLE` | `fundamentals-card`, `valuation-grid` | typed unavailable |
| `/go-nogo` | GET / reader | None; `SOURCE_UNAVAILABLE` | `go-nogo-banner` | typed unavailable |
| `/history` | GET / reader | None; `SOURCE_UNAVAILABLE` | none found | typed unavailable |
| `/jobs` | GET / reader; POST / operator | Job API list/create v1 envelope; disabled-command 503 or validated 400 | `pipeline-status`, `run-pipeline-button` | canonical |
| `/jobs/:id` | GET / reader | Job API detail v1 envelope | `pipeline-status`, `run-pipeline-button` | canonical |
| `/jobs/:id/cancel` | POST / operator | Job API cancel v1 envelope or disabled-command 503 | none found (dynamic caller) | canonical |
| `/keys` | GET, POST / admin | No credential-management upstream; authenticated typed 503 | `settings/page` | typed unavailable |
| `/kill-switch` | GET / reader; POST / admin | Control status plus protected local switch; structured state/error JSON | `quick-actions`, `halt-banner` | compatibility |
| `/live-positions` | GET / reader | None; `SOURCE_UNAVAILABLE` | `live-positions-card` | typed unavailable |
| `/macro` | GET / reader | None; `SOURCE_UNAVAILABLE` | `macro-dashboard` | typed unavailable |
| `/market` | GET / reader | Control API market JSON or typed unavailable | `market-ticker` | canonical |
| `/memory` | GET / reader | None; `SOURCE_UNAVAILABLE` | `memory-context` | typed unavailable |
| `/meta` | GET / reader | Control API deployment metadata JSON | `operator-state` | canonical |
| `/mode` | GET / reader; POST / admin | Control status plus protected local mode; structured mode/error JSON | `operator-state` (GET); no static POST caller | compatibility |
| `/modules` | GET / reader | Static module catalog; JSON array | none found | compatibility |
| `/news` | GET / reader | None; `SOURCE_UNAVAILABLE` | `news-feed` | typed unavailable |
| `/optimizer` | GET / reader | None; `SOURCE_UNAVAILABLE` | `benchmark-comparison` | typed unavailable |
| `/orders` | GET / reader | None; `SOURCE_UNAVAILABLE` | `history/page`, `trade-journal`, `performance-metrics` | typed unavailable |
| `/performance-export` | GET / reader | No export upstream; authenticated typed 503 | `performance/page` | typed unavailable |
| `/performance` | GET / reader | No performance upstream; authenticated typed 503 | `performance/page` | typed unavailable |
| `/pipeline-status` | GET / reader | Job API list v1 envelope | `pipeline-status` | canonical |
| `/plan` | POST / operator | Deterministic local plan; validated JSON or typed input error | `plan-builder` | compatibility |
| `/pnl` | GET / reader | None; `SOURCE_UNAVAILABLE` | `pnl-tracker-card` | typed unavailable |
| `/portfolio` | GET / reader | None; `SOURCE_UNAVAILABLE` | `exposure-gauge`, `portfolio-card` | typed unavailable |
| `/position-sizing` | GET / reader | None; `SOURCE_UNAVAILABLE` | `position-sizing-calculator` | typed unavailable |
| `/prediction` | GET / reader | None; `SOURCE_UNAVAILABLE` | `prediction-market-card` | typed unavailable |
| `/prices-stream` | GET / reader | None; `SOURCE_UNAVAILABLE` | `price-stream` | typed unavailable |
| `/reconciliation` | GET / reader | None; `SOURCE_UNAVAILABLE` | `performance/page` | typed unavailable |
| `/replay` | GET / reader | None; `SOURCE_UNAVAILABLE` | none found | typed unavailable |
| `/reports` | GET / reader | None; `SOURCE_UNAVAILABLE` | none found | typed unavailable |
| `/risk-assets` | GET / reader | Control API risk report JSON or typed unavailable | `risk-asset-list` | canonical |
| `/run` | GET / reader; POST / operator | Compatibility Job API list/create alias; v1 envelope or disabled-command 503 | `run-pipeline-button`, `quick-actions-state` | compatibility |
| `/sentiment` | GET / reader | None; `SOURCE_UNAVAILABLE` | `sentiment-trend-client` | typed unavailable |
| `/service` | GET, POST / admin | No service-control upstream; authenticated typed 503 | `settings/page`, `operator-state` | typed unavailable |
| `/signal-quality` | GET / reader | None; `SOURCE_UNAVAILABLE` | `signal-quality-banner` | typed unavailable |
| `/signals` | GET / reader | Control API report/decision JSON or typed unavailable | none found | canonical |
| `/social` | GET / reader | None; `SOURCE_UNAVAILABLE` | `social-sentiment-card` | typed unavailable |
| `/status` | GET / reader | Control API system/report JSON or typed unavailable | none found | canonical |
| `/summary` | GET / reader | Control API market/decision JSON or typed unavailable | none found | canonical |
| `/ta-validation` | GET / reader | None; `SOURCE_UNAVAILABLE` | none found | typed unavailable |
| `/update-stop` | POST / operator | Protected local trailing-stop state; validated JSON/error | `portfolio-card` | compatibility |
| `/walk-forward` | GET / reader | None; `SOURCE_UNAVAILABLE` | none found | typed unavailable |
| `/watchlist` | GET / reader; POST / operator | GET is unavailable; POST uses protected local override JSON | `watchlist-editor` | compatibility |

## Ownership rules

- `canonical` routes proxy validated Control API or Job API contracts; an
  unavailable response remains typed and fail-closed.
- `compatibility` routes are explicit local-state, static, or alias behaviour.
  They need caller proof and replacement tests before removal or merger.
- `typed unavailable` routes are retained UX boundaries. Clients must handle
  their error envelope, not reinterpret absence as empty or healthy data.
- `dead` is reserved for a later reviewed caller-proof decision.
