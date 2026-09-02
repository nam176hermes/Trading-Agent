import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { afterEach, beforeEach } from 'node:test';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { registerHooks } from 'node:module';

const ROOT = fileURLToPath(new URL('..', import.meta.url));
const SESSION_SECRET = Array.from({ length: 32 }, (_, index) => (index % 16).toString(16)).join('');
const ORIGINAL_ENV = { ...process.env };
let dataRoot;

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === 'server-only') return { shortCircuit: true, url: 'data:text/javascript,export%20{}' };
    if (specifier === 'next/server') {
      return { shortCircuit: true, url: pathToFileURL(path.join(ROOT, 'node_modules/next/server.js')).href };
    }
    if (specifier.startsWith('@/')) {
      const target = pathToFileURL(path.join(ROOT, 'src', specifier.slice(2))).href;
      for (const suffix of ['.ts', '.tsx', '/index.ts']) {
        const candidate = `${target}${suffix}`;
        if (fs.existsSync(fileURLToPath(candidate))) return { shortCircuit: true, url: candidate };
      }
    }
    if (specifier.startsWith('.') && context.parentURL?.includes('/src/')) {
      const target = new URL(specifier.replace(/\.js$/, '') + '.ts', context.parentURL);
      if (fs.existsSync(fileURLToPath(target))) return { shortCircuit: true, url: target.href };
    }
    return nextResolve(specifier, context);
  },
});

beforeEach(() => {
  dataRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'dashboard-request-hardening-'));
  process.env.NODE_ENV = 'test';
  process.env.TRADING_DATA_ROOT = dataRoot;
  process.env.TRADING_DASHBOARD_SESSION_SECRET = SESSION_SECRET;
  process.env.TRADING_DASHBOARD_ADMIN_PASSWORD = 'synthetic-admin-password';
  process.env.TRADING_DASHBOARD_OPERATOR_PASSWORD = 'synthetic-operator-password';
  delete process.env.TRADING_DASHBOARD_TRUSTED_PROXY_SECRET;
});

afterEach(() => {
  process.env = { ...ORIGINAL_ENV };
  fs.rmSync(dataRoot, { recursive: true, force: true });
});

async function authorizedRequest(url, role, body) {
  const { issueSession } = await import('../src/lib/trading/session.ts');
  return new Request(url, {
    method: 'POST',
    headers: {
      cookie: `trading_session=${issueSession(role)}`,
      origin: new URL(url).origin,
      'content-type': 'application/json',
    },
    body: JSON.stringify(body),
  });
}

test('session limiter ignores spoofed forwarding-header rotation unless the proxy secret matches', async () => {
  const route = await import('../src/app/api/auth/session/route.ts');
  const url = 'https://dashboard.test/api/auth/session';

  for (let attempt = 0; attempt < 5; attempt += 1) {
    const response = await route.POST(new Request(url, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        'cf-connecting-ip': `198.51.100.${attempt + 10}`,
        'x-forwarded-for': `203.0.113.${attempt + 10}`,
      },
      body: JSON.stringify({ password: 'wrong' }),
    }));
    assert.equal(response.status, 401);
  }
  const rotated = await route.POST(new Request(url, {
    method: 'POST',
    headers: { 'content-type': 'application/json', 'cf-connecting-ip': '198.51.100.99' },
    body: JSON.stringify({ password: 'wrong' }),
  }));
  assert.equal(rotated.status, 429);

  process.env.TRADING_DASHBOARD_TRUSTED_PROXY_SECRET = 'synthetic-proxy-secret';
  for (let attempt = 0; attempt < 5; attempt += 1) {
    const response = await route.POST(new Request(url, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        'cf-connecting-ip': '198.51.100.200',
        'x-trusted-proxy-secret': 'synthetic-proxy-secret',
      },
      body: JSON.stringify({ password: 'wrong' }),
    }));
    assert.equal(response.status, 401);
  }
  const distinctTrustedClient = await route.POST(new Request(url, {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      'cf-connecting-ip': '198.51.100.201',
      'x-trusted-proxy-secret': 'synthetic-proxy-secret',
    },
    body: JSON.stringify({ password: 'wrong' }),
  }));
  assert.equal(distinctTrustedClient.status, 401);
});

