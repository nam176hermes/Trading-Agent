import assert from 'node:assert/strict';
import { createHmac } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import test, { afterEach } from 'node:test';
import { fileURLToPath } from 'node:url';

const SESSION_SECRET = Array.from({ length: 32 }, (_, index) => (index % 16).toString(16)).join('');
const BASE64URL_ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_';
const ORIGINAL_SECRET = process.env.TRADING_DASHBOARD_SESSION_SECRET;
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

function restoreSecret() {
  if (ORIGINAL_SECRET === undefined) delete process.env.TRADING_DASHBOARD_SESSION_SECRET;
  else process.env.TRADING_DASHBOARD_SESSION_SECRET = ORIGINAL_SECRET;
}

afterEach(restoreSecret);

function decodePayload(token) {
  return JSON.parse(Buffer.from(token.split('.')[0], 'base64url').toString('utf8'));
}

function signPayload(payload, secret = SESSION_SECRET) {
  const encoded = Buffer.from(JSON.stringify(payload)).toString('base64url');
  return signEncodedPayload(encoded, secret);
}

function signEncodedPayload(encoded, secret = SESSION_SECRET) {
  const signature = createHmac('sha256', secret).update(encoded).digest('base64url');
  return `${encoded}.${signature}`;
}

function makeTrailingBitsNoncanonical(encoded) {
  if (encoded.length % 4 !== 2 && encoded.length % 4 !== 3) {
    throw new Error('fixture must have unused trailing bits');
  }
  const lastIndex = BASE64URL_ALPHABET.indexOf(encoded.at(-1));
  return `${encoded.slice(0, -1)}${BASE64URL_ALPHABET[lastIndex + 1]}`;
}

test('issues and verifies an eight-hour versioned session without embedding secrets', async () => {
  process.env.TRADING_DASHBOARD_SESSION_SECRET = SESSION_SECRET;
  const { issueSession, verifySession } = await import('../src/lib/trading/session.ts');
  const now = 1_700_000_000_000;

  const token = issueSession('operator', now);
  const payload = decodePayload(token);

  assert.deepEqual(payload, {
    v: 1,
    role: 'operator',
    iat: 1_700_000_000,
    exp: 1_700_028_800,
  });
  assert.deepEqual(verifySession(token, now), payload);
  assert.doesNotMatch(token, /password/i);
  assert.ok(!token.includes(SESSION_SECRET));
});

test('rejects a tampered token', async () => {
  process.env.TRADING_DASHBOARD_SESSION_SECRET = SESSION_SECRET;
  const { issueSession, verifySession } = await import('../src/lib/trading/session.ts');
  const now = 1_700_000_000_000;
  const token = issueSession('reader', now);
  const [payload, signature] = token.split('.');
  const tamperedPayload = Buffer.from(
    JSON.stringify({ ...decodePayload(token), role: 'admin' }),
  ).toString('base64url');

  assert.equal(verifySession(`${tamperedPayload}.${signature}`, now), null);
  assert.equal(verifySession(`${payload}.${signature.slice(0, -1)}A`, now), null);
});

test('rejects expired sessions', async () => {
  process.env.TRADING_DASHBOARD_SESSION_SECRET = SESSION_SECRET;
  const { issueSession, verifySession } = await import('../src/lib/trading/session.ts');
  const issuedAt = 1_700_000_000_000;
  const token = issueSession('reader', issuedAt);

  assert.ok(verifySession(token, issuedAt + 8 * 60 * 60 * 1000 - 1));
  assert.equal(verifySession(token, issuedAt + 8 * 60 * 60 * 1000), null);
});

test('rejects malformed or invalid signed payloads', async () => {
  process.env.TRADING_DASHBOARD_SESSION_SECRET = SESSION_SECRET;
  const { verifySession } = await import('../src/lib/trading/session.ts');
  const now = 1_700_000_000_000;

  const invalidPayloads = [
    'not-a-token',
    signPayload({ v: 1, role: 'owner', iat: 1_700_000_000, exp: 1_700_028_800 }),
    signPayload({ v: 2, role: 'reader', iat: 1_700_000_000, exp: 1_700_028_800 }),
    signPayload({ v: 1, role: 'reader', iat: 'now', exp: 1_700_028_800 }),
    signPayload({ v: 1, role: 'reader', iat: -1, exp: 28_799 }),
    signPayload({ v: 1, role: 'reader', iat: 0, exp: -1 }),
    signPayload({
      v: 1,
      role: 'reader',
      iat: Number.MAX_SAFE_INTEGER + 1,
      exp: Number.MAX_SAFE_INTEGER + 1 + 28_800,
    }),
    signPayload({
      v: 1,
      role: 'reader',
      iat: Number.MAX_SAFE_INTEGER - 1_000,
      exp: Number.MAX_SAFE_INTEGER - 1_000 + 28_800,
    }),
    signPayload({ v: 1, role: 'reader', iat: 1_700_000_000, exp: 1_700_000_001 }),
    signPayload({ v: 1, role: 'reader', iat: 1_700_000_001, exp: 1_700_028_801 }),
    signPayload({ v: 1, role: 'reader', iat: 1_700_000_000, exp: 1_700_028_800, extra: true }),
  ];

  for (const token of invalidPayloads) assert.equal(verifySession(token, now), null);
});

