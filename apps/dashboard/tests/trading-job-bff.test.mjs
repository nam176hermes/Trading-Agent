import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import { mkdtemp, stat, writeFile } from 'node:fs/promises';
import { createRequire, registerHooks, syncBuiltinESMExports } from 'node:module';
import os from 'node:os';
import path from 'node:path';
import test, { afterEach, beforeEach } from 'node:test';
import { fileURLToPath, pathToFileURL } from 'node:url';

const ROOT = fileURLToPath(new URL('..', import.meta.url));
const require = createRequire(import.meta.url);

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === 'server-only') {
      return { shortCircuit: true, url: 'data:text/javascript,export%20{}' };
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

const SESSION_SECRET = Array.from({ length: 32 }, (_, index) => (index % 16).toString(16)).join('');
const TEST_AUTH_VALUE = ['job', 'api', 'token', 'fixture'].join('-');
const TOKEN = TEST_AUTH_VALUE;
const ORIGINAL_ENV = { ...process.env };
const ORIGINAL_FETCH = globalThis.fetch;
const NOW = '2026-07-12T12:00:00Z';
const OPERATION_ID = '0123456789abcdef0123456789abcdef';
let testResearchRoot;

const canonicalJob = {
  job_id: 'job_123',
  job_type: 'SNAPSHOT',
  state: 'QUEUED',
  payload: { scope: 'default', requested_as_of: null },
  payload_fingerprint: 'a'.repeat(64),
  actor: { actor_type: 'OPERATOR', actor_id: 'dashboard-operator' },
  priority: 3,
  requested_at: NOW,
  updated_at: NOW,
  attempt_count: 0,
  reason_code: 'ENQUEUED',
  result_hash: null,
};

const engineBacktestPayload = {
  engine_backtest: {
    engine_configuration: {
      artifact_id: '11111111-1111-4111-8111-111111111111',
      sha256: '1'.repeat(64),
      media_type: 'application/json',
    },
    instrument_catalog: {
      artifact_id: '22222222-2222-4222-8222-222222222222',
      sha256: '2'.repeat(64),
      media_type: 'application/json',
    },
    strategy_configuration: {
      artifact_id: '33333333-3333-4333-8333-333333333333',
      sha256: '3'.repeat(64),
      media_type: 'application/json',
    },
    market_data: {
      artifact_id: '44444444-4444-4444-8444-444444444444',
      sha256: '4'.repeat(64),
      media_type: 'application/jsonl',
    },
    start_time: '2026-07-01T00:00:00Z',
    end_time: '2026-08-01T00:00:00.123456Z',
  },
};

const engineBacktestJob = {
  ...canonicalJob,
  job_type: 'BACKTEST',
  payload: engineBacktestPayload,
};

function envelope(data, traceId = 'trace_upstream') {
  return { schema_version: '1.0.0', trace_id: traceId, generated_at: NOW, data };
}

function jsonResponse(value, status = 200, headers = {}) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { 'content-type': 'application/json', 'x-trace-id': 'trace_upstream', ...headers },
  });
}

async function sessionRequest(url, role, init = {}) {
  const { issueSession } = await import('../src/lib/trading/session.ts');
  const headers = new Headers(init.headers);
  if (role) headers.set('cookie', `trading_session=${issueSession(role)}`);
  headers.set('origin', new URL(url).origin);
  return new Request(url, { ...init, headers });
}

beforeEach(() => {
  testResearchRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'trading-job-bff-audit-'));
  process.env.TRADING_DASHBOARD_SESSION_SECRET = SESSION_SECRET;
  process.env.TRADING_JOB_API_TOKEN = TOKEN;
  process.env.TRADING_JOB_COMMANDS_ENABLED = '1';
  process.env.TRADING_DATA_ROOT = testResearchRoot;
});

afterEach(() => {
  process.env = { ...ORIGINAL_ENV };
  globalThis.fetch = ORIGINAL_FETCH;
  fs.rmSync(testResearchRoot, { recursive: true, force: true });
});

