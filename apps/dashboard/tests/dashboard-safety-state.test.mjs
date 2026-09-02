import assert from 'node:assert/strict';
import test from 'node:test';

import * as settingsStateModule from '../src/lib/trading/settings-state.ts';

import {
  createPipelineCommand,
  createKillSwitchIntent,
  killSwitchRequest,
  submitPipelineCommand,
  validateKillSwitchIntent,
} from '../src/lib/trading/quick-actions-state.ts';
import {
  exchangeConfigurationPresentation,
  parseExchangeConfigPayload,
} from '../src/lib/trading/settings-state.ts';
import {
  INITIAL_DATA_SOURCES_STATE,
  UNAVAILABLE_DATA_SOURCES_STATE,
  loadDataSourcesState,
  summarizeDataSources,
} from '../src/lib/trading/data-source-state.ts';
import {
  dashboardReportAssets,
  summarizeAssetRisk,
} from '../src/lib/trading/dashboard-report-state.ts';

const NOW = '2026-07-16T12:00:00Z';

function jobFor(command, overrides = {}) {
  const snapshot = command.action === 'snapshot';
  return {
    job_id: snapshot ? 'job_snapshot' : 'job_debate',
    job_type: snapshot ? 'SNAPSHOT' : 'DEBATE',
    state: 'QUEUED',
    payload: snapshot
      ? { scope: 'default', requested_as_of: null }
      : { asset: command.asset, horizon: '1d' },
    payload_fingerprint: 'a'.repeat(64),
    actor: { actor_type: 'OPERATOR', actor_id: 'dashboard-operator' },
    priority: 3,
    requested_at: NOW,
    updated_at: NOW,
    attempt_count: 0,
    reason_code: 'ENQUEUED',
    result_hash: null,
    ...overrides,
  };
}

function successEnvelope(command, overrides = {}) {
  return {
    schema_version: '1.0.0',
    trace_id: 'trace_dashboard',
    generated_at: NOW,
    data: {
      outcome: 'ENQUEUED',
      job: jobFor(command),
      ...overrides,
    },
  };
}

test('pipeline commands are distinct, typed, and serialized exactly for the run route', async () => {
  const snapshot = createPipelineCommand('snapshot');
  const debate = createPipelineCommand('debate', 'btc');
  assert.match(snapshot.operationId, /^[0-9a-f]{32}$/);
  assert.match(debate.operationId, /^[0-9a-f]{32}$/);
  assert.deepEqual(snapshot, { action: 'snapshot', operationId: snapshot.operationId });
  assert.deepEqual(debate, { action: 'debate', asset: 'BTC', operationId: debate.operationId });
  assert.equal(createPipelineCommand('debate', 'NOT_APPROVED'), null);

  const requests = [];
  const fetcher = async (input, init) => {
    const body = JSON.parse(init.body);
    requests.push({ input, init, body });
    return Response.json(successEnvelope(body), { status: 201 });
  };

  const snapshotResult = await submitPipelineCommand(fetcher, snapshot);
  const debateResult = await submitPipelineCommand(fetcher, debate);

  assert.deepEqual(requests.map(({ input, init, body }) => ({
    input,
    method: init.method,
    contentType: init.headers['Content-Type'],
    body,
  })), [
    {
      input: '/api/trading/run',
      method: 'POST',
      contentType: 'application/json',
      body: { action: 'snapshot', operationId: snapshot.operationId },
    },
    {
      input: '/api/trading/run',
      method: 'POST',
      contentType: 'application/json',
      body: { action: 'debate', asset: 'BTC', operationId: debate.operationId },
    },
  ]);
  assert.deepEqual(snapshotResult, {
    outcome: 'ENQUEUED',
    jobId: 'job_snapshot',
    jobType: 'SNAPSHOT',
  });
  assert.deepEqual(debateResult, {
    outcome: 'ENQUEUED',
    jobId: 'job_debate',
    jobType: 'DEBATE',
  });
});

