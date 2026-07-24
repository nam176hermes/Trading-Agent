import assert from 'node:assert/strict';
import fs from 'node:fs';
import { registerHooks } from 'node:module';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const sourceRoot = path.join(root, 'src');

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
      const target = new URL(specifier.replace(/\.js$/, '') + '.ts', context.parentURL);
      if (fs.existsSync(fileURLToPath(target))) return { shortCircuit: true, url: target.href };
    }
    return nextResolve(specifier, context);
  },
});

function sourceFiles(directory) {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const absolute = path.join(directory, entry.name);
    if (entry.isDirectory()) return sourceFiles(absolute);
    return /\.(?:ts|tsx)$/.test(entry.name) ? [absolute] : [];
  });
}

test('dashboard source has no process execution or Python bridge boundary', () => {
  for (const filename of sourceFiles(sourceRoot)) {
    const relative = path.relative(root, filename);
    const source = fs.readFileSync(filename, 'utf8');
    assert.doesNotMatch(source, /(?:node:)?child_process/, relative);
    assert.doesNotMatch(source, /python-bridge/, relative);
    assert.doesNotMatch(source, /(?<![.\w])(?:spawn|exec|execFile|fork)\s*\(/, relative);
    assert.doesNotMatch(source, /\bshell\s*:/, relative);
  }
});

test('retired process-dependent routes expose only typed unavailable responses', () => {
  const expected = new Map([
    ['close-position', ['POST', 'authorizeMutation']],
    ['service', ['GET', 'POST', 'authorizeMutation']],
    ['execution', ['GET', 'checkAuth']],
    ['performance', ['GET', 'checkAuth']],
    ['performance-export', ['GET', 'checkAuth']],
    ['keys', ['GET', 'POST', 'authorizeMutation']],
  ]);

  for (const [route, markers] of expected) {
    const relative = `src/app/api/trading/${route}/route.ts`;
    const source = fs.readFileSync(path.join(root, relative), 'utf8');
    assert.match(source, /PROCESS_EXECUTION_DISABLED/, relative);
    assert.match(source, /status:\s*503/, relative);
    for (const marker of markers) assert.match(source, new RegExp(`\\b${marker}\\b`), relative);
  }
});

test('no operational route can fall back to run_status files', () => {
  for (const filename of sourceFiles(path.join(sourceRoot, 'app', 'api'))) {
    const relative = path.relative(root, filename);
    assert.doesNotMatch(fs.readFileSync(filename, 'utf8'), /run_status(?:\.json)?/i, relative);
  }
});

test('disabled reads are audit-pure and authorized mutations record only authorization', async () => {
  const originalRoot = process.env.TRADING_DATA_ROOT;
  const originalSecret = process.env.TRADING_DASHBOARD_SESSION_SECRET;
  const researchRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'dashboard-process-boundary-'));
  process.env.TRADING_DATA_ROOT = researchRoot;
  process.env.TRADING_DASHBOARD_SESSION_SECRET = Array.from(
    { length: 32 },
    (_, index) => (index % 16).toString(16),
  ).join('');

  try {
    const { issueSession } = await import('../src/lib/trading/session.ts');
    const token = issueSession('admin');
    const request = (pathname, method = 'GET') => new Request(`https://dashboard.test${pathname}`, {
      method,
      headers: { cookie: `trading_session=${token}`, origin: 'https://dashboard.test' },
    });

    for (const routeName of ['service', 'execution', 'performance', 'performance-export', 'keys']) {
      const route = await import(`../src/app/api/trading/${routeName}/route.ts`);
      const response = await route.GET(request(`/api/trading/${routeName}`));
      assert.equal(response.status, 503, routeName);
      assert.equal((await response.json()).code, 'PROCESS_EXECUTION_DISABLED', routeName);
    }

    const auditPath = path.join(researchRoot, 'memory', 'dashboard_mutation_audit.jsonl');
    assert.equal(fs.existsSync(auditPath), false, 'read routes appended mutation audit');

    for (const routeName of ['close-position', 'service', 'keys']) {
      const route = await import(`../src/app/api/trading/${routeName}/route.ts`);
      const response = await route.POST(request(`/api/trading/${routeName}`, 'POST'));
      assert.equal(response.status, 503, routeName);
      assert.equal((await response.json()).code, 'PROCESS_EXECUTION_DISABLED', routeName);
    }

    const events = fs.readFileSync(auditPath, 'utf8').trim().split('\n').map(JSON.parse);
    assert.equal(events.length, 3);
    assert.deepEqual(events.map((event) => event.outcome), ['AUTHORIZED', 'AUTHORIZED', 'AUTHORIZED']);
    assert.deepEqual(events.map((event) => event.action), ['position.close', 'service.control', 'keys.manage']);
  } finally {
    fs.rmSync(researchRoot, { recursive: true, force: true });
    if (originalRoot === undefined) delete process.env.TRADING_DATA_ROOT;
    else process.env.TRADING_DATA_ROOT = originalRoot;
    if (originalSecret === undefined) delete process.env.TRADING_DASHBOARD_SESSION_SECRET;
    else process.env.TRADING_DASHBOARD_SESSION_SECRET = originalSecret;
  }
});
