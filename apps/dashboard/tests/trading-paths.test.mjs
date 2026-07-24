import assert from 'node:assert/strict';
import fs from 'node:fs';
import { registerHooks } from 'node:module';
import os from 'node:os';
import path from 'node:path';
import test, { afterEach } from 'node:test';
import { fileURLToPath } from 'node:url';

const ROOT = fileURLToPath(new URL('..', import.meta.url));
const PATHS_SOURCE = path.join(ROOT, 'src', 'lib', 'trading', 'paths.ts');
const ORIGINAL_ENV = { ...process.env };

registerHooks({
  resolve(specifier, _context, nextResolve) {
    if (specifier === 'server-only') {
      return { shortCircuit: true, url: 'data:text/javascript,export%20{}' };
    }
    return nextResolve(specifier);
  },
});

afterEach(() => {
  process.env = { ...ORIGINAL_ENV };
});

test('runtime path resolver is explicitly server-only', () => {
  const source = fs.readFileSync(PATHS_SOURCE, 'utf8');
  assert.match(source, /^import ['"]server-only['"];?$/m);
});

test('runtime paths default below the external user data root', async () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'trading-paths-home-'));
  process.env.HOME = home;
  delete process.env.TRADING_DATA_ROOT;
  delete process.env.TRADING_MODE_FILE;
  delete process.env.TRADING_KILL_SWITCH_PATH;

  try {
    const paths = await import('../src/lib/trading/paths.ts');
    const root = path.join(home, '.local', 'share', 'trading-agent');
    assert.equal(paths.researchDataRoot(), root);
    assert.equal(paths.reportsDir(), path.join(root, 'reports'));
    assert.equal(paths.decisionsDir(), path.join(root, 'decisions'));
    assert.equal(paths.memoryDir(), path.join(root, 'memory'));
    assert.equal(paths.modeFile(), path.join(root, '.mode'));
    assert.equal(paths.killSwitchFile(), path.join(root, '.kill_switch'));
  } finally {
    fs.rmSync(home, { recursive: true, force: true });
  }
});

test('protected runtime overrides are resolved centrally', async () => {
  const paths = await import('../src/lib/trading/paths.ts');
  const root = path.join(os.tmpdir(), 'configured-trading-data');
  const mode = path.join(os.tmpdir(), 'configured-mode');
  const kill = path.join(os.tmpdir(), 'configured-kill');
  process.env.TRADING_DATA_ROOT = root;
  process.env.TRADING_MODE_FILE = mode;
  process.env.TRADING_KILL_SWITCH_PATH = kill;

  assert.equal(paths.researchDataRoot(), root);
  assert.equal(paths.reportsDir(), path.join(root, 'reports'));
  assert.equal(paths.decisionsDir(), path.join(root, 'decisions'));
  assert.equal(paths.memoryDir(), path.join(root, 'memory'));
  assert.equal(paths.modeFile(), mode);
  assert.equal(paths.killSwitchFile(), kill);
});
