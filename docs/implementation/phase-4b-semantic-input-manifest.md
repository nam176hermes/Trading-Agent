# Phase 4B semantic-input evidence

No semantic input was published. At `2026-07-12T18:57:05Z`, the six explicit
structured sources were all older than the code-owned two-hour maximum:

| Logical input | Latest source timestamp | SHA-256 |
|---|---|---|
| macro report | 2026-06-25 04:52:30 UTC | `808bf7a087ada8bf1308368a3195fc36e5ec4ddb8540da0761941230a8ce95d4` |
| sentiment report | 2026-06-25 04:52:50 UTC | `04d3d12f1d83d94dd6f458c7d65a66bc18b5a568d0ef4a1dc80c37a7afaa1262` |
| on-chain report | 2026-06-25 04:52:52 UTC | `cdf2e024887e16a4c0ba421074649e5e540a8bbcaa40be0df1193e27245a3778` |
| FRED cache | 2026-06-25 04:42:35 UTC | `062e770b76921a12db7b5d8cbc4935c10090bfca9f6f334047d0b5576dcd3918` |
| cross-asset cache | 2026-06-25 04:42:36 UTC | `8cd5d25470c1c6e4813e956ef0216f2f7129cbc7f3da23d67adb0f93ab22cae8` |
| crypto-global cache | 2026-06-25 04:42:36 UTC | `992a6a5e25c4205320db218e557a4808726fbad2f7cda7222d37d37c2b4bd842` |

The read-only refresher dry-run exited `2`, the expected fail-closed result.
The freshness policy was not relaxed, file timestamps were not rewritten, and
no artificial authority or input copy was created. The root semantic refresh
service/timer are provisioning inputs only; the timer must remain disabled
until fresh approved structured sources exist.

The eventual publisher sees only legacy `reports` and `memory/macro`, selects
the six named inputs, and writes only the dedicated protected input/authority
roots. Dynamic `.mode`, kill-switch evidence, credentials, SQLite/WAL, logs and
the rest of the legacy tree are excluded.
