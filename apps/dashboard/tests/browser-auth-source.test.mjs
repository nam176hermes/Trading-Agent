import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const AUTH_GUARD = path.join(ROOT, 'src/components/trading/auth-guard.tsx');
const AUTH_RESPONSE = path.join(ROOT, 'src/lib/trading/browser-auth-response.ts');

test('browser auth uses only the HttpOnly cookie session source', () => {
  const source = fs.readFileSync(AUTH_GUARD, 'utf8');

  for (const forbidden of [
    /localStorage/,
    /sessionStorage/,
    /Authorization/,
    /Bearer/,
    /trading-dashboard-password/,
  ]) {
    assert.doesNotMatch(source, forbidden);
  }
});

test('auth guard checks and creates the cookie session through the auth API', () => {
  const source = fs.readFileSync(AUTH_GUARD, 'utf8');
  const responseSource = fs.readFileSync(AUTH_RESPONSE, 'utf8');

  assert.match(source, /fetch\(['"]\/api\/auth\/session['"],\s*\{[\s\S]*?method:\s*['"]GET['"]/);
  assert.match(responseSource, /await\s+response\.json\(\)/);
  assert.match(responseSource, /authenticated/);
  assert.match(source, /fetch\(['"]\/api\/auth\/session['"],\s*\{[\s\S]*?method:\s*['"]POST['"]/);
  assert.equal(source.match(/readAuthenticatedResponse\(res\)/g)?.length, 2);
});

test('a successful HTTP status with an unauthenticated body stays locked', async () => {
  const { readAuthenticatedResponse } = await import('../src/lib/trading/browser-auth-response.ts');

  assert.equal(
    await readAuthenticatedResponse(Response.json({ authenticated: false }, { status: 200 })),
    false,
  );
  assert.equal(
    await readAuthenticatedResponse(Response.json({ authenticated: true }, { status: 200 })),
    true,
  );
});

test('auth guard stays fail-closed on timeout and network errors', () => {
  const source = fs.readFileSync(AUTH_GUARD, 'utf8');
  const catchBlocks = [...source.matchAll(/catch\s*\{([\s\S]*?)\}/g)].map((match) => match[1]);

  assert.ok(catchBlocks.length > 0);
  for (const block of catchBlocks) assert.doesNotMatch(block, /setState\(['"]authorized['"]\)/);
  assert.match(source, /AbortController/);
});
