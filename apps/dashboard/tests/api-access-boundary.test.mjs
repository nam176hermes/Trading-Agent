import assert from 'node:assert/strict';
import fs from 'node:fs';
import { registerHooks } from 'node:module';
import test, { afterEach, beforeEach } from 'node:test';
import { fileURLToPath } from 'node:url';
import { NextRequest } from 'next/server.js';

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.startsWith('.') && context.parentURL?.includes('/src/')) {
      const typescriptUrl = new URL(specifier.replace(/\.js$/, '') + '.ts', context.parentURL);
      if (fs.existsSync(fileURLToPath(typescriptUrl))) {
        return { shortCircuit: true, url: typescriptUrl.href };
      }
    }
    return nextResolve(specifier, context);
  },
});

const SESSION_SECRET = Array.from({ length: 32 }, (_, index) => (index % 16).toString(16)).join('');
const ORIGINAL_SECRET = process.env.TRADING_DASHBOARD_SESSION_SECRET;
const ORIGINAL_NON_BROWSER_MODE = process.env.TRADING_DASHBOARD_NON_BROWSER_TEST_MODE;
const ORIGINAL_NODE_ENV = process.env.NODE_ENV;

beforeEach(() => {
  process.env.TRADING_DASHBOARD_SESSION_SECRET = SESSION_SECRET;
  delete process.env.TRADING_DASHBOARD_NON_BROWSER_TEST_MODE;
  if (ORIGINAL_NODE_ENV === undefined) delete process.env.NODE_ENV;
  else process.env.NODE_ENV = ORIGINAL_NODE_ENV;
});

afterEach(() => {
  if (ORIGINAL_SECRET === undefined) delete process.env.TRADING_DASHBOARD_SESSION_SECRET;
  else process.env.TRADING_DASHBOARD_SESSION_SECRET = ORIGINAL_SECRET;
  if (ORIGINAL_NON_BROWSER_MODE === undefined) delete process.env.TRADING_DASHBOARD_NON_BROWSER_TEST_MODE;
  else process.env.TRADING_DASHBOARD_NON_BROWSER_TEST_MODE = ORIGINAL_NON_BROWSER_MODE;
  if (ORIGINAL_NODE_ENV === undefined) delete process.env.NODE_ENV;
  else process.env.NODE_ENV = ORIGINAL_NODE_ENV;
});

async function boundaryRequest(pathname, {
  method = 'GET', role, token, origin, sameOriginMarker, fetchSite,
} = {}) {
  const [{ proxy }, { issueSession }] = await Promise.all([
    import('../src/proxy.ts'),
    import('../src/lib/trading/session.ts'),
  ]);
  const headers = new Headers();
  const sessionToken = token ?? (role ? issueSession(role) : null);
  if (sessionToken) headers.set('cookie', `trading_session=${sessionToken}`);
  if (origin) headers.set('origin', origin);
  if (sameOriginMarker) headers.set('x-trading-same-origin', sameOriginMarker);
  if (fetchSite) headers.set('sec-fetch-site', fetchSite);
  return proxy(new NextRequest(`https://dashboard.test${pathname}`, { method, headers }));
}

async function assertJsonError(response, status, code) {
  assert.equal(response.status, status);
  assert.deepEqual(await response.json(), {
    ok: false,
    code,
    message: status === 503
      ? 'Dashboard authentication is unavailable.'
      : status === 401
        ? 'Authentication required.'
        : 'Insufficient permissions.',
  });
}

test('proxy matcher covers exactly the trading API boundary', async () => {
  const { config } = await import('../src/proxy.ts');
  assert.deepEqual(config, { matcher: '/api/trading/:path*' });
});

test('rejects a missing or invalid signed session with structured 401 JSON', async () => {
  await assertJsonError(await boundaryRequest('/api/trading/status'), 401, 'UNAUTHORIZED');
  await assertJsonError(
    await boundaryRequest('/api/trading/status', { token: 'invalid.session' }),
    401,
    'UNAUTHORIZED',
  );
});

test('allows reader GET requests and denies reader mutations', async () => {
  const read = await boundaryRequest('/api/trading/status', { role: 'reader' });
  assert.equal(read.headers.get('x-middleware-next'), '1');

  await assertJsonError(
    await boundaryRequest('/api/trading/run', {
      method: 'POST',
      role: 'reader',
      origin: 'https://dashboard.test',
    }),
    403,
    'FORBIDDEN',
  );
});

