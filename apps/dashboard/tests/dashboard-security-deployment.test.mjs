import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

test('deployment docs constrain the process-local login limiter', () => {
  const docs = fs.readFileSync(path.join(ROOT, 'docs/operations/dashboard-security.md'), 'utf8');

  assert.match(docs, /resets? on[\s\S]{0,30}(?:process )?restart/i);
  assert.match(docs, /replicas?[\s\S]{0,80}(?:multiply|independent)[\s\S]{0,80}(?:allowance|limit)/i);
  assert.match(docs, /single[- ]process deployment/i);
  assert.match(docs, /shared[\s\S]{0,30}(?:rate )?limiter[\s\S]{0,80}horizontal scal/i);
});

test('isolated HTTP smoke proves authenticated credential operations stay disabled', () => {
  const smoke = fs.readFileSync(path.join(ROOT, 'tests/dashboard-security.integration.sh'), 'utf8');

  assert.match(smoke, /fixture-admin-password/);
  assert.match(smoke, /\/api\/trading\/keys/);
  assert.match(smoke, /PROCESS_EXECUTION_DISABLED/);
  assert.doesNotMatch(smoke, /FAKE_PYTHON|\.venv\/bin\/python/);
});
