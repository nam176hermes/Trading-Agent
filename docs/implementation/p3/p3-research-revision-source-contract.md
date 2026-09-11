# P3 research revision source contract

Status: raw normalization source contract; revision/batch authority remains HELD.
This does not grant runtime authority or qualify C01. The accepted V2.1 economic
policy, legacy C01–C16 identities, and exposure classification remain unchanged.

The existing P2 V3 materializer, revision selector, quality receipt, and P3 dataset
sealer own their existing identities. New private P3 code supplies deterministic
inputs and validates retained outputs through `ReadbackStore`.

## Fixed representation

Use schema ID `p3.binance.daily.v1`, Data API epoch 2 and partition spec version
`p3.utc-day.v1`. Field IDs are the following order, starting at 1. All fields are
non-nullable except `provider_published_at`:

| Field | Arrow type |
| --- | --- |
| ts_event | timestamp[ns,UTC] |
| date | string |
| instrument | string |
| opened_at | timestamp[ns,UTC] |
| closed_at_exclusive | timestamp[ns,UTC] |
| raw_close_time | int64 |
| raw_timestamp_unit | string |
| open | string |
| high | string |
| low | string |
| close | string |
| base_volume | string |
| quote_volume | string |
| trade_count | int64 |
| provider_published_at | timestamp[ns,UTC] |

The normalization version is `p3.binance.12-column.daily.v1`. Preserve exact
decimal values as canonical decimal text without quantization or binary floats.
Each numeric CSV field is at most 128 ASCII characters. Decimal fields accept
only digits with an optional decimal point and fractional digits; timestamps and
trade count accept only digits and must fit nonnegative signed int64. These are
parser resource/representation bounds; rejection does not alter economics.
Map Binance columns 0/6 to open/inclusive close integer timestamps, 1–5 to
OHLC/base volume, 7 to quote volume and 8 to nonnegative integer trade count.
Base/quote volumes and columns 9/10 (taker volumes) must be finite and nonnegative.
Column 11 must be literal `0`; retain all original columns in the ZIP evidence.
`instrument` is `BTCUSDT.BINANCE`; `date` is the requested UTC day.
`ts_event` equals `closed_at_exclusive`: the complete bar is available only at
this boundary. This does not change P3's existing close-label adapter.
Publication time remains null. `source_available_at` and `system_observed_at`
equal actual observation after both ZIP and CHECKSUM responses complete.
Acquisition `fetched_at` records durable retention of both raw response bodies;
partition `ingested_at` is actual materialization time, never a historical date.

The quality adapter maps base volume to the existing quality validator's
`volume`; its canonical-row digest remains distinct from Arrow IPC identity.
ProviderReceipt provider is `binance.public-archive`, capability MARKET_BARS.
Its query digest hashes canonical JSON with exactly `schema_version` =
`p3-daily-acquisition-query-v1`, `provider` as above, `day` as ISO date,
`instrument` = `BTCUSDT.BINANCE`, `interval` = `1d`, and `archive_url`/`checksum_url`
as the exact existing acquisition URLs. Evidence IDs are UUIDv5(namespace URL,
the respective URL + `#sha256=` + raw content digest). Evidence entries bind both
raw bodies and actual observation/retention times.
For each raw evidence entry, source_available_at and system_observed_at equal
the acquisition system_observed_at; fetched_at equals acquisition fetched_at.
Media type, byte length and content SHA equal the corresponding raw ArtifactRef.
The sole output digest hashes a canonical JSON document with exactly
`schema_version` = `p3-normalized-daily-row-v1` and `row` containing the 15 fields
above, timestamp values serialized as canonical UTC text. This document is
retained before materialization. P2 independently computes its Arrow IPC digest;
the provider receipt digest is the manifest's transform identity. No cycle or
new implementation of P2 Arrow hashing is introduced.
Retain ProviderReceipt as `canonical_json_bytes(model)`. Retain the quality
receipt as canonical JSON of exactly its existing `dataset`, `row_count`,
`issue_codes`, and `canonical_rows_sha256` fields; this is its existing digest
input, without a new wrapper or altered identity.

## Revision and selection

Partition key is `(BTCUSDT.BINANCE, YYYY-MM-DD)`. Derive revision series UUIDv5
using UUID namespace URL and `p3.research.daily/BTCUSDT.BINANCE/YYYY-MM-DD`.
The protected catalog compares the raw ZIP/CHECKSUM digest pair against the
current head. An identical pair reuses that exact manifest and its first retained
receipt/times, without a new revision. A changed pair receives the next contiguous
ordinal starting at 1. Derive partition UUIDv5 within the series from canonical
JSON text with exactly `archive_sha256`, `checksum_sha256`, `revision_ordinal`.
Each later manifest names its exact predecessor ID and digest. A later reversion
after a different raw pair is a new revision, preserving the observed history.
The protected catalog must serialize head comparison and ordinal assignment in
its existing database transaction; a filesystem inventory cannot grant this
authority or resolve concurrent revisions.
Completeness
means all retained revisions known to that protected catalog at sealing cutoff,
not an assertion of unavailable historical Binance versions.

The research query uses SYSTEM_OBSERVED, valid_at `2025-09-01T00:00:00Z`, and
the exact protected batch receipt `completed_at` as cutoff, bound by its publication.
The receipt must prove `frozen_at <= completed_at`.
Every included manifest must have ingested_at <= cutoff, and every acquired-known
raw item at or before that cutoff must resolve to a materialized catalog revision
or its idempotent current-head observation. Freeze the batch inventory before
completion; no concurrent acquisition may enter that batch after its freeze.
The inventory
contains the complete ordered `(day, revision_ordinal)` universe. Existing V3
selection verifies chains and selects the latest visible revision. Existing
P3 sealing enforces exact 2018-01-01 through 2025-08-31 coverage.

## Proof and authority boundary

A private typed revision proof binds source, DatasetEvidence reference, the exact
schema/query above, ordered inventory and protected acquisition/seal publication.
Each inventory entry associates acquisition receipt, provider receipt, quality
receipt and full V3 partition manifest. Validate canonical bytes and bounds,
reconstruct normalized rows, reproduce P2 materialization and P3 sealing using
readback-only storage, and compare exact retained identities before publication.

The protected producer must prove exhaustive catalog inventory and real origin,
timestamps, source/job/attempt and output custody. A local JSON receipt or an
agent's assertion does not provide that authority. Official OOS consumes a small
protected batch commitment after upstream raw validation; it must not recursively
load the full raw dataset into the existing 8192-reference output closure.
Missing protected proof remains HELD. Synthetic tests qualify only reconstruction
and rejection behavior; they cannot grant C01 or official InputSet authority.
The typed revision inventory, compact protected batch commitment and their exact
resource limits are a separate unresolved source contract. Until implemented and
reviewed, this raw normalization packet cannot enable official qualification or
OOS dispatch. Do not accept an opaque revision reference as C01 evidence.

## Raw-source checkpoint evidence (2026-09-11)

Acquisition/normalization: 27 focused tests passed. The earlier combined
acquisition, dataset, qualification and publication-producer run passed 50 tests;
the final added invalid-OHLC case passed in the focused run. The real Arrow/P2
sealer test uses all 2,800 synthetic research days and strict retained readback.
Unsupported datetime and date serialization both failed before the adapter fix.
Numeric overflow and the missing normalizer produced 10 failures before repair.
Static retains 117 pre-existing diagnostics; no new diagnostic remains.
Independent gpt-5.6-sol/high review passed this raw-source checkpoint only.