test('job API client uses only fixed loopback origin and adds bearer token server-side', async () => {
  let captured;
  globalThis.fetch = async (input, init) => {
    captured = { input: String(input), init };
    return jsonResponse(envelope({ items: [canonicalJob], limit: 50, offset: 0 }));
  };
  const { listJobs } = await import('../src/lib/trading/job-api.ts');

  const result = await listJobs('state=QUEUED');

  assert.equal(captured.input, 'http://127.0.0.1:8401/v1/jobs?state=QUEUED');
  assert.equal(new Headers(captured.init.headers).get('authorization'), `Bearer ${TOKEN}`);
  assert.equal(captured.init.redirect, 'error');
  assert.equal(result.response.status, 200);
  assert.equal(JSON.stringify(await result.response.clone().json()).includes(TOKEN), false);
});

test('job API client cancels upstream bodies rejected by declared or streamed size', async () => {
  const { listJobs } = await import('../src/lib/trading/job-api.ts');

  for (const declared of [true, false]) {
    let pulls = 0;
    let cancelled = false;
    globalThis.fetch = async () => new Response(new ReadableStream({
      pull(controller) {
        pulls += 1;
        controller.enqueue(new Uint8Array(declared ? 2 : 65_536));
        if (pulls === 100) controller.close();
      },
      cancel() { cancelled = true; },
    }), { headers: declared ? { 'content-length': '262145' } : {} });

    const response = (await listJobs('')).response;

    assert.equal(response.status, 503);
    assert.equal(cancelled, true);
    assert.ok(pulls < 100);
  }
});

test('job API client safely cancels the upstream body after JSON parse abort', async () => {
  let cancelCalls = 0;
  globalThis.fetch = async () => {
    const response = new Response('{invalid json');
    response.body.cancel = async () => { cancelCalls += 1; throw new Error('cancel failed'); };
    return response;
  };
  const { listJobs } = await import('../src/lib/trading/job-api.ts');

  const response = (await listJobs('')).response;

  assert.equal(response.status, 503);
  assert.equal(cancelCalls, 1);
});

test('missing token, transport failures, oversized bodies, and invalid schemas are typed 503', async () => {
  const { listJobs } = await import('../src/lib/trading/job-api.ts');

  delete process.env.TRADING_JOB_API_TOKEN;
  assert.deepEqual(await (await listJobs('')).response.json(), {
    ok: false,
    code: 'JOB_API_UNAVAILABLE',
    message: 'Research job service is unavailable.',
  });

  process.env.TRADING_JOB_API_TOKEN = TOKEN;
  for (const upstream of [
    async () => { throw new TypeError('secret network detail'); },
    async () => new Response('x'.repeat(300_000)),
    async () => jsonResponse(envelope({ items: [{ state: 'FAKE_SUCCESS' }], limit: 50, offset: 0 })),
  ]) {
    globalThis.fetch = upstream;
    const response = (await listJobs('')).response;
    assert.equal(response.status, 503);
    assert.equal((await response.json()).code, 'JOB_API_UNAVAILABLE');
  }
});

test('list and detail accept the strict engine BACKTEST response form', async () => {
  const { getJob, listJobs } = await import('../src/lib/trading/job-api.ts');

  globalThis.fetch = async () => jsonResponse(envelope({
    items: [engineBacktestJob], limit: 50, offset: 0,
  }));
  const listed = await listJobs('');
  assert.equal(listed.response.status, 200);
  assert.deepEqual(listed.data.items[0].payload, engineBacktestPayload);

  globalThis.fetch = async () => jsonResponse(envelope({
    job: engineBacktestJob, attempts: [], events: [], artifacts: [],
  }));
  const detailed = await getJob('job_123');
  assert.equal(detailed.response.status, 200);
  assert.deepEqual(detailed.data.job.payload, engineBacktestPayload);
});

test('list and detail accept an engine BACKTEST window increasing within one millisecond', async () => {
  const payload = {
    engine_backtest: {
      ...engineBacktestPayload.engine_backtest,
      start_time: '2026-07-01T00:00:00.000001Z',
      end_time: '2026-07-01T00:00:00.000002Z',
    },
  };
  const job = { ...engineBacktestJob, payload };
  const { getJob, listJobs } = await import('../src/lib/trading/job-api.ts');

  globalThis.fetch = async () => jsonResponse(envelope({
    items: [job], limit: 50, offset: 0,
  }));
  const listed = await listJobs('');
  assert.equal(listed.response.status, 200);
  assert.deepEqual(listed.data.items[0].payload, payload);

  globalThis.fetch = async () => jsonResponse(envelope({
    job, attempts: [], events: [], artifacts: [],
  }));
  const detailed = await getJob('job_123');
  assert.equal(detailed.response.status, 200);
  assert.deepEqual(detailed.data.job.payload, payload);
});

