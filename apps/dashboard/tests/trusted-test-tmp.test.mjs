import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import { createTrustedTestEnvironment } from './trusted-test-tmp.mjs';

function privateRoot() {
  const parent = path.join(os.homedir(), '.cache', 'trading-agent', 'trusted-test-tmp-js-tests');
  fs.mkdirSync(parent, { recursive: true, mode: 0o700 });
  fs.chmodSync(parent, 0o700);
  const root = fs.mkdtempSync(path.join(parent, 'explicit-'));
  fs.chmodSync(root, 0o700);
  return root;
}

test('creates a private per-run directory under an explicit trusted root', () => {
  const root = privateRoot();
  const session = createTrustedTestEnvironment('dashboard', {
    ...process.env,
    TRADING_TEST_TMP_ROOT: root,
  });
  try {
    assert.equal(path.dirname(session.directory), fs.realpathSync(root));
    assert.equal(fs.statSync(session.directory).mode & 0o777, 0o700);
    assert.equal(session.environment.TMPDIR, session.directory);
    assert.equal(session.environment.TEMP, session.directory);
    assert.equal(session.environment.TMP, session.directory);
  } finally {
    session.cleanup();
    fs.rmdirSync(root);
  }
});

test('rejects an explicitly selected root beneath a writable ancestor', () => {
  assert.throws(
    () => createTrustedTestEnvironment('dashboard', {
      ...process.env,
      TRADING_TEST_TMP_ROOT: '/tmp',
    }),
    /writable ancestor/,
  );
});

test('falls back from an unsafe ambient TMPDIR to the private user cache', () => {
  const environment = { ...process.env, TMPDIR: '/tmp' };
  delete environment.TRADING_TEST_TMP_ROOT;
  const session = createTrustedTestEnvironment('dashboard', environment);
  try {
    assert.equal(
      path.dirname(session.directory),
      fs.realpathSync(path.join(os.homedir(), '.cache', 'trading-agent', 'test-tmp')),
    );
  } finally {
    session.cleanup();
  }
});
