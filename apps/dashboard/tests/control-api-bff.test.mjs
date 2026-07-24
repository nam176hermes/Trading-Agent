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

afterEach(() => {
  globalThis.fetch = originalFetch;
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
  assert.match(mode, /PAPER_ONLY_RELEASE/);
  assert.match(mode, /updatePrivateLocalStateFile/);
  assert.doesNotMatch(mode, /from ['"](?:fs|path)['"]/);
});