test('list and detail reject engine BACKTEST mismatches mixtures and invalid boundaries', async () => {
  const legacy = {
    asset: 'BTC', strategy_id: 'legacy-binary-report-v1',
    date_from: null, date_to: null,
  };
  const input = engineBacktestPayload.engine_backtest;
  const invalidJobs = [
    ...['SNAPSHOT', 'DEBATE', 'REPLAY'].map((job_type) => ({
      ...engineBacktestJob, job_type,
    })),
    { ...engineBacktestJob, payload: { ...engineBacktestPayload, asset: 'BTC' } },
    { ...engineBacktestJob, payload: { ...legacy, engine_backtest: input } },
    {
      ...engineBacktestJob,
      payload: { engine_backtest: { ...input, provider: 'nautilus' } },
    },
    {
      ...engineBacktestJob,
      payload: {
        engine_backtest: {
          ...input,
          market_data: { ...input.market_data, path: '/tmp/data.jsonl' },
        },
      },
    },
    {
      ...engineBacktestJob,
      payload: {
        engine_backtest: {
          ...input,
          market_data: { ...input.market_data, artifact_id: 'not-a-uuid' },
        },
      },
    },
    {
      ...engineBacktestJob,
      payload: {
        engine_backtest: {
          ...input,
          market_data: { ...input.market_data, sha256: 'A'.repeat(64) },
        },
      },
    },
    {
      ...engineBacktestJob,
      payload: {
        engine_backtest: {
          ...input,
          market_data: { ...input.market_data, media_type: 'application/octet-stream' },
        },
      },
    },
    {
      ...engineBacktestJob,
      payload: { engine_backtest: { ...input, start_time: '2026-07-01T00:00:00+00:00' } },
    },
    {
      ...engineBacktestJob,
      payload: { engine_backtest: { ...input, start_time: '2026-07-01T00:00:00.1234567Z' } },
    },
    {
      ...engineBacktestJob,
      payload: { engine_backtest: { ...input, start_time: '2026-02-30T00:00:00Z' } },
    },
    {
      ...engineBacktestJob,
      payload: { engine_backtest: { ...input, end_time: input.start_time } },
    },
    {
      ...engineBacktestJob,
      payload: { engine_backtest: { ...input, end_time: '2026-06-30T23:59:59Z' } },
    },
    {
      ...engineBacktestJob,
      payload: {
        engine_backtest: {
          ...input,
          start_time: '2026-07-01T00:00:00.000002Z',
          end_time: '2026-07-01T00:00:00.000001Z',
        },
      },
    },
  ];
  const { getJob, listJobs } = await import('../src/lib/trading/job-api.ts');

  for (const job of invalidJobs) {
    globalThis.fetch = async () => jsonResponse(envelope({
      items: [job], limit: 50, offset: 0,
    }));
    assert.equal((await listJobs('')).response.status, 503);

    globalThis.fetch = async () => jsonResponse(envelope({
      job, attempts: [], events: [], artifacts: [],
    }));
    assert.equal((await getJob('job_123')).response.status, 503);
  }
});