test('shared reader rejects declared, chunked, and invalid UTF-8 request bodies', async () => {
  const route = await import('../src/app/api/auth/session/route.ts');
  const url = 'https://dashboard.test/api/auth/session';
  process.env.TRADING_DASHBOARD_TRUSTED_PROXY_SECRET = 'reader-test-proxy-secret';
  const headers = {
    'content-type': 'application/json',
    'cf-connecting-ip': '198.51.100.210',
    'x-trusted-proxy-secret': 'reader-test-proxy-secret',
  };
  const declared = await route.POST(new Request(url, {
    method: 'POST', headers: { ...headers, 'content-length': '16385' }, body: '{}',
  }));
  assert.equal(declared.status, 413);

  const chunked = await route.POST(new Request(url, {
    method: 'POST',
    headers,
    body: new ReadableStream({
      pull(controller) { controller.enqueue(new Uint8Array(16_385)); controller.close(); },
    }),
    duplex: 'half',
  }));
  assert.equal(chunked.status, 413);

  const invalidUtf8 = await route.POST(new Request(url, {
    method: 'POST', headers, body: new Uint8Array([0xc3, 0x28]),
  }));
  assert.equal(invalidUtf8.status, 400);
});

test('bounded JSON reader rejects syntactically invalid UTF-8 text and releases its stream', async () => {
  const { readBoundedJsonBody } = await import('../src/lib/trading/request-body.ts');
  const result = await readBoundedJsonBody(
    new Request('https://dashboard.test/api/trading/run', {
      method: 'POST',
      body: '{not json}',
    }),
    1024,
  );
  assert.deepEqual(result, { ok: false, reason: 'invalid' });
});

test('retired compatibility routes return typed unavailable without reading or changing local state', async () => {
  const [updateStop, plan, correlation, mode, killSwitch, watchlist] = await Promise.all([
    import('../src/app/api/trading/update-stop/route.ts'),
    import('../src/app/api/trading/plan/route.ts'),
    import('../src/app/api/trading/correlation/route.ts'),
    import('../src/app/api/trading/mode/route.ts'),
    import('../src/app/api/trading/kill-switch/route.ts'),
    import('../src/app/api/trading/watchlist/route.ts'),
  ]);

  const stateDir = path.join(dataRoot, 'memory');
  fs.mkdirSync(stateDir, { recursive: true, mode: 0o700 });
  const stopsPath = path.join(stateDir, 'trailing_stops.json');
  const watchlistPath = path.join(stateDir, 'watchlist.json');
  fs.writeFileSync(stopsPath, '{"BTC":{"stop":90}}', { mode: 0o600 });
  fs.writeFileSync(watchlistPath, '{"symbols":["BTC"]}', { mode: 0o600 });

  for (const [route, url, body] of [
    [plan, 'plan', { query: 'synthetic query', keywords: ['Risk'] }],
    [updateStop, 'update-stop', { symbol: 'BTC', stopLoss: 100 }],
    [watchlist, 'watchlist', { action: 'add', symbol: 'ETH' }],
  ]) {
    const response = await route.POST(await authorizedRequest(
      `https://dashboard.test/api/trading/${url}`, 'operator', body,
    ));
    assert.equal(response.status, 503);
    assert.equal((await response.json()).error.code, 'COMMAND_UNAVAILABLE');
  }
  for (const response of [await correlation.GET(), await watchlist.GET()]) {
    assert.equal(response.status, 503);
    assert.equal((await response.json()).error.code, 'SOURCE_UNAVAILABLE');
  }
  assert.equal(fs.readFileSync(stopsPath, 'utf8'), '{"BTC":{"stop":90}}');
  assert.equal(fs.readFileSync(watchlistPath, 'utf8'), '{"symbols":["BTC"]}');

  for (const body of [{ mode: 'paper', extra: true }, { mode: 'paper' }]) {
    const response = await mode.POST(await authorizedRequest(
      'https://dashboard.test/api/trading/mode', 'admin', body,
    ));
    assert.equal(response.status, 403);
    assert.equal((await response.json()).code, 'CLI_REQUIRED');
  }
  assert.equal(fs.existsSync(path.join(dataRoot, '.mode')), false);

  assert.equal((await killSwitch.POST(await authorizedRequest('https://dashboard.test/api/trading/kill-switch', 'admin', {
    action: 'off', reason: 'not allowed for off',
  }))).status, 403);
  assert.equal((await killSwitch.POST(await authorizedRequest('https://dashboard.test/api/trading/kill-switch', 'admin', {
    action: 'on', reason: 'synthetic safety drill',
  }))).status, 400);

});

