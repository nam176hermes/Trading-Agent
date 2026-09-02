import assert from 'node:assert/strict';
import fs from 'node:fs';
import { registerHooks } from 'node:module';
import path from 'node:path';
import test, { afterEach } from 'node:test';
import { fileURLToPath, pathToFileURL } from 'node:url';

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === 'server-only') {
      return { shortCircuit: true, url: 'data:text/javascript,export%20{}' };
    }
    if (specifier === 'next/server') return nextResolve('next/server.js', context);
    if (specifier.startsWith('@/')) {
      const target = pathToFileURL(path.join(root, 'src', specifier.slice(2))).href;
      for (const suffix of ['.ts', '.tsx', '/index.ts']) {
        const candidate = `${target}${suffix}`;
        if (fs.existsSync(fileURLToPath(candidate))) return { shortCircuit: true, url: candidate };
      }
    }
    if (specifier.startsWith('.') && context.parentURL?.includes('/src/')) {
      const typescriptUrl = new URL(specifier.replace(/\.js$/, '') + '.ts', context.parentURL);
      if (fs.existsSync(fileURLToPath(typescriptUrl))) {
        return { shortCircuit: true, url: typescriptUrl.href };
      }
    }
    return nextResolve(specifier, context);
  },
});

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const originalFetch = globalThis.fetch;
const originalEnvironment = { ...process.env };

afterEach(() => {
  globalThis.fetch = originalFetch;
  process.env = { ...originalEnvironment };
});

function statusEnvelope(extra = {}) {
  return {
    schema_version: '2.0.0',
    trace_id: 'trace_control_fixture',
    generated_at: '2026-07-13T10:00:00Z',
    freshness: null,
    data: {
      api_liveness: 'UP',
      api_readiness: 'READY',
      backend_service_liveness: 'UNKNOWN',
      research_pipeline_health: 'STALE',
      research_data_freshness: {
        status: 'STALE', as_of: '2026-07-13T09:00:00Z', age_seconds: 3600,
        stale_after_seconds: 1800,
      },
      live_price_freshness: {
        status: 'UNKNOWN', as_of: null, age_seconds: null, stale_after_seconds: 1800,
      },
      database_status: 'AVAILABLE',
      requested_mode: 'PAPER',
      effective_mode: 'PAPER',
      execution_capability: 'NON_LIVE',
      kill_switch_state: 'INACTIVE',
      orders_count: null,
      trades_count: null,
    },
    ...extra,
  };
}

function canonicalMarketEnvelope({ freshness, snapshot } = {}) {
  const canonicalSnapshot = {
    continuity: {
      duplicate_open_times: [],
      missing_open_times: [],
      timeframe: '1m',
    },
    snapshot: {
      candles: [{
        close: '64240',
        high: '64260',
        instrument: { symbol: 'BTC', venue: 'FIXTURE', product_type: 'crypto_spot' },
        low: '64180',
        open: '64200',
        open_time: '2026-08-04T11:59:00Z',
        timeframe: '1m',
        volume: '12.5',
      }],
      instrument: { symbol: 'BTC', venue: 'FIXTURE', product_type: 'crypto_spot' },
      known_at: '2026-08-04T12:00:00Z',
      normalization_version: 'p10-fixture-v1',
      provenance: {
        fetched_at: '2026-08-04T12:00:00Z',
        normalization_version: 'p10-fixture-v1',
        observed_at: '2026-08-04T12:00:00Z',
        provider: 'deterministic-provider-free-fixture-v1',
        raw_evidence_sha256: 'a'.repeat(64),
        schema_version: 'market-data-v1',
      },
      schema_version: 'market-data-v1',
      timeframe: '1m',
    },
    snapshot_digest: 'b'.repeat(64),
  };
  return {
    schema_version: '2.0.0',
    trace_id: 'trace_market_fixture',
    generated_at: '2026-08-04T12:00:01Z',
    freshness: freshness ?? {
      status: 'FRESH', as_of: '2026-08-04T12:00:00Z', age_seconds: 1,
      stale_after_seconds: 60,
    },
    data: { snapshot: snapshot === undefined ? canonicalSnapshot : snapshot },
  };
}

test('Control API client uses one fixed loopback origin and validates a strict status envelope', async () => {
  const requested = [];
  globalThis.fetch = async (url) => {
    requested.push(String(url));
    return Response.json(statusEnvelope());
  };
  const { getControlStatus } = await import('../src/lib/trading/control-api.ts');

  const response = await getControlStatus();

  assert.deepEqual(requested, ['http://127.0.0.1:8400/v1/system/status']);
  assert.equal(response.trace_id, 'trace_control_fixture');
  assert.equal(response.data.effective_mode, 'PAPER');
  assert.equal(response.data.orders_count, null);
  assert.equal(response.data.trades_count, null);
});