test('strict response DTOs reject secret or debug fields at every upstream object boundary', async () => {
  const attempt = {
    attempt_id: 'attempt_1', attempt_number: 1, worker_id: null,
    claimed_at: null, started_at: null, finished_at: null, exit_code: null,
    termination_reason: null, artifact_count: 0,
  };
  const event = {
    event_id: 'event_1', sequence: 1, from_state: null, to_state: 'QUEUED',
    reason_code: 'ENQUEUED', actor: canonicalJob.actor, trace_id: 'trace_event', created_at: NOW,
  };
  const artifact = {
    artifact_id: 'artifact_1', attempt_id: 'attempt_1', artifact_type: 'REPORT',
    validator_id: 'validator_1', sha256: 'b'.repeat(64), size_bytes: 1, created_at: NOW,
  };
  const cases = [
    { ...envelope({ items: [canonicalJob], limit: 50, offset: 0 }), debug: 'secret-envelope' },
    envelope({ items: [{ ...canonicalJob, secret: 'secret-job' }], limit: 50, offset: 0 }),
    envelope({ items: [{ ...canonicalJob, actor: { ...canonicalJob.actor, debug: 'secret-actor' } }], limit: 50, offset: 0 }),
    envelope({ items: [canonicalJob], limit: 50, offset: 0, debug: 'secret-list' }),
    envelope({ job: canonicalJob, attempts: [{ ...attempt, secret: 'secret-attempt' }], events: [event], artifacts: [artifact] }),
    envelope({ job: canonicalJob, attempts: [attempt], events: [{ ...event, debug: 'secret-event' }], artifacts: [artifact] }),
    envelope({ job: canonicalJob, attempts: [attempt], events: [event], artifacts: [{ ...artifact, secret: 'secret-artifact' }] }),
  ];
  const { getJob, listJobs } = await import('../src/lib/trading/job-api.ts');

  for (const value of cases) {
    globalThis.fetch = async () => jsonResponse(value);
    const result = 'job' in value.data ? await getJob('job_123') : await listJobs('');
    const responseBody = await result.response.json();
    assert.equal(result.response.status, 503);
    assert.equal(JSON.stringify(responseBody).includes('secret-'), false);
  }
});

test('strict envelopes reject wrong versions, unsafe trace/time values, and extra error fields', async () => {
  const invalid = [
    { ...envelope({ items: [canonicalJob], limit: 50, offset: 0 }), schema_version: '2.0.0' },
    envelope({ items: [canonicalJob], limit: 50, offset: 0 }, `trace_${'x'.repeat(129)}`),
    { ...envelope({ items: [canonicalJob], limit: 50, offset: 0 }), generated_at: 'not-a-time' },
    {
      schema_version: '1.0.0', trace_id: 'trace_error', generated_at: NOW,
      error: { code: 'JOB_NOT_FOUND', message: 'Job was not found.', details: {}, debug: 'secret-error' },
    },
  ];
  const { listJobs } = await import('../src/lib/trading/job-api.ts');

  for (const value of invalid) {
    globalThis.fetch = async () => jsonResponse(value, 'error' in value ? 404 : 200);
    const response = (await listJobs('')).response;
    assert.equal(response.status, 503);
    assert.equal(JSON.stringify(await response.json()).includes('secret-'), false);
  }
});

test('dashboard request bodies stop streaming at the configured bound', async () => {
  const { readDashboardAction } = await import('../src/lib/trading/job-api.ts');
  let pulls = 0;
  let cancelled = false;
  const body = new ReadableStream({
    pull(controller) {
      pulls += 1;
      controller.enqueue(new Uint8Array(8_192));
      if (pulls === 100) controller.close();
    },
    cancel() { cancelled = true; },
  });
  const request = new Request('https://dashboard.test/api/trading/jobs', {
    method: 'POST', body, duplex: 'half',
  });
  assert.equal(await readDashboardAction(request), null);
  assert.equal(cancelled, true);
  assert.ok(pulls < 100);
});

test('list/detail BFF routes fail closed for missing sessions and proxy canonical data', async () => {
  globalThis.fetch = async (input) => String(input).endsWith('/job_123')
    ? jsonResponse(envelope({ job: canonicalJob, attempts: [], events: [], artifacts: [] }))
    : jsonResponse(envelope({ items: [canonicalJob], limit: 20, offset: 0 }));
  const listRoute = await import('../src/app/api/trading/jobs/route.ts');
  const detailRoute = await import('../src/app/api/trading/jobs/[id]/route.ts');

  const unauthenticated = await listRoute.GET(new Request('https://dashboard.test/api/trading/jobs'));
  assert.equal(unauthenticated.status, 401);

  const listed = await listRoute.GET(await sessionRequest('https://dashboard.test/api/trading/jobs?limit=20', 'reader'));
  assert.equal(listed.status, 200);
  assert.equal((await listed.json()).data.items[0].job_id, 'job_123');

  const detailed = await detailRoute.GET(
    await sessionRequest('https://dashboard.test/api/trading/jobs/job_123', 'reader'),
    { params: Promise.resolve({ id: 'job_123' }) },
  );
  assert.equal((await detailed.json()).data.job.state, 'QUEUED');
});

