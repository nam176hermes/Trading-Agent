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

test('mutation DTOs reject extras, Infinity, and unsafe symbols while valid requests preserve state', async () => {
  const updateStop = await import('../src/app/api/trading/update-stop/route.ts');
  const plan = await import('../src/app/api/trading/plan/route.ts');
  const mode = await import('../src/app/api/trading/mode/route.ts');
  const killSwitch = await import('../src/app/api/trading/kill-switch/route.ts');
  const watchlist = await import('../src/app/api/trading/watchlist/route.ts');

  const stopsDir = path.join(dataRoot, 'memory');
  fs.mkdirSync(stopsDir, { recursive: true });
  fs.chmodSync(stopsDir, 0o700);
  fs.writeFileSync(path.join(stopsDir, 'trailing_stops.json'), JSON.stringify({
    BTC: { stop: 90, highest_price: 120, risk_note: 'synthetic' },
  }));

  for (const body of [
    { symbol: 'BTC', stopLoss: 1e309 },
    { symbol: '__proto__', stopLoss: 100 },
    { symbol: 'BTC', stopLoss: 100, extra: true },
  ]) {
    const response = await updateStop.POST(await authorizedRequest('https://dashboard.test/api/trading/update-stop', 'operator', body));
    assert.equal(response.status, 400);
  }
  const validStop = await updateStop.POST(await authorizedRequest(
    'https://dashboard.test/api/trading/update-stop', 'operator', { symbol: 'btc', stopLoss: 100 },
  ));
  assert.equal(validStop.status, 200);
  const stops = JSON.parse(fs.readFileSync(path.join(stopsDir, 'trailing_stops.json'), 'utf8'));
  assert.deepEqual(stops.BTC, { stop: 100, highest_price: 120, risk_note: 'synthetic', manual: true });

  assert.equal((await plan.POST(await authorizedRequest('https://dashboard.test/api/trading/plan', 'operator', {
    query: 'synthetic query', keywords: ['Risk'], extra: true,
  }))).status, 400);
  assert.equal((await plan.POST(await authorizedRequest('https://dashboard.test/api/trading/plan', 'operator', {
    query: 'synthetic query', keywords: ['Risk'],
  }))).status, 200);

  assert.equal((await mode.POST(await authorizedRequest('https://dashboard.test/api/trading/mode', 'admin', {
    mode: 'paper', extra: true,
  }))).status, 400);
  assert.equal((await mode.POST(await authorizedRequest('https://dashboard.test/api/trading/mode', 'admin', {
    mode: 'paper',
  }))).status, 200);

  assert.equal((await killSwitch.POST(await authorizedRequest('https://dashboard.test/api/trading/kill-switch', 'admin', {
    action: 'off', reason: 'not allowed for off',
  }))).status, 400);
  assert.equal((await killSwitch.POST(await authorizedRequest('https://dashboard.test/api/trading/kill-switch', 'admin', {
    action: 'on', reason: 'synthetic safety drill',
  }))).status, 200);

  assert.equal((await watchlist.POST(await authorizedRequest('https://dashboard.test/api/trading/watchlist', 'operator', {
    action: 'add', symbol: 'constructor', extra: true,
  }))).status, 400);
  const validWatchlist = await watchlist.POST(await authorizedRequest('https://dashboard.test/api/trading/watchlist', 'operator', {
    action: 'add', symbol: 'btc',
  }));
  assert.equal(validWatchlist.status, 200);
  assert.equal((await validWatchlist.json()).symbols.includes('BTC'), true);
});

