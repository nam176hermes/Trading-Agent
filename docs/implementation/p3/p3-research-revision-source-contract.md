# P3 research revision source contract

Status: raw normalization and standalone reconstruction source; protected batch
authority remains HELD.
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
The protected producer compares the raw ZIP/CHECKSUM digest pair against the
current head. An identical pair reuses that exact manifest and its first retained
receipt/times, without a new revision. A changed pair receives the next contiguous
ordinal starting at 1. Derive partition UUIDv5 within the series from canonical
JSON text with exactly `archive_sha256`, `checksum_sha256`, `revision_ordinal`.
Each later manifest names its exact predecessor ID and digest. A later reversion
after a different raw pair is a new revision, preserving the observed history.
The protected acquisition/seal workflow must serialize complete batches with
an immutable predecessor inventory and no-clobber publication. The first batch
requires an explicit first-batch review; later batches must name the exact prior
protected batch. A local inventory alone cannot prove that no concurrent batch
or predecessor was omitted. No new database catalog or job operation is added.
Completeness
means all retained revisions in the protected predecessor chain at sealing cutoff,
not an assertion of unavailable historical Binance versions.

The research query uses SYSTEM_OBSERVED, valid_at `2025-09-01T00:00:00Z`, and
the exact protected batch receipt `completed_at` as cutoff, bound by its publication.
The receipt must prove `frozen_at <= completed_at`.
Every included manifest must have ingested_at <= cutoff, and every acquired-known
raw item at or before that cutoff must resolve to a materialized inventory revision
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

The protected producer must prove exhaustive batch inventory and real origin,
timestamps, source/workflow run/attempt and output custody. A local JSON receipt or an
agent's assertion does not provide that authority. Official OOS consumes a small
protected batch commitment after upstream raw validation; it must not recursively
load the full raw dataset into the existing 8192-reference output closure.
Missing protected proof remains HELD. Synthetic tests qualify only reconstruction
and rejection behavior; they cannot grant C01 or official InputSet authority.
The standalone scripts in runbook section 5 own acquisition and offline sealing;
section 6's official workflow enum and SQL publication remain unchanged.
The typed reconstruction inventory is implemented below. Protected batch commitment,
provenance and receipt-admission contracts remain unresolved. Until implemented
and reviewed, raw reconstruction cannot enable official qualification or OOS
dispatch. Do not accept an opaque revision reference as C01 evidence.

## Readback-only reconstruction seam

`P3RevisionEntry` has exactly `day`, `acquisition_ref`, `normalized_ref`,
`provider_ref`, `quality_ref`, and `partition` (existing full V3 manifest).
The four refs name JSON blobs, each positive and at most 65,536 bytes. Derive
Parquet reference from the V3 manifest's SHA/size with the existing media/locator;
each one-row Parquet is positive and at most 1 MiB.

`P3ResearchRevisionInventory` has exactly `schema_version` =
`p3-research-revision-inventory-v1`, `digest`, `source`, `policy_digest`,
`entries`. Entries are ordered by `(day, revision_ordinal)`, unique, at least
2,800 and at most P2's 100,000 manifest limit. Its JSON ArtifactRef is positive
and at most 64 MiB. This inventory is a declared universe, not evidence that an
upstream protected predecessor or concurrent batch was not omitted.

All top and entry refs must have the stated exact media type, positive bounded
size and `locator == content_sha256 + '.blob'` before reading their bytes.
The reconstruction validator takes that inventory reference plus the expected
source, policy, query and DatasetEvidence reference. Schema is the source-defined
constant; query mode must be SYSTEM_OBSERVED and valid_at must be
`2025-09-01T00:00:00Z`. Cutoff remains the declared actual completion timestamp,
which protected admission must separately bind to the seal receipt. It is strictly
read-only: revalidate each acquisition, normalized document, ProviderReceipt and
four-field quality receipt; require exact day/key/schema, identity/time bindings
and predecessor links; call P2 materialization through ReadbackStore and compare
the exact manifest/Parquet; then build the V3 snapshot and reproduce the existing
P3 sealer and DatasetEvidence. It enforces the fixed research range, ordinal
prefix, raw-pair idempotency/reversion rule, and ingested_at <= query.cutoff.
Any missing retained byte or mismatch fails before an output artifact is written.
The reconstruction operation retains the existing 1 GiB aggregate artifact-byte
ceiling. Count declared bytes conservatively, including repeated references,
inventory, raw ZIP/CHECKSUM, normalized/provider/quality documents, Parquet,
dataset/snapshot and daily rows; reject before reading a reference that would
cross the ceiling. DatasetEvidence JSON is at most 2 MiB; snapshot JSON is at
most 64 MiB; daily-row JSON is at most 65,536 bytes. Resource rejection never
changes date coverage or numerical policy.
This validator alone does not grant C01; protected first/prior-batch provenance,
backup custody and independent InputSet review remain required.
The full inventory is a standalone reconstruction input. It must not become
`PITProof.revision_proof_ref` or an ArtifactRef child of that proof. The future
compact protected commitment binds its digest/count/root identity as an admitted
external evidence boundary; this preserves the existing OOS 8,192-reference
closure. No graph limit is weakened and this packet adds no unvalidated leaf rule.

