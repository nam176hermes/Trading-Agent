import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { afterEach } from 'node:test';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { registerHooks } from 'node:module';

const ROOT = fileURLToPath(new URL('..', import.meta.url));
const ORIGINAL_ENV = { ...process.env };
const ORIGINAL_FETCH = globalThis.fetch;
const temporaryDirectories = [];

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === 'server-only') return { shortCircuit: true, url: 'data:text/javascript,export%20{}' };
    if (specifier === 'next/server') {
      return { shortCircuit: true, url: pathToFileURL(path.join(ROOT, 'node_modules/next/server.js')).href };
    }
    if (specifier.startsWith('@/')) {
      const target = pathToFileURL(path.join(ROOT, 'src', specifier.slice(2))).href;
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

afterEach(() => {
  process.env = { ...ORIGINAL_ENV };
  globalThis.fetch = ORIGINAL_FETCH;
  while (temporaryDirectories.length) fs.rmSync(temporaryDirectories.pop(), { recursive: true, force: true });
});

function privateToken(contents = 'w'.repeat(32)) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'operator-api-bff-'));
  temporaryDirectories.push(directory);
  fs.chmodSync(directory, 0o700);
  const target = path.join(directory, 'web.token');
  fs.writeFileSync(target, contents, { mode: 0o600 });
  return target;
}

const NOW = '2026-08-30T12:00:00Z';
const OPERATION_ID = `op_${'a'.repeat(32)}`;

function receipt(overrides = {}) {
  return {
    schema_version: 'operator-command-receipt-v1',
    command_id: `cmd_${'a'.repeat(32)}`,
    idempotency_key_sha256: '1'.repeat(64),
    correlation_id: OPERATION_ID,
    request_sha256: '2'.repeat(64),
    actor: {
      schema_version: 'operator-actor-v1',
      principal_id: 'dashboard-web',
      interface: 'WEB',
    },
    command_type: 'SET_KILL_SWITCH',
    desired_state: 'KILL_SWITCH_ACTIVE',
    prior_state_sha256: '3'.repeat(64),
    expected_state_sha256: null,
    safety_evidence_sha256: null,
    reason_sha256: '4'.repeat(64),
    accepted_at: NOW,
    applied_at: NOW,
    completed_at: NOW,
    outcome: 'APPLIED',
    outcome_code: 'KILL_SWITCH_ACTIVATED',
    resulting_state_sha256: '5'.repeat(64),
    intent_sha256: '6'.repeat(64),
    applied_sha256: '7'.repeat(64),
    receipt_sha256: '8'.repeat(64),
    ...overrides,
  };
}

function operatorEnvelope(overrides = {}) {
  return {
    schema_version: '1.0.0',
    trace_id: 'trace_operator_fixture',
    generated_at: NOW,
    data: {
      result: {
        schema_version: 'operator-command-execution-result-v1',
        receipt: receipt(),
        deduplicated: false,
        ...overrides,
      },
    },
  };
}

function jsonResponse(value, init = {}) {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { 'content-type': 'application/json', ...init.headers },
    ...init,
  });
}

function controlStatusEnvelope(killSwitchState) {
  return {
    schema_version: '2.0.0',
    trace_id: 'trace_control_fixture',
    generated_at: NOW,
    freshness: null,
    data: {
      api_liveness: 'UP',
      api_readiness: 'READY',
      backend_service_liveness: 'UNKNOWN',
      research_pipeline_health: 'STALE',
      research_data_freshness: {
        status: 'STALE', as_of: NOW, age_seconds: 1, stale_after_seconds: 60,
      },
      live_price_freshness: {
        status: 'UNKNOWN', as_of: null, age_seconds: null, stale_after_seconds: 60,
      },
      database_status: 'UNKNOWN',
      requested_mode: 'PAPER',
      effective_mode: 'PAPER',
      execution_capability: 'NON_LIVE',
      kill_switch_state: killSwitchState,
      orders_count: null,
      trades_count: null,
    },
  };
}