test('a dropped-response retry preserves one client operation identity', async () => {
  const command = createPipelineCommand('snapshot');
  assert.notEqual(command, null);
  const requests = [];
  let calls = 0;
  const fetcher = async (_input, init) => {
    calls += 1;
    requests.push(JSON.parse(init.body));
    if (calls === 1) throw new TypeError('response dropped');
    return Response.json(successEnvelope(command), { status: 201 });
  };

  const first = await submitPipelineCommand(fetcher, command);
  const second = await submitPipelineCommand(fetcher, command);

  assert.equal(first, null);
  assert.equal(second.outcome, 'ENQUEUED');
  assert.equal(requests.length, 2);
  assert.deepEqual(requests[0], requests[1]);
  assert.match(requests[0].operationId, /^[0-9a-f]{32}$/);
});

test('pipeline submission rejects non-OK, null, malformed, and mismatched 2xx responses', async (t) => {
  const command = createPipelineCommand('debate', 'BTC');
  assert.notEqual(command, null);

  const invalidResponses = [
    ['non-OK', new Response(null, { status: 503 })],
    ['null', Response.json(null, { status: 201 })],
    ['missing envelope field', Response.json({
      schema_version: '1.0.0', trace_id: 'trace', generated_at: NOW,
    }, { status: 201 })],
    ['extra envelope field', Response.json({
      ...successEnvelope(command), debug: true,
    }, { status: 201 })],
    ['mismatched job type', Response.json(successEnvelope(command, {
      job: jobFor(command, {
        job_type: 'SNAPSHOT',
        payload: { scope: 'default', requested_as_of: null },
      }),
    }), { status: 201 })],
    ['mismatched payload', Response.json(successEnvelope(command, {
      job: jobFor(command, { payload: { asset: 'ETH', horizon: '1d' } }),
    }), { status: 201 })],
  ];

  for (const [name, response] of invalidResponses) {
    await t.test(name, async () => {
      assert.equal(await submitPipelineCommand(async () => response, command), null);
    });
  }
});

test('kill-switch intent binds authority revision and rejects an ABA state cycle', () => {
  const halt = createKillSwitchIntent('INACTIVE', 1);
  const resume = createKillSwitchIntent('ACTIVE', 2);

  assert.deepEqual(halt, {
    observedState: 'INACTIVE',
    observedRevision: 1,
    action: 'on',
  });
  assert.deepEqual(resume, {
    observedState: 'ACTIVE',
    observedRevision: 2,
    action: 'off',
  });
  assert.equal(createKillSwitchIntent('UNKNOWN', 1), null);
  assert.deepEqual(validateKillSwitchIntent(halt, 'INACTIVE', 1), halt);
  assert.equal(validateKillSwitchIntent(halt, 'ACTIVE', 2), null);
  assert.equal(validateKillSwitchIntent(halt, 'INACTIVE', 3), null);
  assert.equal(validateKillSwitchIntent(halt, 'UNKNOWN', 3), null);
  assert.deepEqual(killSwitchRequest(halt, 'INACTIVE', 1, ' volatility '), {
    action: 'on',
    reason: 'volatility',
  });
  assert.equal(killSwitchRequest(halt, 'ACTIVE', 2, 'volatility'), null);
  assert.equal(killSwitchRequest(halt, 'INACTIVE', 3, 'volatility'), null);
  assert.deepEqual(killSwitchRequest(resume, 'ACTIVE', 2, ''), { action: 'off' });
});

test('exchange configuration preserves and presents authoritative configured false', () => {
  const parsed = parseExchangeConfigPayload({
    exchanges: {
      binance: { configured: true },
      coinbase: { configured: false },
    },
  });
  assert.deepEqual(parsed, {
    binance: { configured: true },
    coinbase: { configured: false },
  });
  assert.deepEqual(exchangeConfigurationPresentation(parsed.binance), {
    label: 'configured',
    configured: true,
  });
  assert.deepEqual(exchangeConfigurationPresentation(parsed.coinbase), {
    label: 'not configured',
    configured: false,
  });
  assert.equal(parseExchangeConfigPayload({
    exchanges: { coinbase: { configured: false, debug: true } },
  }), null);

});