test('create/cancel require operator and leave actor attribution to upstream auth', async () => {
  const calls = [];
  globalThis.fetch = async (input, init) => {
    calls.push({ input: String(input), body: init.body && JSON.parse(init.body) });
    return String(input).endsWith('/cancel')
      ? jsonResponse(envelope({ ...canonicalJob, state: 'CANCELLED' }, 'trace_cancel'), 200, { 'x-trace-id': 'trace_cancel' })
      : jsonResponse(envelope({ outcome: 'ENQUEUED', job: canonicalJob }, 'trace_create'), 201, { 'x-trace-id': 'trace_create' });
  };
  const jobsRoute = await import('../src/app/api/trading/jobs/route.ts');
  const cancelRoute = await import('../src/app/api/trading/jobs/[id]/cancel/route.ts');
  const payload = JSON.stringify({ action: 'snapshot', operationId: OPERATION_ID });

  const reader = await jobsRoute.POST(await sessionRequest('https://dashboard.test/api/trading/jobs', 'reader', { method: 'POST', body: payload }));
  assert.equal(reader.status, 403);

  const created = await jobsRoute.POST(await sessionRequest('https://dashboard.test/api/trading/jobs', 'operator', { method: 'POST', body: payload }));
  assert.equal(created.status, 201);
  assert.equal(created.headers.get('x-trace-id'), 'trace_create');
  assert.equal('actor' in calls[0].body, false);
  assert.deepEqual(calls[0].body.payload, { scope: 'default', requested_as_of: null });
  assert.equal(calls[0].body.idempotency_key, `dashboard:snapshot:${OPERATION_ID}`);

  const cancelled = await cancelRoute.POST(
    await sessionRequest('https://dashboard.test/api/trading/jobs/job_123/cancel', 'operator', { method: 'POST' }),
    { params: Promise.resolve({ id: 'job_123' }) },
  );
  assert.equal(cancelled.headers.get('x-trace-id'), 'trace_cancel');
  assert.deepEqual(calls[1].body, {});
});

test('jobs POST rejects empty, implicit, missing, or malformed operation identity without upstream', async () => {
  let calls = 0;
  globalThis.fetch = async () => { calls += 1; return jsonResponse(envelope({ outcome: 'ENQUEUED', job: canonicalJob }), 201); };
  const jobsRoute = await import('../src/app/api/trading/jobs/route.ts');

  for (const body of [
    '',
    '{}',
    JSON.stringify({ action: 'snapshot' }),
    JSON.stringify({ action: 'snapshot', operationId: 'ABCDEF' }),
    JSON.stringify({ action: 'snapshot', operationId: `${OPERATION_ID}0` }),
  ]) {
    const response = await jobsRoute.POST(await sessionRequest('https://dashboard.test/api/trading/jobs', 'operator', {
      method: 'POST', body,
    }));
    assert.ok(response.status === 400 || response.status === 422);
  }
  assert.equal(calls, 0);
});

test('deprecated run POST rejects empty-body snapshot compatibility', async () => {
  let calls = 0;
  globalThis.fetch = async () => { calls += 1; return jsonResponse({}); };
  const runRoute = await import('../src/app/api/trading/run/route.ts');

  const response = await runRoute.POST(await sessionRequest('https://dashboard.test/api/trading/run', 'operator', {
    method: 'POST', body: '',
  }));

  assert.equal(response.status, 400);
  assert.equal(calls, 0);
});

test('dropped-response replay derives the same upstream idempotency key', async () => {
  const bodies = [];
  let calls = 0;
  globalThis.fetch = async (_input, init) => {
    calls += 1;
    bodies.push(JSON.parse(init.body));
    if (calls === 1) throw new TypeError('upstream response dropped');
    return jsonResponse(envelope({ outcome: 'ENQUEUED', job: canonicalJob }), 201);
  };
  const jobsRoute = await import('../src/app/api/trading/jobs/route.ts');
  const body = JSON.stringify({ action: 'snapshot', operationId: OPERATION_ID });

  const first = await jobsRoute.POST(await sessionRequest('https://dashboard.test/api/trading/jobs', 'operator', { method: 'POST', body }));
  const retry = await jobsRoute.POST(await sessionRequest('https://dashboard.test/api/trading/jobs', 'operator', { method: 'POST', body }));

  assert.equal(first.status, 503);
  assert.equal(retry.status, 201);
  assert.equal(bodies.length, 2);
  assert.equal(bodies[0].idempotency_key, `dashboard:snapshot:${OPERATION_ID}`);
  assert.deepEqual(bodies[0], bodies[1]);
});