test('protected WEB token loader enforces the frozen file contract', async (t) => {
  const { loadOperatorWebToken } = await import('../src/lib/trading/operator-api.ts');
  const valid = privateToken(`${'v'.repeat(32)}\n`);
  assert.equal(loadOperatorWebToken(valid), 'v'.repeat(32));

  const cases = [
    ['missing', path.join(path.dirname(valid), 'missing.token')],
    ['relative', 'web.token'],
    ['whitespace', privateToken(` ${'x'.repeat(31)}`)],
    ['non-ASCII', privateToken(`${'x'.repeat(31)}é`)],
    ['embedded newline', privateToken(`${'x'.repeat(32)}\nextra`)],
    ['too short', privateToken('x'.repeat(31))],
    ['too large', privateToken('x'.repeat(4097))],
  ];
  const symlink = path.join(path.dirname(valid), 'link.token');
  fs.symlinkSync(valid, symlink);
  cases.push(['symlink', symlink]);
  const loose = privateToken('x'.repeat(32));
  fs.chmodSync(loose, 0o640);
  cases.push(['mode', loose]);

  for (const [name, target] of cases) {
    await t.test(name, () => assert.throws(
      () => loadOperatorWebToken(target),
      /Operator API is unavailable\.$/,
    ));
  }

  await t.test('owner', () => {
    const original = fs.fstatSync;
    fs.fstatSync = (...args) => {
      const metadata = original(...args);
      const changed = Object.create(metadata);
      Object.defineProperty(changed, 'uid', { value: metadata.uid + 1 });
      return changed;
    };
    try {
      assert.throws(() => loadOperatorWebToken(valid), /Operator API is unavailable\.$/);
    } finally {
      fs.fstatSync = original;
    }
  });
});

test('kill-switch activation uses the exact loopback command contract and preserves retry identity', async () => {
  const { submitKillSwitchActivation } = await import('../src/lib/trading/operator-api.ts');
  const tokenPath = privateToken('t'.repeat(32));
  process.env.OPERATOR_API_WEB_TOKEN_FILE = tokenPath;
  const requests = [];
  const fetcher = async (input, init) => {
    requests.push({ input: String(input), init, body: JSON.parse(init.body) });
    return jsonResponse(operatorEnvelope());
  };
  const command = { operationId: OPERATION_ID, reason: 'volatility drill' };

  const first = await submitKillSwitchActivation(command, fetcher);
  const retry = await submitKillSwitchActivation(command, fetcher);

  assert.equal(first.receipt.receipt_sha256, '8'.repeat(64));
  assert.deepEqual(retry, first);
  assert.equal(requests.length, 2);
  assert.deepEqual(requests.map(({ input, init, body }) => ({
    input,
    method: init.method,
    redirect: init.redirect,
    authorization: init.headers.authorization,
    accept: init.headers.accept,
    contentType: init.headers['content-type'],
    body,
  })), [0, 1].map(() => ({
    input: 'http://127.0.0.1:8402/v1/commands',
    method: 'POST',
    redirect: 'error',
    authorization: `Bearer ${'t'.repeat(32)}`,
    accept: 'application/json',
    contentType: 'application/json',
    body: {
      schema_version: 'submit-operator-command-v1',
      command_id: `cmd_${'a'.repeat(32)}`,
      idempotency_key: `web.kill-switch.activate:${OPERATION_ID}`,
      correlation_id: OPERATION_ID,
      expected_state_sha256: null,
      command: {
        command_type: 'SET_KILL_SWITCH',
        desired_state: 'ACTIVE',
        reason: 'volatility drill',
      },
    },
  })));
});

test('operator client fails closed on invalid commands and untrusted responses', async (t) => {
  const { submitKillSwitchActivation } = await import('../src/lib/trading/operator-api.ts');
  process.env.OPERATOR_API_WEB_TOKEN_FILE = privateToken('z'.repeat(32));
  let calls = 0;
  const unused = async () => { calls += 1; return jsonResponse(operatorEnvelope()); };
  for (const command of [
    { operationId: 'op_BAD', reason: 'drill' },
    { operationId: OPERATION_ID, reason: '' },
    { operationId: OPERATION_ID, reason: 'x'.repeat(257) },
    { operationId: OPERATION_ID, reason: 'line\nbreak' },
  ]) {
    await assert.rejects(submitKillSwitchActivation(command, unused), /Operator API is unavailable/);
  }
  assert.equal(calls, 0);

  const redirected = jsonResponse(operatorEnvelope());
  Object.defineProperties(redirected, {
    redirected: { value: true },
    url: { value: 'https://example.test/v1/commands' },
  });
  const oversized = new Response('x'.repeat(262_145), {
    headers: { 'content-type': 'application/json', 'content-length': '262145' },
  });
  const malformedReceipt = operatorEnvelope();
  malformedReceipt.data.result.receipt.extra = true;
  const cases = [
    ['redirect', redirected],
    ['wrong content type', new Response('{}', { headers: { 'content-type': 'text/plain' } })],
    ['oversized', oversized],
    ['malformed receipt', jsonResponse(malformedReceipt)],
    ['upstream error', jsonResponse({}, { status: 503 })],
  ];
  for (const [name, response] of cases) {
    await t.test(name, async () => assert.rejects(
      submitKillSwitchActivation(
        { operationId: OPERATION_ID, reason: 'drill' },
        async () => response,
      ),
      /Operator API is unavailable/,
    ));
  }
  await t.test('timeout', async () => assert.rejects(
    submitKillSwitchActivation(
      { operationId: OPERATION_ID, reason: 'drill' },
      async () => { throw new DOMException('timeout', 'TimeoutError'); },
    ),
    /Operator API is unavailable/,
  ));
});

