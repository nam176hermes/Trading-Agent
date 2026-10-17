import assert from 'node:assert/strict';
import { realpathSync } from 'node:fs';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

const require = createRequire(import.meta.url);
const pluginRequire = createRequire(require.resolve('@next/eslint-plugin-next'));

test('Next lint resolves the local fail-loud glob shim', () => {
  assert.equal(
    realpathSync(pluginRequire.resolve('fast-glob')),
    fileURLToPath(new URL('../vendor/next-fast-glob-unsupported/index.js', import.meta.url)),
  );
  assert.throws(
    () => pluginRequire('fast-glob').globSync('**'),
    /settings\.next\.rootDir globbing is unsupported/,
  );
});

test('dashboard lint configuration does not request unsupported rootDir globbing', async () => {
  const { default: config } = await import('../eslint.config.mjs');
  for (const entry of config) assert.equal(entry.settings?.next?.rootDir, undefined);
});