test('rejects negative and unsafe integer timestamps even when otherwise valid', async () => {
  process.env.TRADING_DASHBOARD_SESSION_SECRET = SESSION_SECRET;
  const { verifySession } = await import('../src/lib/trading/session.ts');
  const unsafeIat = Number.MAX_SAFE_INTEGER + 1;
  const unsafeExpIat = Number.MAX_SAFE_INTEGER - 1_000;

  assert.equal(verifySession(
    signPayload({ v: 1, role: 'reader', iat: -1, exp: 28_799 }),
    0,
  ), null);
  assert.throws(() => verifySession(
    signPayload({ v: 1, role: 'reader', iat: unsafeIat, exp: unsafeIat + 28_800 }),
    unsafeIat * 1_000,
  ), TypeError);
  assert.throws(() => verifySession(
    signPayload({ v: 1, role: 'reader', iat: unsafeExpIat, exp: unsafeExpIat + 28_800 }),
    unsafeExpIat * 1_000,
  ), TypeError);
});

test('issuer and verifier reject finite now values that cannot form safe session timestamps', async () => {
  process.env.TRADING_DASHBOARD_SESSION_SECRET = SESSION_SECRET;
  const { issueSession, verifySession } = await import('../src/lib/trading/session.ts');
  const unsafeConvertedSeconds = (Number.MAX_SAFE_INTEGER + 1) * 1_000;
  const unsafeEightHourExpiry = (Number.MAX_SAFE_INTEGER - 10_000) * 1_000;

  for (const now of [unsafeConvertedSeconds, unsafeEightHourExpiry]) {
    assert.throws(() => issueSession('reader', now), TypeError);
    assert.throws(() => verifySession('not-a-token', now), TypeError);
  }
});

test('rejects noncanonical base64url token segments', async () => {
  process.env.TRADING_DASHBOARD_SESSION_SECRET = SESSION_SECRET;
  const { issueSession, verifySession } = await import('../src/lib/trading/session.ts');
  const now = 1_700_000_000_000;
  const token = issueSession('reader', now);
  const [payload, signature] = token.split('.');
  const paddedPayload = `${payload}==`;
  const paddedSignature = `${signature}=`;
  const standardBase64Signature = signature.replaceAll('-', '+').replaceAll('_', '/');
  const whitespacePayload = Buffer.from(`${JSON.stringify(decodePayload(token))} `).toString('base64url');
  const trailingBitsPayload = makeTrailingBitsNoncanonical(whitespacePayload);
  const trailingBitsSignature = makeTrailingBitsNoncanonical(signature);

  assert.notEqual(standardBase64Signature, signature, 'fixture must exercise + or /');
  assert.equal(verifySession(signEncodedPayload(paddedPayload), now), null);
  assert.equal(verifySession(`${payload}.${paddedSignature}`, now), null);
  assert.equal(verifySession(`${payload}.${standardBase64Signature}`, now), null);
  assert.deepEqual(
    Buffer.from(trailingBitsPayload, 'base64url'),
    Buffer.from(whitespacePayload, 'base64url'),
  );
  assert.equal(verifySession(signEncodedPayload(trailingBitsPayload), now), null);
  assert.deepEqual(
    Buffer.from(trailingBitsSignature, 'base64url'),
    Buffer.from(signature, 'base64url'),
  );
  assert.equal(verifySession(`${payload}.${trailingBitsSignature}`, now), null);
});

test('fails closed when the session signing secret is missing or shorter than 32 characters', async () => {
  const { issueSession, verifySession } = await import('../src/lib/trading/session.ts');
  delete process.env.TRADING_DASHBOARD_SESSION_SECRET;

  assert.throws(() => issueSession('reader'), { code: 'CONFIGURATION_ERROR' });
  assert.throws(() => verifySession('anything'), { code: 'CONFIGURATION_ERROR' });

  process.env.TRADING_DASHBOARD_SESSION_SECRET = 'too-short';
  assert.throws(() => issueSession('reader'), { code: 'CONFIGURATION_ERROR' });
  assert.throws(() => verifySession('anything'), { code: 'CONFIGURATION_ERROR' });
});