test('Control API client accepts only a literal loopback test origin', async () => {
  const requested = [];
  globalThis.fetch = async (url) => {
    requested.push(String(url));
    return Response.json(statusEnvelope());
  };
  const { ControlApiClientError, getControlStatus } = await import('../src/lib/trading/control-api.ts');
  process.env.TRADING_CONTROL_API_ORIGIN = 'http://127.0.0.1:49123';
  await getControlStatus();
  assert.deepEqual(requested, ['http://127.0.0.1:49123/v1/system/status']);

  process.env.TRADING_CONTROL_API_ORIGIN = 'http://example.test:49123';
  await assert.rejects(getControlStatus(), (error) => error instanceof ControlApiClientError);
  assert.equal(requested.length, 1);
});

test('Control API client rejects extra contract fields without a filesystem fallback', async () => {
  globalThis.fetch = async () => Response.json(statusEnvelope({ debug_secret: 'forbidden' }));
  const { ControlApiClientError, getControlStatus } = await import('../src/lib/trading/control-api.ts');

  await assert.rejects(getControlStatus(), (error) => error instanceof ControlApiClientError);
});

test('Control API client cancels an oversized upstream response', async () => {
  let cancelled = false;
  globalThis.fetch = async () => new Response(new ReadableStream({
    pull(controller) { controller.enqueue(new Uint8Array([123])); },
    cancel() { cancelled = true; },
  }), { headers: { 'content-length': String(512 * 1024 + 1) } });
  const { ControlApiClientError, getControlStatus } = await import('../src/lib/trading/control-api.ts');

  await assert.rejects(getControlStatus(), (error) => error instanceof ControlApiClientError);
  assert.equal(cancelled, true);
});

test('dashboard canonical market-data client requests the closed fixture and retains freshness and provenance', async () => {
  const requested = [];
  globalThis.fetch = async (url) => {
    requested.push(String(url));
    return Response.json(canonicalMarketEnvelope());
  };
  const { getControlCanonicalMarketData } = await import('../src/lib/trading/control-api.ts');

  const response = await getControlCanonicalMarketData();

  assert.deepEqual(requested, [
    'http://127.0.0.1:8400/v1/market-data/latest?instrument=crypto_spot%3AFIXTURE%3ABTC&timeframe=1m',
  ]);
  assert.equal(response.data.snapshot.snapshot.candles[0].close, '64240');
  assert.equal(response.data.snapshot.snapshot.provenance.provider, 'deterministic-provider-free-fixture-v1');
  assert.equal(response.freshness.status, 'FRESH');
});

test('dashboard canonical market-data client rejects malformed upstream snapshots', async () => {
  const malformed = canonicalMarketEnvelope();
  malformed.data.snapshot.snapshot.candles[0].unexpected = 'forbidden';
  globalThis.fetch = async () => Response.json(malformed);
  const { ControlApiClientError, getControlCanonicalMarketData } = await import('../src/lib/trading/control-api.ts');

  await assert.rejects(getControlCanonicalMarketData(), (error) => error instanceof ControlApiClientError);
});

test('market BFF exposes canonical fresh, stale, and no-data states without legacy tickers', async () => {
  const { GET } = await import('../src/app/api/trading/market/route.ts');
  globalThis.fetch = async () => Response.json(canonicalMarketEnvelope());

  const fresh = await GET(new Request('http://dashboard.test/api/trading/market'));
  assert.equal(fresh.status, 200);
  const freshBody = await fresh.json();
  assert.equal(freshBody.data.snapshot.snapshot_digest, 'b'.repeat(64));
  assert.equal(freshBody.freshness.status, 'FRESH');
  assert.equal(Object.hasOwn(freshBody, 'tickers'), false);

  globalThis.fetch = async () => Response.json(canonicalMarketEnvelope({
    freshness: { status: 'STALE', as_of: '2026-08-04T12:00:00Z', age_seconds: 61, stale_after_seconds: 60 },
  }));
  const stale = await GET(new Request('http://dashboard.test/api/trading/market'));
  assert.equal((await stale.json()).freshness.status, 'STALE');

  globalThis.fetch = async () => Response.json(canonicalMarketEnvelope({
    freshness: { status: 'NO_DATA', as_of: null, age_seconds: null, stale_after_seconds: 60 },
    snapshot: null,
  }));
  const unavailable = await GET(new Request('http://dashboard.test/api/trading/market'));
  const unavailableBody = await unavailable.json();
  assert.equal(unavailable.status, 200);
  assert.equal(unavailableBody.data.snapshot, null);
  assert.equal(unavailableBody.freshness.status, 'NO_DATA');
  assert.equal(Object.hasOwn(unavailableBody, 'tickers'), false);

  const malformed = canonicalMarketEnvelope();
  malformed.data.snapshot.snapshot.provenance.provider = 'untrusted-provider';
  globalThis.fetch = async () => Response.json(malformed);
  const upstreamUnavailable = await GET(new Request('http://dashboard.test/api/trading/market'));
  assert.equal(upstreamUnavailable.status, 503);
  assert.deepEqual(await upstreamUnavailable.json(), {
    ok: false,
    code: 'CONTROL_API_UNAVAILABLE',
    message: 'Canonical trading data is unavailable.',
  });
});