test('operator can run the pipeline but cannot manage keys', async () => {
  const run = await boundaryRequest('/api/trading/run', {
    method: 'POST',
    role: 'operator',
    origin: 'https://dashboard.test',
  });
  assert.equal(run.headers.get('x-middleware-next'), '1');

  await assertJsonError(
    await boundaryRequest('/api/trading/keys', {
      method: 'POST',
      role: 'operator',
      origin: 'https://dashboard.test',
    }),
    403,
    'FORBIDDEN',
  );
});

test('job mutations require operator role and same-origin enforcement', async () => {
  await assertJsonError(
    await boundaryRequest('/api/trading/jobs', {
      method: 'POST', role: 'reader', origin: 'https://dashboard.test',
    }),
    403,
    'FORBIDDEN',
  );
  const create = await boundaryRequest('/api/trading/jobs', {
    method: 'POST', role: 'operator', origin: 'https://dashboard.test',
  });
  assert.equal(create.headers.get('x-middleware-next'), '1');

  const cancel = await boundaryRequest('/api/trading/jobs/job_123/cancel', {
    method: 'POST', role: 'operator', origin: 'https://dashboard.test',
  });
  assert.equal(cancel.headers.get('x-middleware-next'), '1');
});

test('admin can manage keys', async () => {
  const response = await boundaryRequest('/api/trading/keys', {
    method: 'POST',
    role: 'admin',
    origin: 'https://dashboard.test',
  });
  assert.equal(response.headers.get('x-middleware-next'), '1');
});

test('rejects absent and cross-origin mutation origins', async () => {
  for (const origin of [undefined, 'https://attacker.test']) {
    const response = await boundaryRequest('/api/trading/run', {
      method: 'POST',
      role: 'operator',
      origin,
    });
    assert.equal(response.status, 403);
    assert.deepEqual(await response.json(), {
      ok: false,
      code: 'FORBIDDEN',
      message: 'Same-origin request required.',
    });
  }
});

test('allows Chromium same-origin evidence when POST omits Origin', async () => {
  const browser = await boundaryRequest('/api/trading/kill-switch', {
    method: 'POST', role: 'admin', sameOriginMarker: '1',
  });
  assert.equal(browser.headers.get('x-middleware-next'), '1');

  const response = await boundaryRequest('/api/trading/kill-switch', {
    method: 'POST', role: 'admin', fetchSite: 'same-origin',
  });
  assert.equal(response.headers.get('x-middleware-next'), '1');

  for (const fetchSite of ['cross-site', 'same-site', 'none']) {
    const rejected = await boundaryRequest('/api/trading/kill-switch', {
      method: 'POST', role: 'admin', fetchSite,
    });
    assert.equal(rejected.status, 403);
  }

  const rejectedMarker = await boundaryRequest('/api/trading/kill-switch', {
    method: 'POST', role: 'admin', sameOriginMarker: '0',
  });
  assert.equal(rejectedMarker.status, 403);
});

test('rejects a mismatched origin even with the same-origin marker', async () => {
  const response = await boundaryRequest('/api/trading/kill-switch', {
    method: 'POST',
    role: 'admin',
    origin: 'https://attacker.test',
    sameOriginMarker: '1',
  });
  assert.equal(response.status, 403);
});

test('rejects a mismatched origin even in explicit non-browser test mode', async () => {
  process.env.TRADING_DASHBOARD_NON_BROWSER_TEST_MODE = '1';
  process.env.NODE_ENV = 'test';
  const response = await boundaryRequest('/api/trading/run', {
    method: 'POST',
    role: 'operator',
    origin: 'https://attacker.test',
  });
  assert.equal(response.status, 403);
});

test('rejects an absent origin when non-browser mode is enabled in production', async () => {
  process.env.TRADING_DASHBOARD_NON_BROWSER_TEST_MODE = '1';
  process.env.NODE_ENV = 'production';
  const response = await boundaryRequest('/api/trading/run', {
    method: 'POST',
    role: 'operator',
  });
  assert.equal(response.status, 403);
});

test('allows an absent origin only when non-browser mode is enabled in test environment', async () => {
  process.env.TRADING_DASHBOARD_NON_BROWSER_TEST_MODE = '1';
  process.env.NODE_ENV = 'test';
  const response = await boundaryRequest('/api/trading/run', {
    method: 'POST',
    role: 'operator',
  });
  assert.equal(response.headers.get('x-middleware-next'), '1');
});

test('fails closed with structured 503 JSON when session verification is unavailable', async () => {
  process.env.TRADING_DASHBOARD_SESSION_SECRET = 'too-short';
  await assertJsonError(
    await boundaryRequest('/api/trading/status', { token: 'anything' }),
    503,
    'CONFIGURATION_ERROR',
  );
});