test('local state reader rejects symlinks, nonregular files, and oversized regular files', async () => {
  const { readLocalStateFile } = await import('../src/lib/trading/local-state.ts');
  const stateDir = path.join(dataRoot, 'state');
  fs.mkdirSync(stateDir, { recursive: true, mode: 0o700 });

  const regular = path.join(stateDir, 'regular.json');
  const symlink = path.join(stateDir, 'link.json');
  fs.writeFileSync(regular, '{"safe":true}', { mode: 0o600 });
  fs.symlinkSync(regular, symlink);
  assert.throws(() => readLocalStateFile(symlink, 1024), /unsafe local state file/i);

  assert.throws(() => readLocalStateFile('/dev/null', 1024), /unsafe local state file/i);

  const oversized = path.join(stateDir, 'oversized.json');
  fs.writeFileSync(oversized, 'x'.repeat(1025), { mode: 0o600 });
  assert.throws(() => readLocalStateFile(oversized, 1024), /too large/i);
});

test('local state writer creates private modes, atomically replaces, and cleans temporary files', async () => {
  const { writePrivateLocalStateFile } = await import('../src/lib/trading/local-state.ts');
  const stateDir = path.join(dataRoot, 'private-state');
  const target = path.join(stateDir, 'state.json');

  writePrivateLocalStateFile(target, '{"version":1}');
  writePrivateLocalStateFile(target, '{"version":2}');

  assert.equal(fs.readFileSync(target, 'utf8'), '{"version":2}');
  assert.equal(fs.statSync(stateDir).mode & 0o777, 0o700);
  assert.equal(fs.statSync(target).mode & 0o777, 0o600);
  assert.deepEqual(fs.readdirSync(stateDir), ['state.json']);
});

test('mode route ignores persisted state and preserves it while requiring CLI', async () => {
  const mode = await import('../src/app/api/trading/mode/route.ts');
  const target = path.join(dataRoot, '.mode');
  const malformed = 'paper\nunknown\n';
  fs.writeFileSync(target, malformed, { mode: 0o600 });

  const response = await mode.POST(await authorizedRequest(
    'https://dashboard.test/api/trading/mode', 'admin', { mode: 'paper' },
  ));

  assert.equal(response.status, 403);
  assert.equal((await response.json()).code, 'CLI_REQUIRED');
  assert.equal(fs.readFileSync(target, 'utf8'), malformed);
  assert.equal(fs.readdirSync(dataRoot).some((entry) => entry.startsWith('..mode.tmp.')), false);
});

test('local state writer rejects a symlinked state directory without touching its target', async () => {
  const { writePrivateLocalStateFile } = await import('../src/lib/trading/local-state.ts');
  const outside = path.join(dataRoot, 'outside-state');
  const linked = path.join(dataRoot, 'linked-state');
  fs.mkdirSync(outside, { mode: 0o755 });
  fs.symlinkSync(outside, linked);

  assert.throws(
    () => writePrivateLocalStateFile(path.join(linked, 'state.json'), '{"unsafe":true}'),
    /unsafe local state/i,
  );
  assert.equal(fs.existsSync(path.join(outside, 'state.json')), false);
  assert.equal(fs.statSync(outside).mode & 0o777, 0o755);
});

test('local state writer rejects a safe leaf below an attacker-writable ancestor', async () => {
  const { writePrivateLocalStateFile } = await import('../src/lib/trading/local-state.ts');
  const unsafeAncestor = path.join(dataRoot, 'unsafe-ancestor');
  const stateDirectory = path.join(unsafeAncestor, 'private-state');
  const target = path.join(stateDirectory, 'state.json');
  fs.mkdirSync(stateDirectory, { recursive: true, mode: 0o700 });
  fs.chmodSync(stateDirectory, 0o700);
  fs.chmodSync(unsafeAncestor, 0o777);

  assert.throws(
    () => writePrivateLocalStateFile(target, '{"unsafe":true}'),
    /unsafe ancestor|writable by another principal/i,
  );
  assert.equal(fs.existsSync(target), false);
});

