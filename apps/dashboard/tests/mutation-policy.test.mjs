import assert from 'node:assert/strict';
import fs from 'node:fs';
import { registerHooks } from 'node:module';
import os from 'node:os';
import path from 'node:path';
import test, { afterEach, beforeEach } from 'node:test';
import { fileURLToPath } from 'node:url';

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === 'server-only') {
      return { shortCircuit: true, url: 'data:text/javascript,export%20{}' };
    }
    if (specifier === 'next/server') {
      return {
        shortCircuit: true,
        url: new URL('../node_modules/next/server.js', import.meta.url).href,
      };
    }
    if (specifier.startsWith('@/')) {
      const target = new URL(`../src/${specifier.slice(2)}`, import.meta.url);
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
const mutationRoutes = new Map([
  ['src/app/api/trading/close-position/route.ts', 'operator'],
  ['src/app/api/trading/jobs/route.ts', 'operator'],
  ['src/app/api/trading/jobs/[id]/cancel/route.ts', 'operator'],
  ['src/app/api/trading/keys/route.ts', 'admin'],
  ['src/app/api/trading/kill-switch/route.ts', 'admin'],
  ['src/app/api/trading/mode/route.ts', 'admin'],
  ['src/app/api/trading/plan/route.ts', 'operator'],
  ['src/app/api/trading/run/route.ts', 'operator'],
  ['src/app/api/trading/service/route.ts', 'admin'],
  ['src/app/api/trading/update-stop/route.ts', 'operator'],
  ['src/app/api/trading/watchlist/route.ts', 'operator'],
]);
const SESSION_SECRET = Array.from({ length: 32 }, (_, index) => (index % 16).toString(16)).join('');
const ORIGINAL_ENV = {
  TRADING_DATA_ROOT: process.env.TRADING_DATA_ROOT,
  TRADING_DASHBOARD_SESSION_SECRET: process.env.TRADING_DASHBOARD_SESSION_SECRET,
};
const TEST_TEMP_DIRECTORY = process.platform === 'linux' ? '/tmp' : os.tmpdir();
let researchDirectory;

beforeEach(() => {
  researchDirectory = fs.mkdtempSync(path.join(TEST_TEMP_DIRECTORY, 'mutation-policy-'));
  process.env.TRADING_DATA_ROOT = researchDirectory;
  process.env.TRADING_DASHBOARD_SESSION_SECRET = SESSION_SECRET;
});

afterEach(() => {
  fs.rmSync(researchDirectory, { force: true, recursive: true });
  for (const [name, value] of Object.entries(ORIGINAL_ENV)) {
    if (value === undefined) delete process.env[name];
    else process.env[name] = value;
  }
});

function requestWithSession(token) {
  return new Request('https://dashboard.test/api/trading/run', {
    method: 'POST',
    headers: token ? { cookie: `trading_session=${token}` } : undefined,
  });
}

function auditEvents() {
  const auditPath = path.join(researchDirectory, 'memory', 'dashboard_mutation_audit.jsonl');
  if (!fs.existsSync(auditPath)) return [];
  return fs.readFileSync(auditPath, 'utf8').trim().split('\n').map(JSON.parse);
}

test('every mutation route passes its explicit required role to the shared policy', () => {
  for (const [relative, role] of mutationRoutes) {
    const source = fs.readFileSync(path.join(root, relative), 'utf8');
    assert.match(
      source,
      new RegExp(`authorizeMutation\\([\\s\\S]{0,180}['\"]${role}['\"]\\s*\\)`),
      relative,
    );
  }
});

test('mutation authorization re-verifies signed sessions and enforces the required role', async () => {
  const [{ authorizeMutation }, { issueSession }] = await Promise.all([
    import('../src/lib/trading/auth.ts'),
    import('../src/lib/trading/session.ts'),
  ]);
  const operatorToken = issueSession('operator');

  const missing = authorizeMutation(
    requestWithSession(),
    'keys.manage',
    'SECRET_MANAGEMENT',
    'admin',
  );
  assert.equal(missing?.status, 401);
  assert.equal((await missing?.json()).code, 'UNAUTHORIZED');

  const forbidden = authorizeMutation(
    requestWithSession(operatorToken),
    'keys.manage',
    'SECRET_MANAGEMENT',
    'admin',
  );
  assert.equal(forbidden?.status, 403);
  assert.equal((await forbidden?.json()).code, 'FORBIDDEN');

  assert.equal(authorizeMutation(
    requestWithSession(operatorToken),
    'pipeline.run',
    'MUTATION_EXECUTION_SENSITIVE',
    'operator',
  ), null);
});

test('mutation audit preserves classification and authenticated role without recording tokens', async () => {
  const [{ authorizeMutation }, { issueSession }] = await Promise.all([
    import('../src/lib/trading/auth.ts'),
    import('../src/lib/trading/session.ts'),
  ]);
  const token = issueSession('admin');

  assert.equal(authorizeMutation(
    requestWithSession(token),
    'mode.update',
    'MUTATION_EXECUTION_SENSITIVE',
    'admin',
  ), null);

  const events = auditEvents();
  assert.equal(events.length, 1);
  assert.equal(events[0].event, 'dashboard_mutation_authorization');
  assert.equal(events[0].classification, 'MUTATION_EXECUTION_SENSITIVE');
  assert.equal(events[0].role, 'admin');
  assert.equal(events[0].authorization_outcome, 'GRANTED');
  assert.equal(Object.hasOwn(events[0], 'mutation_outcome'), false);
  assert.doesNotMatch(JSON.stringify(events[0]), new RegExp(token.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
});

test('unsafe audit storage fails closed before sensitive and low-risk handlers run', async () => {
  const [{ issueSession }, modeRoute, watchlistRoute] = await Promise.all([
    import('../src/lib/trading/session.ts'),
    import('../src/app/api/trading/mode/route.ts'),
    import('../src/app/api/trading/watchlist/route.ts'),
  ]);
  const memory = path.join(researchDirectory, 'memory');
  const auditTarget = path.join(researchDirectory, 'audit-target');
  fs.symlinkSync(auditTarget, memory, 'dir');
  const token = issueSession('admin');

  const sensitive = await modeRoute.POST(new Request('https://dashboard.test/api/trading/mode', {
    method: 'POST',
    headers: { cookie: `trading_session=${token}`, 'content-type': 'application/json' },
    body: JSON.stringify({ mode: 'paper' }),
  }));
  assert.equal(sensitive.status, 503);
  const sensitiveBody = await sensitive.json();
  assert.deepEqual(Object.keys(sensitiveBody).sort(), ['code', 'message', 'ok']);
  assert.equal(sensitiveBody.code, 'AUDIT_UNAVAILABLE');
  assert.doesNotMatch(JSON.stringify(sensitiveBody), new RegExp(token.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
  assert.doesNotMatch(JSON.stringify(sensitiveBody), new RegExp(researchDirectory.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
  assert.equal(fs.existsSync(path.join(researchDirectory, '.mode')), false);

  const lowRisk = await watchlistRoute.POST(new Request('https://dashboard.test/api/trading/watchlist', {
    method: 'POST',
    headers: { cookie: `trading_session=${issueSession('operator')}`, 'content-type': 'application/json' },
    body: JSON.stringify({ action: 'add', symbol: 'BTC' }),
  }));
  assert.equal(lowRisk.status, 503);
  assert.equal((await lowRisk.json()).code, 'AUDIT_UNAVAILABLE');
  assert.equal(fs.existsSync(path.join(auditTarget, 'watchlist.json')), false);
});

test('malformed persisted audit evidence fails closed without overwriting it', async () => {
  const [{ authorizeMutation }, { issueSession }] = await Promise.all([
    import('../src/lib/trading/auth.ts'),
    import('../src/lib/trading/session.ts'),
  ]);
  const auditDirectory = path.join(researchDirectory, 'memory');
  const auditPath = path.join(auditDirectory, 'dashboard_mutation_audit.jsonl');
  const malformed = '{"event":"dashboard_mutation_authorization"\n';
  fs.mkdirSync(auditDirectory, { mode: 0o700 });
  fs.writeFileSync(auditPath, malformed, { mode: 0o600 });

  const response = authorizeMutation(
    requestWithSession(issueSession('operator')),
    'pipeline.run',
    'MUTATION_EXECUTION_SENSITIVE',
    'operator',
  );

  assert.equal(response?.status, 503);
  assert.equal((await response?.json()).code, 'AUDIT_UNAVAILABLE');
  assert.equal(fs.readFileSync(auditPath, 'utf8'), malformed);
});

test('denied mutations retain typed responses when best-effort audit storage is unavailable', async () => {
  const [{ authorizeMutation }, { issueSession }] = await Promise.all([
    import('../src/lib/trading/auth.ts'),
    import('../src/lib/trading/session.ts'),
  ]);
  const memory = path.join(researchDirectory, 'memory');
  const auditTarget = path.join(researchDirectory, 'audit-target');
  fs.symlinkSync(auditTarget, memory, 'dir');

  const unauthorized = authorizeMutation(
    requestWithSession(),
    'pipeline.run',
    'MUTATION_EXECUTION_SENSITIVE',
    'operator',
  );
  assert.equal(unauthorized?.status, 401);
  assert.equal((await unauthorized?.json()).code, 'UNAUTHORIZED');

  const forbidden = authorizeMutation(
    requestWithSession(issueSession('reader')),
    'pipeline.run',
    'MUTATION_EXECUTION_SENSITIVE',
    'operator',
  );
  assert.equal(forbidden?.status, 403);
  assert.equal((await forbidden?.json()).code, 'FORBIDDEN');
  assert.equal(fs.existsSync(auditTarget), false);
});

test('authorization evidence does not claim mutation completion', async () => {
  const [{ issueSession }, planRoute] = await Promise.all([
    import('../src/lib/trading/session.ts'),
    import('../src/app/api/trading/plan/route.ts'),
  ]);

  const response = await planRoute.POST(new Request('https://dashboard.test/api/trading/plan', {
    method: 'POST',
    headers: { cookie: `trading_session=${issueSession('operator')}`, 'content-type': 'application/json' },
    body: JSON.stringify({}),
  }));

  assert.equal(response.status, 400);
  assert.equal(auditEvents().at(-1)?.event, 'dashboard_mutation_authorization');
  assert.equal(auditEvents().at(-1)?.authorization_outcome, 'GRANTED');
  assert.equal(Object.hasOwn(auditEvents().at(-1), 'mutation_outcome'), false);
});

test('checkAuth is pure and never appends mutation audit events', async () => {
  const [{ checkAuth }, { issueSession }] = await Promise.all([
    import('../src/lib/trading/auth.ts'),
    import('../src/lib/trading/session.ts'),
  ]);

  assert.equal(checkAuth(requestWithSession(issueSession('reader'))), null);
  const denied = checkAuth(requestWithSession());
  assert.equal(denied?.status, 401);
  assert.deepEqual(auditEvents(), []);
});

test('auth helpers fail closed for malformed cookies and unavailable session configuration', async () => {
  const { authorizeMutation, checkAuth } = await import('../src/lib/trading/auth.ts');
  const malformedCookie = new Request('https://dashboard.test/api/trading/run', {
    headers: { cookie: 'not-a-session; trading_session; other=value' },
  });
  assert.equal(checkAuth(malformedCookie)?.status, 401);

  process.env.TRADING_DASHBOARD_SESSION_SECRET = 'too-short';
  const configured = new Request('https://dashboard.test/api/trading/run', {
    headers: { cookie: 'trading_session=synthetic.token' },
  });
  assert.equal(checkAuth(configured)?.status, 503);
  assert.equal(authorizeMutation(
    configured,
    'pipeline.run',
    'MUTATION_EXECUTION_SENSITIVE',
    'operator',
  )?.status, 503);
  assert.equal(auditEvents().at(-1)?.authorization_outcome, 'CONFIGURATION_ERROR');
});

test('client auth guard never unlocks on timeout or fetch failure', () => {
  const source = fs.readFileSync(path.join(root, 'src/components/trading/auth-guard.tsx'), 'utf8');
  assert.doesNotMatch(source, /setTimeout\([\s\S]{0,200}setState\(['"]authorized['"]\)/);
  const catchBlocks = [...source.matchAll(/catch\s*\{([\s\S]*?)\}/g)].map((match) => match[1]);
  assert.ok(catchBlocks.length > 0);
  for (const block of catchBlocks) assert.doesNotMatch(block, /setState\(['"]authorized['"]\)/);
});

test('auth helper has structured fail-closed configuration and credential errors', () => {
  const source = fs.readFileSync(path.join(root, 'src/lib/trading/auth.ts'), 'utf8');
  assert.match(source, /CONFIGURATION_ERROR/);
  assert.match(source, /UNAUTHORIZED/);
  assert.doesNotMatch(source, /if\s*\(!password\)\s*return null/);
});
