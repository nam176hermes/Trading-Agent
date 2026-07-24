import assert from 'node:assert/strict';
import fs from 'node:fs';
import { registerHooks } from 'node:module';
import test, { afterEach } from 'node:test';
import { fileURLToPath } from 'node:url';

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === 'server-only') {
      return { shortCircuit: true, url: 'data:text/javascript,export%20{}' };
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

const SESSION_SECRET = Array.from({ length: 32 }, (_, index) => (index % 16).toString(16)).join('');
const ORIGINAL_ENV = {
  NODE_ENV: process.env.NODE_ENV,
  TRADING_DASHBOARD_ADMIN_PASSWORD: process.env.TRADING_DASHBOARD_ADMIN_PASSWORD,
  TRADING_DASHBOARD_OPERATOR_PASSWORD: process.env.TRADING_DASHBOARD_OPERATOR_PASSWORD,
  TRADING_DASHBOARD_PASSWORD: process.env.TRADING_DASHBOARD_PASSWORD,
  TRADING_DASHBOARD_SESSION_SECRET: process.env.TRADING_DASHBOARD_SESSION_SECRET,
  TRADING_DASHBOARD_TRUSTED_PROXY_SECRET: process.env.TRADING_DASHBOARD_TRUSTED_PROXY_SECRET,
};

function restoreEnvironment() {
  for (const [name, value] of Object.entries(ORIGINAL_ENV)) {
    if (value === undefined) delete process.env[name];
    else process.env[name] = value;
  }
}

afterEach(restoreEnvironment);

test('blocks after five failures in fifteen minutes and reports Retry-After seconds', async () => {
  const { checkLoginAttempt, recordLoginFailure } = await import('../src/lib/trading/login-rate-limit.ts');
  const key = `five-failures-${Date.now()}`;
  const startedAt = 1_700_000_000_000;

  for (let attempt = 0; attempt < 5; attempt += 1) {
    assert.deepEqual(checkLoginAttempt(key, startedAt + attempt), { allowed: true });
    recordLoginFailure(key, startedAt + attempt);
  }

  assert.deepEqual(checkLoginAttempt(key, startedAt + 60_001), {
    allowed: false,
    retryAfter: 840,
  });
});

test('a rate-limit failure window expires after fifteen minutes', async () => {
  const { checkLoginAttempt, recordLoginFailure } = await import('../src/lib/trading/login-rate-limit.ts');
  const expiryKey = `expiry-${Date.now()}`;
  const startedAt = 1_710_000_000_000;

  for (let attempt = 0; attempt < 5; attempt += 1) {
    recordLoginFailure(expiryKey, startedAt + attempt);
  }
  assert.deepEqual(checkLoginAttempt(expiryKey, startedAt + 15 * 60 * 1000), { allowed: true });
});

test('valid reader logins cannot erase elevated-password failures for the same IP', async () => {
  process.env.TRADING_DASHBOARD_ADMIN_PASSWORD = 'admin-password';
  process.env.TRADING_DASHBOARD_PASSWORD = 'reader-password';
  process.env.TRADING_DASHBOARD_SESSION_SECRET = SESSION_SECRET;
  process.env.TRADING_DASHBOARD_TRUSTED_PROXY_SECRET = 'test-trusted-proxy-secret';
  const route = await import('../src/app/api/auth/session/route.ts');
  const url = 'https://dashboard.test/api/auth/session';
  const headers = {
    'content-type': 'application/json',
    'cf-connecting-ip': `mixed-role-${Date.now()}`,
    'x-trusted-proxy-secret': 'test-trusted-proxy-secret',
  };

  const login = (password) => route.POST(new Request(url, {
    method: 'POST', headers, body: JSON.stringify({ password }),
  }));

  for (let attempt = 0; attempt < 4; attempt += 1) {
    assert.equal((await login('wrong-admin-guess')).status, 401);
  }
  assert.equal((await login('reader-password')).status, 200);

  assert.equal((await login('wrong-admin-guess')).status, 401);
  const blockedBeforeComparison = await route.POST({
    headers: new Headers(headers),
    async json() {
      assert.fail('rate-limited mixed-role requests must not parse or compare credentials');
    },
  });
  assert.equal(blockedBeforeComparison.status, 429);
  assert.match(blockedBeforeComparison.headers.get('retry-after') ?? '', /^\d+$/);
});

test('keeps the in-memory limiter bounded when many keys are recorded', async () => {
  const { checkLoginAttempt, recordLoginFailure } = await import('../src/lib/trading/login-rate-limit.ts');
  const startedAt = 1_720_000_000_000;
  const firstKey = `bounded-first-${Date.now()}`;

  for (let failure = 0; failure < 5; failure += 1) recordLoginFailure(firstKey, startedAt);
  for (let key = 0; key < 1_100; key += 1) {
    recordLoginFailure(`bounded-${Date.now()}-${key}`, startedAt + key + 1);
  }

  assert.deepEqual(checkLoginAttempt(firstKey, startedAt + 2_000), { allowed: true });
});

test('session route sets, reports, and clears an HttpOnly strict eight-hour cookie', async () => {
  process.env.NODE_ENV = 'production';
  process.env.TRADING_DASHBOARD_OPERATOR_PASSWORD = 'operator-password';
  process.env.TRADING_DASHBOARD_SESSION_SECRET = SESSION_SECRET;
  process.env.TRADING_DASHBOARD_TRUSTED_PROXY_SECRET = 'test-trusted-proxy-secret';
  const route = await import('../src/app/api/auth/session/route.ts');

  const login = await route.POST(new Request('https://dashboard.test/api/auth/session', {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      'cf-connecting-ip': '203.0.113.10',
      'x-trusted-proxy-secret': 'test-trusted-proxy-secret',
    },
    body: JSON.stringify({ password: 'operator-password' }),
  }));
  assert.equal(login.status, 200);
  assert.deepEqual(await login.json(), { authenticated: true });
  const setCookie = login.headers.get('set-cookie');
  assert.ok(setCookie);
  assert.match(setCookie, /^trading_session=/);
  assert.match(setCookie, /HttpOnly/i);
  assert.match(setCookie, /Secure/i);
  assert.match(setCookie, /SameSite=Strict/i);
  assert.match(setCookie, /Path=\//i);
  assert.match(setCookie, /Max-Age=28800/i);

  const cookie = setCookie.split(';', 1)[0];
  const status = await route.GET(new Request('https://dashboard.test/api/auth/session', {
    headers: { cookie },
  }));
  assert.deepEqual(await status.json(), { authenticated: true, role: 'operator' });

  const logout = await route.DELETE();
  assert.equal(logout.status, 200);
  assert.deepEqual(await logout.json(), { authenticated: false });
  assert.match(logout.headers.get('set-cookie') ?? '', /^trading_session=;/);
  assert.match(logout.headers.get('set-cookie') ?? '', /Max-Age=0/i);
});

test('production login fails closed until trusted proxy attribution is configured', async () => {
  process.env.NODE_ENV = 'production';
  process.env.TRADING_DASHBOARD_ADMIN_PASSWORD = 'admin-password';
  process.env.TRADING_DASHBOARD_SESSION_SECRET = SESSION_SECRET;
  delete process.env.TRADING_DASHBOARD_TRUSTED_PROXY_SECRET;
  const route = await import('../src/app/api/auth/session/route.ts');
  const url = 'https://dashboard.test/api/auth/session';
  const headers = {
    'content-type': 'application/json',
    'cf-connecting-ip': '198.51.100.7',
    'x-forwarded-for': '192.0.2.1, 192.0.2.2',
  };

  for (let attempt = 0; attempt < 6; attempt += 1) {
    const response = await route.POST(new Request(url, {
      method: 'POST',
      headers: { ...headers, 'cf-connecting-ip': `198.51.100.${attempt + 1}` },
      body: JSON.stringify({ password: 'wrong' }),
    }));
    assert.equal(response.status, 503);
    assert.deepEqual(await response.json(), { authenticated: false });
  }

  const forwardedOnly = await route.POST(new Request(url, {
    method: 'POST',
    headers: { ...headers, 'cf-connecting-ip': '198.51.100.8' },
    body: JSON.stringify({ password: 'admin-password' }),
  }));
  assert.equal(forwardedOnly.status, 503);

  const noCookie = await route.GET(new Request(url));
  assert.deepEqual(await noCookie.json(), { authenticated: false });
});

test('concurrent login requests cannot pass more than five credential checks per window', async () => {
  process.env.TRADING_DASHBOARD_PASSWORD = 'reader-password';
  process.env.TRADING_DASHBOARD_SESSION_SECRET = SESSION_SECRET;
  process.env.TRADING_DASHBOARD_TRUSTED_PROXY_SECRET = 'test-trusted-proxy-secret';
  const route = await import('../src/app/api/auth/session/route.ts');
  const headers = new Headers({
    'content-type': 'application/json',
    'cf-connecting-ip': '198.51.100.222',
    'x-trusted-proxy-secret': 'test-trusted-proxy-secret',
  });
  let releaseBodies;
  const bodyBarrier = new Promise((resolve) => { releaseBodies = resolve; });
  let bodiesAwaitingRelease = 0;
  let allBodiesStartedResolve;
  const allBodiesStarted = new Promise((resolve) => { allBodiesStartedResolve = resolve; });

  const requests = Array.from({ length: 10 }, () => {
    let started = false;
    return new Request('https://dashboard.test/api/auth/session', {
      method: 'POST',
      headers,
      body: new ReadableStream({
        async pull(controller) {
          if (started) return;
          started = true;
          bodiesAwaitingRelease += 1;
          if (bodiesAwaitingRelease === 10) allBodiesStartedResolve();
          await bodyBarrier;
          controller.enqueue(new TextEncoder().encode('{"password":"wrong"}'));
          controller.close();
        },
      }),
      duplex: 'half',
    });
  });

  const responsesPromise = Promise.all(requests.map((request) => route.POST(request)));
  await allBodiesStarted;
  releaseBodies();
  const responses = await responsesPromise;
  const statuses = responses.map((response) => response.status);

  assert.equal(statuses.filter((status) => status === 401).length, 5);
  assert.equal(statuses.filter((status) => status === 429).length, 5);
});