test('update-stop preserves corrupted risk state and fails closed', async () => {
  const updateStop = await import('../src/app/api/trading/update-stop/route.ts');
  const stopsDir = path.join(dataRoot, 'memory');
  const stopsPath = path.join(stopsDir, 'trailing_stops.json');
  fs.mkdirSync(stopsDir, { recursive: true });
  const corrupted = '{"BTC":{"stop":90}';
  fs.writeFileSync(stopsPath, corrupted, { mode: 0o600 });

  const response = await updateStop.POST(await authorizedRequest(
    'https://dashboard.test/api/trading/update-stop', 'operator', { symbol: 'BTC', stopLoss: 100 },
  ));

  assert.equal(response.status, 503);
  assert.equal(fs.readFileSync(stopsPath, 'utf8'), corrupted);
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

test('local state updater preserves existing evidence when validation fails', async () => {
  const { updatePrivateLocalStateFile } = await import('../src/lib/trading/local-state.ts');
  const stateDir = path.join(dataRoot, 'validated-state');
  const target = path.join(stateDir, 'state.txt');
  const malformed = 'malformed-evidence\n';
  fs.mkdirSync(stateDir, { recursive: true, mode: 0o700 });
  fs.writeFileSync(target, malformed, { mode: 0o600 });

  assert.throws(
    () => updatePrivateLocalStateFile(target, 1024, () => {
      throw new Error('persisted schema rejected');
    }),
    /persisted schema rejected/,
  );
  assert.equal(fs.readFileSync(target, 'utf8'), malformed);
  assert.deepEqual(fs.readdirSync(stateDir), ['state.txt']);
});

test('local state updater creates missing private state after validating absence', async () => {
  const { updatePrivateLocalStateFile } = await import('../src/lib/trading/local-state.ts');
  const stateDir = path.join(dataRoot, 'new-state', 'nested');
  const target = path.join(stateDir, 'state.txt');

  updatePrivateLocalStateFile(target, 1024, (existing) => {
    assert.equal(existing, null);
    return 'paper\n';
  });

  assert.equal(fs.readFileSync(target, 'utf8'), 'paper\n');
  assert.equal(fs.statSync(stateDir).mode & 0o777, 0o700);
  assert.equal(fs.statSync(target).mode & 0o777, 0o600);
});

test('mode route rejects malformed persisted state without deleting evidence', async () => {
  const mode = await import('../src/app/api/trading/mode/route.ts');
  const target = path.join(dataRoot, '.mode');
  const malformed = 'paper\nunknown\n';
  fs.writeFileSync(target, malformed, { mode: 0o600 });

  const response = await mode.POST(await authorizedRequest(
    'https://dashboard.test/api/trading/mode', 'admin', { mode: 'paper' },
  ));

  assert.equal(response.status, 503);
  assert.deepEqual(await response.json(), { ok: false, code: 'MODE_STATE_UNAVAILABLE' });
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

test('local state removal rejects an attacker-writable parent and preserves the file', async () => {
  const { removePrivateLocalStateFile } = await import('../src/lib/trading/local-state.ts');
  const unsafeDirectory = path.join(dataRoot, 'unsafe-removal');
  const target = path.join(unsafeDirectory, 'state.json');
  fs.mkdirSync(unsafeDirectory, { mode: 0o777 });
  fs.chmodSync(unsafeDirectory, 0o777);
  fs.writeFileSync(target, '{"preserve":true}', { mode: 0o600 });

  assert.throws(
    () => removePrivateLocalStateFile(target),
    /safe state directory|writable by another principal/i,
  );
  assert.equal(fs.readFileSync(target, 'utf8'), '{"preserve":true}');
});

test('watchlist refuses malformed persisted symbols without overwriting the unsafe state', async () => {
  const watchlist = await import('../src/app/api/trading/watchlist/route.ts');
  const stateDir = path.join(dataRoot, 'memory');
  const target = path.join(stateDir, 'watchlist.json');
  const unsafe = JSON.stringify({ symbols: ['btc', 'BTC'] });
  fs.mkdirSync(stateDir, { recursive: true, mode: 0o700 });
  fs.writeFileSync(target, unsafe, { mode: 0o600 });

  const response = await watchlist.POST(await authorizedRequest(
    'https://dashboard.test/api/trading/watchlist', 'operator', { action: 'add', symbol: 'ETH' },
  ));

  assert.equal(response.status, 503);
  assert.equal(fs.readFileSync(target, 'utf8'), unsafe);
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

test('all JSON routes use the shared bounded reader instead of request.json', () => {
  const routes = [
    'src/app/api/auth/session/route.ts',
    'src/app/api/trading/update-stop/route.ts',
    'src/app/api/trading/plan/route.ts',
    'src/app/api/trading/mode/route.ts',
    'src/app/api/trading/kill-switch/route.ts',
    'src/app/api/trading/watchlist/route.ts',
  ];
  for (const route of routes) {
    const source = fs.readFileSync(path.join(ROOT, route), 'utf8');
    assert.doesNotMatch(source, /request\.json\(/, route);
    assert.match(source, /request-body/, route);
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

test('watchlist rejects additions at the persisted symbol limit without corrupting state', async () => {
  const watchlist = await import('../src/app/api/trading/watchlist/route.ts');
  const stateDir = path.join(dataRoot, 'memory');
  const target = path.join(stateDir, 'watchlist.json');
  const symbols = Array.from({ length: 64 }, (_, index) =>
    String.fromCharCode(65 + Math.floor(index / 26), 65 + (index % 26)));
  const original = JSON.stringify({ symbols });
  fs.mkdirSync(stateDir, { recursive: true, mode: 0o700 });
  fs.writeFileSync(target, original, { mode: 0o600 });

  const response = await watchlist.POST(await authorizedRequest(
    'https://dashboard.test/api/trading/watchlist', 'operator', { action: 'add', symbol: 'ZZZZZ' },
  ));

  assert.equal(response.status, 409);
  assert.equal(fs.readFileSync(target, 'utf8'), original);
});

test('halt banner uses the strict kill-switch-off request contract', () => {
  const source = fs.readFileSync(path.join(
    ROOT, 'src/components/trading/halt-banner.tsx',
  ), 'utf8');
  assert.match(source, /JSON\.stringify\(\{ action: 'off' \}\)/);
  assert.doesNotMatch(source, /action: 'off', reason:/);
});