test('maps trading routes and methods to the required role', async () => {
  const { requiredRole } = await import('../src/lib/trading/access-policy.ts');
  const cases = [
    ['/api/trading/overview', 'GET', 'reader'],
    ['/api/trading/overview', 'HEAD', 'reader'],
    ['/api/trading/keys', 'GET', 'admin'],
    ['/api/trading/keys', 'DELETE', 'admin'],
    ['/api/trading/keys/coinbase', 'HEAD', 'admin'],
    ['/api/trading/service', 'POST', 'admin'],
    ['/api/trading/mode', 'POST', 'admin'],
    ['/api/trading/kill-switch', 'POST', 'admin'],
    ['/api/trading/run', 'POST', 'operator'],
    ['/api/trading/close-position', 'POST', 'operator'],
    ['/api/trading/update-stop', 'POST', 'operator'],
    ['/api/trading/plan', 'POST', 'operator'],
    ['/api/trading/watchlist', 'POST', 'operator'],
    ['/api/trading/watchlist', 'PUT', 'admin'],
    ['/api/trading/unclassified', 'POST', 'admin'],
  ];

  for (const [pathname, method, role] of cases) {
    assert.equal(requiredRole(pathname, method), role, `${method} ${pathname}`);
  }
});

test('orders roles so admin satisfies every role and reader cannot mutate', async () => {
  const { roleSatisfies } = await import('../src/lib/trading/access-policy.ts');

  assert.equal(roleSatisfies('admin', 'admin'), true);
  assert.equal(roleSatisfies('admin', 'operator'), true);
  assert.equal(roleSatisfies('admin', 'reader'), true);
  assert.equal(roleSatisfies('operator', 'operator'), true);
  assert.equal(roleSatisfies('operator', 'reader'), true);
  assert.equal(roleSatisfies('operator', 'admin'), false);
  assert.equal(roleSatisfies('reader', 'reader'), true);
  assert.equal(roleSatisfies('reader', 'operator'), false);
  assert.equal(roleSatisfies('reader', 'admin'), false);
});

test('authenticates admin, then operator, then legacy reader passwords', async () => {
  const { authenticatePassword } = await import('../src/lib/trading/access-policy.ts');
  const env = {
    TRADING_DASHBOARD_ADMIN_PASSWORD: 'admin-password',
    TRADING_DASHBOARD_OPERATOR_PASSWORD: 'operator-password',
    TRADING_DASHBOARD_PASSWORD: 'reader-password',
  };

  assert.deepEqual(authenticatePassword('admin-password', env), { ok: true, role: 'admin' });
  assert.deepEqual(authenticatePassword('operator-password', env), { ok: true, role: 'operator' });
  assert.deepEqual(authenticatePassword('reader-password', env), { ok: true, role: 'reader' });
  assert.deepEqual(authenticatePassword('wrong-password', env), { ok: false, code: 'UNAUTHORIZED' });
});

test('rejects missing and duplicate password configuration without collapsing roles', async () => {
  const { authenticatePassword } = await import('../src/lib/trading/access-policy.ts');

  assert.deepEqual(authenticatePassword('anything', {}), {
    ok: false,
    code: 'CONFIGURATION_ERROR',
  });

  for (const env of [
    {
      TRADING_DASHBOARD_ADMIN_PASSWORD: 'same-password',
      TRADING_DASHBOARD_OPERATOR_PASSWORD: 'same-password',
    },
    {
      TRADING_DASHBOARD_ADMIN_PASSWORD: 'same-password',
      TRADING_DASHBOARD_PASSWORD: 'same-password',
    },
    {
      TRADING_DASHBOARD_OPERATOR_PASSWORD: 'same-password',
      TRADING_DASHBOARD_PASSWORD: 'same-password',
    },
  ]) {
    assert.deepEqual(authenticatePassword('same-password', env), {
      ok: false,
      code: 'CONFIGURATION_ERROR',
    });
  }
});

test('password matching uses the constant-time crypto primitive', () => {
  const source = fs.readFileSync(path.join(ROOT, 'src/lib/trading/access-policy.ts'), 'utf8');
  assert.match(source, /timingSafeEqual/);
  assert.doesNotMatch(source, /provided\s*===\s*configured|configured\s*===\s*provided/);
});
