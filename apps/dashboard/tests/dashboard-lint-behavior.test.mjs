import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const COMPONENTS = path.join(ROOT, 'src/components/trading');

test('stop-loss refresh records the completed portfolio fetch time', () => {
  const source = fs.readFileSync(path.join(COMPONENTS, 'portfolio-card.tsx'), 'utf8');
  const saveStop = source.slice(source.indexOf('async function saveStop'), source.indexOf('function cancelEdit'));

  assert.doesNotMatch(saveStop, /setDataFetchedAt\(null\)/);
  assert.match(
    saveStop,
    /await refresh\.json\(\)\.then\([\s\S]*fetchedAt: Date\.now\(\)[\s\S]*setData\(json\);\s*setDataFetchedAt\(fetchedAt\)/,
  );
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