test('canonical market ticker view preserves fresh and stale provenance and never invents a quote for no data', async () => {
  const { parseCanonicalMarketTickerView } = await import('../src/lib/trading/market-data-view.ts');

  const fresh = parseCanonicalMarketTickerView(canonicalMarketEnvelope());
  assert.deepEqual(fresh, {
    kind: 'snapshot',
    freshness: 'FRESH',
    close: '64240',
    knownAt: '2026-08-04T12:00:00Z',
    provider: 'deterministic-provider-free-fixture-v1',
    evidenceDigest: 'a'.repeat(64),
    snapshotDigest: 'b'.repeat(64),
  });

  const stale = parseCanonicalMarketTickerView(canonicalMarketEnvelope({
    freshness: { status: 'STALE', as_of: '2026-08-04T12:00:00Z', age_seconds: 61, stale_after_seconds: 60 },
  }));
  assert.equal(stale.kind, 'snapshot');
  assert.equal(stale.freshness, 'STALE');

  const noData = parseCanonicalMarketTickerView(canonicalMarketEnvelope({
    freshness: { status: 'NO_DATA', as_of: null, age_seconds: null, stale_after_seconds: 60 },
    snapshot: null,
  }));
  assert.deepEqual(noData, { kind: 'no_data' });

  const malformed = canonicalMarketEnvelope();
  malformed.data.snapshot.snapshot.provenance.provider = 'untrusted-provider';
  assert.equal(parseCanonicalMarketTickerView(malformed), null);
});

test('read-only dashboard routes and server data modules have no legacy filesystem reader', () => {
  const routeRoot = path.join(root, 'src/app/api/trading');
  const routes = [];
  const visit = (directory) => {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      const target = path.join(directory, entry.name);
      if (entry.isDirectory()) visit(target);
      else if (entry.name === 'route.ts') routes.push(target);
    }
  };
  visit(routeRoot);
  for (const route of routes) {
    const source = fs.readFileSync(route, 'utf8');
    if (!/export async function GET/.test(source)) continue;
    assert.doesNotMatch(source, /@\/lib\/trading\/collectors/, route);
    if (!/export async function POST/.test(source)) {
      assert.doesNotMatch(source, /from ['"](?:fs|fs\/promises|path|os)['"]/, route);
    }
  }
  for (const relative of ['src/lib/trading/data.ts', 'src/lib/trading/runtime-status.ts']) {
    const source = fs.readFileSync(path.join(root, relative), 'utf8');
    assert.doesNotMatch(source, /from ['"](?:fs|fs\/promises|path|os)['"]/, relative);
    assert.doesNotMatch(source, /@\/lib\/trading\/collectors|\.\/collectors/, relative);
  }
});

test('unsupported read domains expose a typed fail-closed response', async () => {
  for (const route of ['reports', 'alerts', 'exchange-status']) {
    const { GET } = await import(`../src/app/api/trading/${route}/route.ts`);
    const response = await GET();
    assert.equal(response.status, 503, route);
    const body = await response.json();
    assert.equal(body.error.code, 'SOURCE_UNAVAILABLE', route);
    assert.match(body.error.message, /canonical Control API\/PostgreSQL read contract/, route);
  }
});

test('legacy dashboard adapters do not fabricate unavailable operational values', () => {
  const data = fs.readFileSync(path.join(root, 'src/lib/trading/data.ts'), 'utf8');
  const status = fs.readFileSync(path.join(root, 'src/app/api/trading/status/route.ts'), 'utf8');
  const meta = fs.readFileSync(path.join(root, 'src/app/api/trading/meta/route.ts'), 'utf8');
  const mode = fs.readFileSync(path.join(root, 'src/app/api/trading/mode/route.ts'), 'utf8');
  assert.doesNotMatch(data, /(?:positions|openOrders|realizedPnl|unrealizedPnl): 0/);
  assert.match(data, /volume24h: null/);
  assert.doesNotMatch(data, /new Date\(0\)/);
  assert.match(status, /llmWorking: null/);
  assert.doesNotMatch(meta, /process\.cwd\(\)/);
  assert.match(mode, /CLI_REQUIRED/);
  assert.doesNotMatch(mode, /updatePrivateLocalStateFile|modeFile|request-body/);
  assert.doesNotMatch(mode, /from ['"](?:fs|path)['"]/);

  const modeToggle = fs.readFileSync(
    path.join(root, 'src/components/trading/mode-toggle.tsx'), 'utf8',
  );
  const settings = fs.readFileSync(
    path.join(root, 'src/app/dashboard/settings/page.tsx'), 'utf8',
  );
  assert.match(modeToggle, /Requested.*Effective/s);
  assert.match(modeToggle, /Change mode via CLI/);
  assert.match(settings, /Change mode via CLI/);
  assert.doesNotMatch(settings, /\(\['paper', 'dryrun', 'live'\]/);
});