test('kill-switch route rejects clear locally and validates exact activation JSON', async () => {
  const sessionSecret = Array.from({ length: 32 }, (_, index) => (index % 16).toString(16)).join('');
  process.env.TRADING_DASHBOARD_SESSION_SECRET = sessionSecret;
  process.env.TRADING_DATA_ROOT = path.dirname(privateToken('d'.repeat(32)));
  process.env.OPERATOR_API_WEB_TOKEN_FILE = privateToken('o'.repeat(32));
  const [{ issueSession }, route] = await Promise.all([
    import('../src/lib/trading/session.ts'),
    import('../src/app/api/trading/kill-switch/route.ts'),
  ]);
  const request = (body) => new Request('https://dashboard.test/api/trading/kill-switch', {
    method: 'POST',
    headers: {
      cookie: `trading_session=${issueSession('admin')}`,
      origin: 'https://dashboard.test',
      'content-type': 'application/json',
    },
    body: JSON.stringify(body),
  });
  let upstreamCalls = 0;
  globalThis.fetch = async () => { upstreamCalls += 1; return jsonResponse(operatorEnvelope()); };

  const clear = await route.POST(request({ action: 'off' }));
  assert.equal(clear.status, 403);
  assert.equal((await clear.json()).code, 'CLI_REQUIRED');
  assert.equal(upstreamCalls, 0);

  for (const body of [
    { action: 'on', reason: 'drill' },
    { action: 'on', reason: 'drill', operation_id: OPERATION_ID, extra: true },
    { action: 'on', reason: '', operation_id: OPERATION_ID },
    { action: 'on', reason: 'drill', operation_id: 'op_BAD' },
  ]) assert.equal((await route.POST(request(body))).status, 400);
  assert.equal(upstreamCalls, 0);

  for (const [observedState, expectedStatus] of [
    ['ACTIVE', 'OBSERVED'],
    ['INACTIVE', 'PENDING'],
  ]) {
    globalThis.fetch = async (input) => String(input).startsWith('http://127.0.0.1:8402')
      ? jsonResponse(operatorEnvelope())
      : jsonResponse(controlStatusEnvelope(observedState));
    const response = await route.POST(request({
      action: 'on', reason: 'drill', operation_id: OPERATION_ID,
    }));
    assert.equal(response.status, 200);
    const body = await response.json();
    assert.equal(body.command_status, 'SUCCEEDED');
    assert.equal(body.observation_status, expectedStatus);
    assert.equal(body.receipt.receipt_sha256, '8'.repeat(64));
  }

  globalThis.fetch = async (input) => {
    if (String(input).startsWith('http://127.0.0.1:8402')) return jsonResponse(operatorEnvelope());
    throw new Error('control unavailable');
  };
  const unavailableObservation = await route.POST(request({
    action: 'on', reason: 'drill', operation_id: OPERATION_ID,
  }));
  assert.equal(unavailableObservation.status, 200);
  assert.equal((await unavailableObservation.json()).observation_status, 'UNAVAILABLE');
});

test('active kill-switch UI is CLI-clear only', () => {
  const quickActions = fs.readFileSync(path.join(ROOT, 'src/components/trading/quick-actions.tsx'), 'utf8');
  const haltBanner = fs.readFileSync(path.join(ROOT, 'src/components/trading/halt-banner.tsx'), 'utf8');
  assert.match(quickActions, /Clear via CLI/);
  assert.match(haltBanner, /Clear via CLI/);
  assert.doesNotMatch(quickActions, /Resume Trading|Resuming\.\.\./);
  assert.doesNotMatch(haltBanner, /action: 'off'|Override/);
});