## Raw-source checkpoint evidence (2026-09-11)

Acquisition/normalization: 27 focused tests passed. The earlier combined
acquisition, dataset, qualification and publication-producer run passed 50 tests;
the final added invalid-OHLC case passed in the focused run. The real Arrow/P2
sealer test uses all 2,800 synthetic research days and strict retained readback.
Unsupported datetime and date serialization both failed before the adapter fix.
Numeric overflow and the missing normalizer produced 10 failures before repair.
Static retains 117 pre-existing diagnostics; no new diagnostic remains.
Independent gpt-5.6-sol/high review passed this raw-source checkpoint only.

Standalone reconstruction first failed four tests because the validator was
absent, including the complete 2,800-day fixture. The final acquisition, dataset
and reconstruction suite passed 57 tests in 156.98 seconds. It covers source,
policy, reference bounds, missing/duplicate days, partition namespace/key,
ordinal, normalized/provider/quality evidence, deterministic UUIDs, identical
consecutive raw revisions, read budget and zero writes during reconstruction.
Two intermediate negative runs failed in test mutation helpers (computed P2
digest and strict JSON model parsing); corrected mutations reached the validator
in the final green run. These helper failures are not claimed as source defects.

The subsequent fold-builder test failed on unsupported raw `date` hashing.
The correction serializes only its date payload fields to ISO before canonical
hashing. All three fold tests passed in 10.48 seconds, including exact
365/366/365 return counts and decision/return references over 2,800 synthetic
rows. No fold ranges, legacy identities or economic policies changed.
Independent gpt-5.6-sol/high review passed the reconstruction and fold correction
scope. Contract generation check passed; static retained the same 117 existing
diagnostics. Protected provenance, backup and official admission remain HELD.

## Compact commitment representation (structural source only)

The private `research_custody.py` models describe a compact commitment and its
small backup receipt. They are not connected to official dispatch or C01 yet.
`P3ResearchBatchCommitment` binds source/policy, RESEARCH, the exact query,
inventory content SHA/byte size/entry count, DatasetEvidence content SHA and
semantic digest, snapshot content SHA, batch ordinal, predecessor commitment
content SHA, inventory frozen/completed times, issuance time, producer
repository/workflow/run/attempt, backup receipt reference, status and safe
authority. FIRST has ordinal one and no predecessor; a later protected producer
must compare the actual prior ordinal plus one and exact content SHA.

Every `content_sha256` is the hash of complete canonical artifact bytes,
including a model's `digest`. `digest` retains the existing DigestModel semantic
meaning. Inventory and predecessor hashes are commitments, not recursive
ArtifactRefs. The backup receipt is an ordinary bounded JSON reference (at most
65,536 bytes), so it remains visible to the closed artifact graph.

`P3ResearchBackupReceipt` binds source, inventory/dataset/snapshot content SHAs,
the full backup object-inventory content SHA, logical destination
`p3.research.backup`, immutable object version equal to that object-inventory SHA,
object count/byte total, verification time and producer identity. It intentionally
does not bind the future commitment hash; the commitment references this earlier
receipt. This avoids a hash cycle. Its object inventory, physical destination
and readback remain external evidence that protected admission must verify.

The inventory's `completed_at` is the query cutoff after acquisition and
materialization freeze. Dataset sealing/reconstruction and backup follow;
commitment issuance is after backup verification. Thus the dataset cutoff never
depends on completing its own backup or commitment. The structural validator
requires `frozen_at <= completed_at <= backup.verified_at <= issued_at`, exact
source/run/attempt and fixed 2,800-day retrospective dataset bindings.

The proposed producer identity is a separate protected standalone
`p3-research-inputs.yml` workflow. That workflow, its approved pre-run request,
serialization/no-clobber, actual backup producer and attestation/admission checks
are still missing. Recorded workflow strings and a self-digest cannot replace
them. The later existing P3 authority review must cover both operation-input and
commitment digests, with authentic source/producer evidence checked before
enqueue and spawn. No new official operation, SQL catalog, job authority or
unvalidated artifact leaf is introduced by these structural models.

The consumer checks bounded child references, exact retrospective disclosure,
distinct daily rows, the existing ordered-row digest formula, consistent sizes
for shared content hashes and lower bounds for backup object/byte accounting.
Regression tests first reproduced acceptance of invalid closure/accounting and
an arbitrary ordered-row digest. The final compact suite passed 39 tests in
11.26 seconds; independent gpt-5.6-sol/high review passed this structural scope.
These synthetic tests do not authenticate the declared producer or custody.