test('shared reader joins decoded tiny chunks without changing UTF-8 body semantics', async () => {
  const { readBoundedUtf8Body } = await import('../src/lib/trading/request-body.ts');
  const encoded = new TextEncoder().encode('{"symbol":"BTC","note":"€"}');
  const result = await readBoundedUtf8Body(new Request('https://dashboard.test/tiny-chunks', {
    method: 'POST',
    body: new ReadableStream({
      start(controller) {
        for (const byte of encoded) controller.enqueue(new Uint8Array([byte]));
        controller.close();
      },
    }),
    duplex: 'half',
  }), 1024);

  assert.deepEqual(result, { ok: true, text: '{"symbol":"BTC","note":"€"}' });
});

test('only active JSON routes use the shared bounded reader', () => {
  const routes = [
    'src/app/api/auth/session/route.ts',
    'src/app/api/trading/kill-switch/route.ts',
  ];
  for (const route of routes) {
    const source = fs.readFileSync(path.join(ROOT, route), 'utf8');
    assert.doesNotMatch(source, /request\.json\(/, route);
    assert.match(source, /request-body/, route);
  }
  const mode = fs.readFileSync(
    path.join(ROOT, 'src/app/api/trading/mode/route.ts'), 'utf8',
  );
  assert.doesNotMatch(mode, /request-body|request\.json\(/);
  for (const route of ['plan', 'update-stop', 'watchlist']) {
    const source = fs.readFileSync(
      path.join(ROOT, `src/app/api/trading/${route}/route.ts`), 'utf8',
    );
    assert.doesNotMatch(source, /request-body|request\.json\(/);
  }
});

test('local state accepts a sticky mapped-owner system ancestor', async () => {
  const { readLocalStateFile } = await import('../src/lib/trading/local-state.ts');
  const stateDir = path.join(dataRoot, 'mapped-ancestor-state');
  const target = path.join(stateDir, 'state.json');
  fs.mkdirSync(stateDir, { recursive: true, mode: 0o700 });
  fs.writeFileSync(target, '{"safe":true}', { mode: 0o600 });

  const originalLstatSync = fs.lstatSync;
  fs.lstatSync = (...args) => {
    const metadata = originalLstatSync(...args);
    if (args[0] === os.tmpdir()) {
      Object.defineProperty(metadata, 'uid', { configurable: true, value: 65534 });
      Object.defineProperty(metadata, 'mode', {
        configurable: true,
        value: metadata.mode | 0o1022,
      });
    }
    return metadata;
  };
  try {
    assert.equal(readLocalStateFile(target, 1024), '{"safe":true}');
  } finally {
    fs.lstatSync = originalLstatSync;
  }
});

test('halt banner requires CLI for kill-switch clear', () => {
  const source = fs.readFileSync(path.join(
    ROOT, 'src/components/trading/halt-banner.tsx',
  ), 'utf8');
  assert.match(source, /Clear via CLI/);
  assert.doesNotMatch(source, /fetch\('\/api\/trading\/kill-switch'|action: 'off'|Override/);
});

test('retired compatibility UI names canonical unavailability and has no mutation caller', () => {
  const sources = {
    plan: fs.readFileSync(path.join(ROOT, 'src/components/trading/plan-builder.tsx'), 'utf8'),
    correlation: fs.readFileSync(path.join(ROOT, 'src/components/trading/correlation-matrix.tsx'), 'utf8'),
    portfolio: fs.readFileSync(path.join(ROOT, 'src/components/trading/portfolio-card.tsx'), 'utf8'),
    watchlist: fs.readFileSync(path.join(ROOT, 'src/components/trading/watchlist-editor.tsx'), 'utf8'),
  };
  assert.match(sources.plan, /Canonical research planning is unavailable/);
  assert.match(sources.correlation, /Canonical correlation source is unavailable/);
  assert.match(sources.portfolio, /Stop mutation unavailable/);
  assert.match(sources.portfolio, /Canonical paper portfolio is unavailable/);
  assert.match(sources.watchlist, /Canonical watchlist is unavailable/);
  assert.doesNotMatch(Object.values(sources).join('\n'), /fetch\('\/api\/trading\/(?:plan|correlation|update-stop|watchlist)/);
});

test('risk page catches unavailable market source and still renders typed correlation state', () => {
  const source = fs.readFileSync(path.join(
    ROOT, 'src/app/dashboard/risk/page.tsx',
  ), 'utf8');
  assert.match(source, /Canonical risk source is unavailable/);
  assert.match(source, /catch/);
  assert.match(source, /<CorrelationMatrix \/>/);
});
