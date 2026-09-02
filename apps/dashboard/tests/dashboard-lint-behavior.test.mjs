import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const COMPONENTS = path.join(ROOT, 'src/components/trading');

test('portfolio presents stop-loss state without a local mutation path', () => {
  const source = fs.readFileSync(path.join(COMPONENTS, 'portfolio-card.tsx'), 'utf8');
  assert.match(source, /Stop mutation unavailable/);
  assert.doesNotMatch(source, /saveStop|startEditStop|\/api\/trading\/update-stop/);
});

test('ticker flashes only the original strip without remounting on same-direction updates', async () => {
  const { getPriceFlashClass, mergePriceUpdates } = await import(
    '../src/lib/trading/price-ticker-state.ts'
  );

  const initial = mergePriceUpdates({}, [['BTC', { price: 100 }]]);
  assert.equal(getPriceFlashClass(initial.BTC, false), '');
  assert.equal(getPriceFlashClass(initial.BTC, true), '');

  const firstRise = mergePriceUpdates(initial, [['BTC', { price: 101 }]]);
  assert.equal(getPriceFlashClass(firstRise.BTC, false), 'price-flash-up');
  assert.equal(getPriceFlashClass(firstRise.BTC, true), '');

  const secondRise = mergePriceUpdates(firstRise, [['BTC', { price: 102 }]]);
  assert.equal(getPriceFlashClass(secondRise.BTC, false), 'price-flash-up');
  assert.equal(getPriceFlashClass(secondRise.BTC, true), '');

  const source = fs.readFileSync(path.join(COMPONENTS, 'price-ticker.tsx'), 'utf8');
  assert.doesNotMatch(source, /flashVersion/);
  assert.doesNotMatch(source, /key=\{`\$\{symbol\}-/);
});

test('alerts age has no per-second timer or render-time clock read', () => {
  const source = fs.readFileSync(path.join(COMPONENTS, 'alerts-panel.tsx'), 'utf8');

  assert.doesNotMatch(source, /setInterval\([\s\S]{0,160}1000\)/);
  assert.doesNotMatch(source, /const secondsSinceFetch[\s\S]{0,160}Date\.now\(\)/);
  assert.match(source, /setDisplayTimestamp\(fetchedAt\)/);
});
