type JsonObject = Record<string, unknown>;

export type CanonicalMarketTickerView =
  | { kind: 'no_data' }
  | {
    kind: 'snapshot';
    freshness: 'FRESH' | 'STALE';
    close: string;
    knownAt: string;
    provider: string;
    evidenceDigest: string;
    snapshotDigest: string;
  };

const P10_PROVIDER = 'deterministic-provider-free-fixture-v1';
const P10_TIMEFRAME = '1m';
const SHA256 = /^[0-9a-f]{64}$/;
const CANONICAL_DECIMAL = /^(?:0|[1-9]\d*)(?:\.\d+)?$/;

function isObject(value: unknown): value is JsonObject {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function hasExactKeys(value: JsonObject, keys: readonly string[]): boolean {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}

function canonicalUtc(value: unknown): value is string {
  return typeof value === 'string'
    && value.length <= 35
    && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$/.test(value)
    && Number.isFinite(Date.parse(value));
}

function canonicalInstrument(value: unknown): boolean {
  return isObject(value)
    && hasExactKeys(value, ['symbol', 'venue', 'product_type'])
    && value.symbol === 'BTC'
    && value.venue === 'FIXTURE'
    && value.product_type === 'crypto_spot';
}

function canonicalFreshness(value: unknown): 'FRESH' | 'STALE' | 'NO_DATA' | null {
  if (!(isObject(value)
    && hasExactKeys(value, ['status', 'as_of', 'age_seconds', 'stale_after_seconds'])
    && Number.isSafeInteger(value.stale_after_seconds)
    && (value.as_of === null || canonicalUtc(value.as_of))
    && (value.age_seconds === null || (typeof value.age_seconds === 'number'
      && Number.isSafeInteger(value.age_seconds) && value.age_seconds >= 0)))) return null;
  if (value.status === 'NO_DATA') {
    return value.as_of === null && value.age_seconds === null ? 'NO_DATA' : null;
  }
  return (value.status === 'FRESH' || value.status === 'STALE')
    && value.as_of !== null && value.age_seconds !== null
    ? value.status
    : null;
}

/**
 * Treat the dashboard BFF payload as an untrusted boundary too. The server
 * validates the full generated Control API DTO; this client parser validates
 * every field it will display and returns no quote for an absent snapshot.
 */
export function parseCanonicalMarketTickerView(value: unknown): CanonicalMarketTickerView | null {
  if (!(isObject(value)
    && hasExactKeys(value, ['schema_version', 'trace_id', 'generated_at', 'data', 'freshness'])
    && value.schema_version === '2.0.0'
    && canonicalUtc(value.generated_at)
    && isObject(value.data)
    && hasExactKeys(value.data, ['snapshot']))) return null;

  const freshness = canonicalFreshness(value.freshness);
  if (value.data.snapshot === null) return freshness === 'NO_DATA' ? { kind: 'no_data' } : null;
  if (freshness !== 'FRESH' && freshness !== 'STALE') return null;

  const canonical = value.data.snapshot;
  if (!(isObject(canonical)
    && hasExactKeys(canonical, ['continuity', 'snapshot', 'snapshot_digest'])
    && typeof canonical.snapshot_digest === 'string' && SHA256.test(canonical.snapshot_digest)
    && isObject(canonical.snapshot)
    && hasExactKeys(canonical.snapshot, [
      'candles', 'instrument', 'known_at', 'normalization_version', 'provenance', 'schema_version', 'timeframe',
    ])
    && canonicalInstrument(canonical.snapshot.instrument)
    && canonicalUtc(canonical.snapshot.known_at)
    && canonical.snapshot.timeframe === P10_TIMEFRAME
    && Array.isArray(canonical.snapshot.candles)
    && canonical.snapshot.candles.length > 0)) return null;

  const candle = canonical.snapshot.candles.at(-1);
  const provenance = canonical.snapshot.provenance;
  if (!(isObject(candle)
    && hasExactKeys(candle, ['close', 'high', 'instrument', 'low', 'open', 'open_time', 'timeframe', 'volume'])
    && typeof candle.close === 'string' && CANONICAL_DECIMAL.test(candle.close)
    && canonicalInstrument(candle.instrument)
    && canonicalUtc(candle.open_time)
    && candle.timeframe === P10_TIMEFRAME
    && isObject(provenance)
    && hasExactKeys(provenance, [
      'fetched_at', 'normalization_version', 'observed_at', 'provider', 'raw_evidence_sha256', 'schema_version',
    ])
    && provenance.provider === P10_PROVIDER
    && typeof provenance.raw_evidence_sha256 === 'string' && SHA256.test(provenance.raw_evidence_sha256))) return null;

  return {
    kind: 'snapshot',
    freshness,
    close: candle.close,
    knownAt: canonical.snapshot.known_at,
    provider: provenance.provider,
    evidenceDigest: provenance.raw_evidence_sha256,
    snapshotDigest: canonical.snapshot_digest,
  };
}
