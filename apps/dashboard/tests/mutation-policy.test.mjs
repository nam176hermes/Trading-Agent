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
let researchDirectory;

beforeEach(() => {
  researchDirectory = fs.mkdtempSync(path.join(os.tmpdir(), 'mutation-policy-'));
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
  assert.equal(events[0].classification, 'MUTATION_EXECUTION_SENSITIVE');
  assert.equal(events[0].role, 'admin');
  assert.equal(events[0].outcome, 'AUTHORIZED');
  assert.doesNotMatch(JSON.stringify(events[0]), new RegExp(token.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
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