test('disabled command flag is read-only and never contacts Job API for mutations', async () => {
  process.env.TRADING_JOB_COMMANDS_ENABLED = '0';
  let calls = 0;
  globalThis.fetch = async () => { calls += 1; return jsonResponse({}); };
  const jobsRoute = await import('../src/app/api/trading/jobs/route.ts');
  const response = await jobsRoute.POST(await sessionRequest('https://dashboard.test/api/trading/jobs', 'operator', {
    method: 'POST', body: JSON.stringify({ action: 'snapshot', operationId: OPERATION_ID }),
  }));
  assert.equal(response.status, 503);
  assert.equal((await response.json()).code, 'JOB_COMMANDS_DISABLED');
  assert.equal(calls, 0);
});

test('isolated mutation handler leaves the active append-only audit unchanged', async () => {
  const protectedAuditPath = path.join(testResearchRoot, 'protected-audit.jsonl');
  fs.writeFileSync(protectedAuditPath, '{"event":"protected"}\n', { mode: 0o600 });
  const beforeStat = fs.statSync(protectedAuditPath);
  const beforeHash = createHash('sha256').update(fs.readFileSync(protectedAuditPath)).digest('hex');
  process.env.TRADING_JOB_COMMANDS_ENABLED = '0';
  const jobsRoute = await import('../src/app/api/trading/jobs/route.ts');

  const response = await jobsRoute.POST(await sessionRequest('https://dashboard.test/api/trading/jobs', 'operator', {
    method: 'POST', body: JSON.stringify({ action: 'snapshot', operationId: OPERATION_ID }),
  }));

  assert.equal(response.status, 503);
  const afterStat = fs.statSync(protectedAuditPath);
  const afterHash = createHash('sha256').update(fs.readFileSync(protectedAuditPath)).digest('hex');
  assert.equal(afterHash, beforeHash);
  assert.equal(afterStat.size, beforeStat.size);
  assert.equal(afterStat.mtimeMs, beforeStat.mtimeMs);
  assert.equal(afterStat.mode, beforeStat.mode);
});