test('settings operator status stays unknown unless canonical state is authoritative', () => {
  assert.equal(typeof settingsStateModule.settingsOperatorStatus, 'function');

  assert.deepEqual(settingsStateModule.settingsOperatorStatus({
    availability: 'UNAVAILABLE',
    mode: 'UNKNOWN',
    liveExecutionEnabled: null,
    liveTradingApproved: null,
  }), {
    availability: 'UNAVAILABLE',
    mode: 'UNKNOWN',
    liveExecutionEnabled: 'UNKNOWN',
    liveTradingApproved: 'UNKNOWN',
  });
  assert.deepEqual(settingsStateModule.settingsOperatorStatus({
    availability: 'AVAILABLE',
    mode: 'PAPER',
    liveExecutionEnabled: false,
    liveTradingApproved: false,
  }), {
    availability: 'AVAILABLE',
    mode: 'PAPER',
    liveExecutionEnabled: 'DISABLED',
    liveTradingApproved: 'NOT_APPROVED',
  });
});

test('settings agents distinguish unavailable data from authoritative empty data', async () => {
  assert.equal(typeof settingsStateModule.loadAgentsState, 'function');

  assert.deepEqual(settingsStateModule.summarizeAgentsState(
    settingsStateModule.INITIAL_AGENTS_STATE,
  ), {
    availability: 'LOADING',
    agents: null,
    count: null,
  });
  assert.deepEqual(
    await settingsStateModule.loadAgentsState(async () => new Response(null, { status: 503 })),
    settingsStateModule.UNAVAILABLE_AGENTS_STATE,
  );
  assert.deepEqual(
    await settingsStateModule.loadAgentsState(async () => Response.json(null)),
    settingsStateModule.UNAVAILABLE_AGENTS_STATE,
  );

  const authoritativeEmpty = await settingsStateModule.loadAgentsState(
    async () => Response.json([]),
  );
  assert.deepEqual(settingsStateModule.summarizeAgentsState(authoritativeEmpty), {
    availability: 'AVAILABLE',
    agents: [],
    count: 0,
  });
});

test('settings costs distinguish unavailable data from authoritative zero totals', async () => {
  assert.equal(typeof settingsStateModule.loadCostsState, 'function');

  assert.deepEqual(
    await settingsStateModule.loadCostsState(async () => new Response(null, { status: 503 })),
    settingsStateModule.UNAVAILABLE_COSTS_STATE,
  );
  assert.deepEqual(
    await settingsStateModule.loadCostsState(async () => Response.json(null)),
    settingsStateModule.UNAVAILABLE_COSTS_STATE,
  );

  const zeroCosts = {
    summary: {
      totalSessions: 0,
      totalLLMCalls: 0,
      totalToolCalls: 0,
      estimatedCost: 0,
      optimizerTokensSaved: 0,
      optimizerCostSaved: 0,
      evidenceQuality: 'EXACT',
      note: 'Canonical zero totals',
    },
    sessions: [],
    costModel: null,
    efficiency: {
      avgLLMCallsPerSession: null,
      avgCostPerSession: null,
      avgToolCallsPerLLM: null,
    },
  };
  const authoritativeZero = await settingsStateModule.loadCostsState(
    async () => Response.json(zeroCosts),
  );
  assert.deepEqual(authoritativeZero, {
    availability: 'AVAILABLE',
    data: zeroCosts,
  });
});

