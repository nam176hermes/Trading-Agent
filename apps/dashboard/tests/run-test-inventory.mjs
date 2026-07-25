import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const testsRoot = path.dirname(fileURLToPath(import.meta.url));
const dashboardRoot = path.dirname(testsRoot);
const manifestPath = path.join(testsRoot, 'test-inventory.json');
const expectedKeys = [
  'integration_test_suffix',
  'node_test_suffix',
  'recursive',
  'schema_version',
  'support_files',
];

function loadManifest() {
  const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
  assert.deepEqual(Object.keys(manifest).sort(), expectedKeys);
  assert.equal(manifest.schema_version, 1);
  assert.equal(manifest.recursive, true);
  assert.equal(manifest.node_test_suffix, '.test.mjs');
  assert.equal(manifest.integration_test_suffix, '.integration.sh');
  assert.deepEqual(
    [...manifest.support_files].sort(),
    ['run-test-inventory.mjs', 'test-inventory.json'],
  );
  return manifest;
}

function walk(directory) {
  const files = [];
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const absolute = path.join(directory, entry.name);
    if (entry.isDirectory()) files.push(...walk(absolute));
    else if (entry.isFile()) files.push(absolute);
    else throw new Error(`unsupported test inventory entry: ${absolute}`);
  }
  return files;
}

function inventory() {
  const manifest = loadManifest();
  const support = new Set(manifest.support_files);
  const nodeTests = [];
  const integrationTests = [];
  const unclassified = [];
  for (const absolute of walk(testsRoot)) {
    const relativeToTests = path.relative(testsRoot, absolute).split(path.sep).join('/');
    const relativeToDashboard = `tests/${relativeToTests}`;
    if (relativeToTests.endsWith(manifest.node_test_suffix)) {
      nodeTests.push(relativeToDashboard);
    } else if (relativeToTests.endsWith(manifest.integration_test_suffix)) {
      integrationTests.push(relativeToDashboard);
    } else if (!support.has(relativeToTests)) {
      unclassified.push(relativeToDashboard);
    }
  }
  nodeTests.sort();
  integrationTests.sort();
  unclassified.sort();
  assert.ok(nodeTests.length > 0, 'dashboard inventory has no Node tests');
  assert.ok(integrationTests.length > 0, 'dashboard inventory has no integration tests');
  assert.deepEqual(unclassified, [], `unclassified dashboard test files: ${unclassified.join(', ')}`);
  return { schema_version: 1, node_tests: nodeTests, integration_tests: integrationTests };
}

const discovered = inventory();
if (process.argv.includes('--list-json')) {
  process.stdout.write(`${JSON.stringify(discovered)}\n`);
  process.exit(0);
}

const environment = {
  ...process.env,
  LIVE_EXECUTION_ENABLED: 'false',
  LIVE_TRADING_APPROVED: 'false',
};
const nodeResult = spawnSync(
  process.execPath,
  ['--test', ...discovered.node_tests],
  { cwd: dashboardRoot, env: environment, stdio: 'inherit' },
);
if (nodeResult.error) throw nodeResult.error;
if (nodeResult.status !== 0) process.exit(nodeResult.status ?? 1);

for (const script of discovered.integration_tests) {
  const result = spawnSync('bash', [script], {
    cwd: dashboardRoot,
    env: environment,
    stdio: 'inherit',
  });
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status ?? 1);
}