test('deprecated run route only enqueues a fixed snapshot and does not touch run_status.json', async () => {
  const temp = await mkdtemp(path.join(os.tmpdir(), 'trading-run-status-'));
  const legacy = path.join(temp, 'run_status.json');
  await writeFile(legacy, '{"status":"legacy"}');
  const before = await stat(legacy);
  const beforeHash = createHash('sha256').update(fs.readFileSync(legacy)).digest('hex');
  process.env.TRADING_DATA_ROOT = temp;

  let body;
  globalThis.fetch = async (_input, init) => {
    body = JSON.parse(init.body);
    return jsonResponse(envelope({ outcome: 'ENQUEUED', job: canonicalJob }), 201);
  };
  const builtinFs = require('node:fs');
  const builtinChildProcess = require('node:child_process');
  const originals = {
    readFileSync: builtinFs.readFileSync,
    writeFileSync: builtinFs.writeFileSync,
    renameSync: builtinFs.renameSync,
    spawn: builtinChildProcess.spawn,
  };
  const calls = { read: 0, write: 0, rename: 0, spawn: 0 };
  builtinFs.readFileSync = (...args) => { calls.read += 1; return originals.readFileSync(...args); };
  builtinFs.writeFileSync = (...args) => { calls.write += 1; return originals.writeFileSync(...args); };
  builtinFs.renameSync = (...args) => { calls.rename += 1; return originals.renameSync(...args); };
  builtinChildProcess.spawn = (...args) => { calls.spawn += 1; return originals.spawn(...args); };
  syncBuiltinESMExports();
  let response;
  try {
    const runRoute = await import('../src/app/api/trading/run/route.ts');
    response = await runRoute.POST(await sessionRequest('https://dashboard.test/api/trading/run', 'operator', {
      method: 'POST', body: JSON.stringify({ action: 'snapshot', operationId: OPERATION_ID }),
    }));
  } finally {
    Object.assign(builtinFs, {
      readFileSync: originals.readFileSync,
      writeFileSync: originals.writeFileSync,
      renameSync: originals.renameSync,
    });
    builtinChildProcess.spawn = originals.spawn;
    syncBuiltinESMExports();
  }
  assert.equal(response.status, 201);
  assert.equal(body.job_type, 'SNAPSHOT');
  assert.deepEqual(body.payload, { scope: 'default', requested_as_of: null });
  assert.equal('command' in body, false);

  const after = await stat(legacy);
  const afterHash = createHash('sha256').update(fs.readFileSync(legacy)).digest('hex');
  assert.equal(afterHash, beforeHash);
  assert.equal(after.mtimeMs, before.mtimeMs);
  assert.deepEqual(calls, { read: 0, write: 1, rename: 1, spawn: 0 });
  const auditRows = fs.readFileSync(path.join(temp, 'memory', 'dashboard_mutation_audit.jsonl'), 'utf8')
    .trim().split('\n').map(JSON.parse);
  assert.deepEqual(auditRows.map((event) => event.action), ['pipeline.run']);
  assert.deepEqual(auditRows.map((event) => event.authorization_outcome), ['GRANTED']);

  const source = fs.readFileSync(path.join(ROOT, 'src/app/api/trading/run/route.ts'), 'utf8');
  assert.doesNotMatch(source, /child_process|node:child_process|spawn\s*\(|\.venv|python/i);
  assert.doesNotMatch(source, /from ['"](?:node:)?fs|run_status|readFile|writeFile|rename/i);
});

test('pipeline status is canonical job data and returns typed unavailable without filesystem fallback', async () => {
  globalThis.fetch = async () => jsonResponse(envelope({ items: [canonicalJob], limit: 10, offset: 0 }));
  const route = await import('../src/app/api/trading/pipeline-status/route.ts');
  const response = await route.GET(await sessionRequest('https://dashboard.test/api/trading/pipeline-status', 'reader'));
  assert.equal(response.status, 200);
  assert.equal((await response.json()).data.items[0].state, 'QUEUED');

  globalThis.fetch = async () => { throw new Error('down'); };
  const unavailable = await route.GET(await sessionRequest('https://dashboard.test/api/trading/pipeline-status', 'reader'));
  assert.equal(unavailable.status, 503);
  assert.equal((await unavailable.json()).code, 'JOB_API_UNAVAILABLE');

  const source = fs.readFileSync(path.join(ROOT, 'src/app/api/trading/pipeline-status/route.ts'), 'utf8');
  assert.doesNotMatch(source, /from ['"](?:node:)?fs|run_status|readdir|statSync|readFile/i);
});

test('the server token name and value are absent from client component sources', () => {
  for (const name of ['run-pipeline-button.tsx', 'pipeline-status.tsx']) {
    const source = fs.readFileSync(path.join(ROOT, 'src/components/trading', name), 'utf8');
    assert.doesNotMatch(source, /TRADING_JOB_API_TOKEN|job-api-token-that-must-never-leak/);
  }
});

test('job UI renders canonical identity, state, timing, attempts, event, result and reason with bounded polling', () => {
  const run = fs.readFileSync(path.join(ROOT, 'src/components/trading/run-pipeline-button.tsx'), 'utf8');
  const status = fs.readFileSync(path.join(ROOT, 'src/components/trading/pipeline-status.tsx'), 'utf8');
  const combined = `${run}\n${status}`;
  for (const field of ['job_id', 'state', 'requested_at', 'attempt_count', 'reason_code', 'result_hash', 'events', 'attempts']) {
    assert.match(combined, new RegExp(field));
  }
  for (const state of ['QUEUED', 'CLAIMED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'BLOCKED', 'TIMED_OUT', 'CANCEL_REQUESTED', 'CANCELLED']) {
    assert.match(combined, new RegExp(state));
  }
  assert.match(combined, /MAX_POLL_ATTEMPTS|POLL_LIMIT/);
  assert.doesNotMatch(combined, /exit_code\s*===\s*0|status:\s*['"]idle['"]|status:\s*['"]running['"]/);
});