test('data-source failures stay unavailable with unknown counts instead of an empty healthy list', async (t) => {
  assert.deepEqual(summarizeDataSources(INITIAL_DATA_SOURCES_STATE), {
    availability: 'LOADING',
    sources: null,
    counts: null,
  });
  assert.deepEqual(summarizeDataSources(UNAVAILABLE_DATA_SOURCES_STATE), {
    availability: 'UNAVAILABLE',
    sources: null,
    counts: null,
  });

  const validSource = {
    id: 'canonical-market-data',
    name: 'Canonical Market Data',
    status: 'unknown',
    latency_ms: 0,
    rate_limit_remaining: 0,
    last_check: null,
    error_message: null,
  };
  const missing = { ...validSource };
  delete missing.error_message;
  const cases = [
    ['503', async () => new Response(null, { status: 503 })],
    ['null', async () => Response.json(null)],
    ['object instead of array', async () => Response.json({ sources: [] })],
    ['missing field', async () => Response.json([missing])],
    ['extra field', async () => Response.json([{ ...validSource, debug: true }])],
    ['invalid status', async () => Response.json([{ ...validSource, status: 'healthy' }])],
  ];
  for (const [name, fetcher] of cases) {
    await t.test(name, async () => {
      const state = await loadDataSourcesState(fetcher);
      assert.deepEqual(state, UNAVAILABLE_DATA_SOURCES_STATE);
      assert.equal(summarizeDataSources(state).counts, null);
    });
  }

  const available = await loadDataSourcesState(async () => Response.json([validSource]));
  assert.deepEqual(summarizeDataSources(available), {
    availability: 'AVAILABLE',
    sources: [{
      id: 'canonical-market-data',
      name: 'Canonical Market Data',
      status: 'unknown',
      latency: 0,
      rateLimitRemaining: 0,
      lastUpdate: null,
      error: null,
    }],
    counts: { active: 0, error: 0, unknown: 1 },
  });

  const authoritativeEmpty = await loadDataSourcesState(async () => Response.json([]));
  assert.deepEqual(summarizeDataSources(authoritativeEmpty).counts, {
    active: 0,
    error: 0,
    unknown: 0,
  });
});

test('an OK data source without freshness evidence remains unknown', async () => {
  const source = {
    id: 'canonical-market-data',
    name: 'Canonical Market Data',
    status: 'ok',
    latency_ms: 0,
    rate_limit_remaining: 0,
    last_check: null,
    error_message: null,
  };

  const withoutFreshness = await loadDataSourcesState(async () => Response.json([source]));
  assert.deepEqual(summarizeDataSources(withoutFreshness), {
    availability: 'AVAILABLE',
    sources: [{
      id: 'canonical-market-data',
      name: 'Canonical Market Data',
      status: 'unknown',
      latency: 0,
      rateLimitRemaining: 0,
      lastUpdate: null,
      error: null,
    }],
    counts: { active: 0, error: 0, unknown: 1 },
  });

  const withFreshness = await loadDataSourcesState(async () => Response.json([{
    ...source,
    last_check: NOW,
  }]));
  assert.deepEqual(summarizeDataSources(withFreshness).counts, {
    active: 1,
    error: 0,
    unknown: 0,
  });
});

test('asset risk only reports a green zero when every asset has valid risk evidence', () => {
  assert.equal(dashboardReportAssets(null), null);
  const emptyReport = { assets: [] };
  assert.equal(dashboardReportAssets(emptyReport), emptyReport.assets);
  assert.deepEqual(summarizeAssetRisk(null), {
    availability: 'UNKNOWN',
    highRisk: null,
    tracked: null,
  });
  assert.deepEqual(summarizeAssetRisk([
    { risk_assessment: { risk_level: 'LOW' } },
    { risk_assessment: null },
  ]), {
    availability: 'UNKNOWN',
    highRisk: null,
    tracked: null,
  });
  assert.deepEqual(summarizeAssetRisk([
    { risk_assessment: { risk_level: 'LOW' } },
    { risk_assessment: { risk_level: 'MEDIUM' } },
  ]), {
    availability: 'AVAILABLE',
    highRisk: 0,
    tracked: 2,
  });
  assert.deepEqual(summarizeAssetRisk([
    { risk_assessment: { risk_level: 'HIGH' } },
    { risk_assessment: { risk_level: 'CRITICAL' } },
  ]), {
    availability: 'AVAILABLE',
    highRisk: 2,
    tracked: 2,
  });
});
