import assert from 'node:assert/strict';
import test from 'node:test';

import * as operatorStateModule from '../src/lib/trading/operator-state.ts';
import {
  createKillSwitchIntent,
  validateKillSwitchIntent,
} from '../src/lib/trading/quick-actions-state.ts';

import {
  INITIAL_OPERATOR_STATE,
  INITIAL_EXECUTION_STATE,
  INITIAL_EXCHANGE_STATUS_STATE,
  INITIAL_SERVICE_STATE,
  UNAVAILABLE_EXECUTION_STATE,
  UNAVAILABLE_EXCHANGE_STATUS_STATE,
  UNAVAILABLE_OPERATOR_STATE,
  UNAVAILABLE_SERVICE_STATE,
  createLatestStateCoordinator,
  exchangeStatusMatchesOperator,
  executionMatchesOperator,
  loadExchangeStatusState,
  loadExecutionState,
  loadOperatorState,
  loadServiceState,
  parseExchangeStatusPayload,
  parseExecutionPayload,
  parseServicePayload,
  projectOperatorState,
} from '../src/lib/trading/operator-state.ts';

function authoritativeMeta(overrides = {}) {
  return {
    service: 'legacy-trading-dashboard',
    git_commit: '0123456789abcdef',
    build_time: '2026-07-16T12:00:00Z',
    deployment_id: 'dashboard-test',
    control_api_available: true,
    requested_mode: 'paper',
    effective_mode: 'paper',
    execution_capability: 'NON_LIVE',
    live_execution_enabled: false,
    live_trading_approved: false,
    kill_switch_state: 'INACTIVE',
    canonical_deployment_id: 'control-test',
    ...overrides,
  };
}

function unavailableMeta() {
  return {
    service: 'legacy-trading-dashboard',
    git_commit: 'unknown',
    build_time: 'unknown',
    deployment_id: 'dashboard-test',
    control_api_available: false,
    requested_mode: null,
    effective_mode: null,
    execution_capability: null,
    live_execution_enabled: false,
    live_trading_approved: false,
    kill_switch_state: 'UNKNOWN',
    canonical_deployment_id: null,
  };
}

test('operator state is unknown-first and unavailable states never synthesize safety truth', () => {
  assert.deepEqual(INITIAL_OPERATOR_STATE, {
    availability: 'LOADING',
    health: 'UNKNOWN',
    requestedMode: 'UNKNOWN',
    mode: 'UNKNOWN',
    executionCapability: 'UNKNOWN',
    liveExecutionEnabled: null,
    liveTradingApproved: null,
    killSwitchState: 'UNKNOWN',
    deployment: null,
    metrics: null,
    controlsEnabled: false,
  });
  assert.deepEqual(UNAVAILABLE_OPERATOR_STATE, {
    ...INITIAL_OPERATOR_STATE,
    availability: 'UNAVAILABLE',
  });
});

test('operator meta projection is strict and fail-closed', async (t) => {
  const missing = authoritativeMeta();
  delete missing.canonical_deployment_id;

  const cases = [
    ['null body', null],
    ['route reports no canonical authority', unavailableMeta()],
    ['missing field', missing],
    ['extra field', { ...authoritativeMeta(), debug: true }],
    ['invalid mode', authoritativeMeta({ effective_mode: 'simulated' })],
  ];

  for (const [name, body] of cases) {
    await t.test(name, () => {
      assert.deepEqual(projectOperatorState(body), UNAVAILABLE_OPERATOR_STATE);
    });
  }
});

test('an explicit canonical unknown remains visible and disables controls', () => {
  const state = projectOperatorState(authoritativeMeta({ kill_switch_state: 'UNKNOWN' }));

  assert.equal(state.availability, 'AVAILABLE');
  assert.equal(state.health, 'UNKNOWN');
  assert.equal(state.killSwitchState, 'UNKNOWN');
  assert.equal(state.controlsEnabled, false);
});

test('controls remain disabled when requested mode is not paper even if effective mode is paper', () => {
  const state = projectOperatorState(authoritativeMeta({
    requested_mode: 'live',
    effective_mode: 'paper',
    execution_capability: 'LIVE_BLOCKED',
  }));

  assert.equal(state.availability, 'AVAILABLE');
  assert.equal(state.requestedMode, 'LIVE');
  assert.equal(state.mode, 'PAPER');
  assert.equal(state.controlsEnabled, false);
});

test('controls require exact NON_LIVE execution capability', () => {
  assert.equal(
    projectOperatorState(authoritativeMeta({ execution_capability: 'NON_LIVE' })).controlsEnabled,
    true,
  );
  assert.equal(
    projectOperatorState(authoritativeMeta({ execution_capability: 'LIVE_BLOCKED' })).controlsEnabled,
    false,
  );
  assert.equal(
    projectOperatorState(authoritativeMeta({ execution_capability: 'LIVE_AVAILABLE' })).controlsEnabled,
    false,
  );
  assert.equal(UNAVAILABLE_OPERATOR_STATE.controlsEnabled, false);
});

test('control enablement is fail-closed across the mode and capability matrix', () => {
  const modes = ['paper', 'dryrun', 'live'];
  const capabilities = ['NON_LIVE', 'LIVE_BLOCKED', 'LIVE_AVAILABLE'];
  for (const requestedMode of modes) {
    for (const effectiveMode of modes) {
      for (const capability of capabilities) {
        const state = projectOperatorState(authoritativeMeta({
          requested_mode: requestedMode,
          effective_mode: effectiveMode,
          execution_capability: capability,
        }));
        assert.equal(
          state.controlsEnabled,
          requestedMode === 'paper'
            && effectiveMode === 'paper'
            && capability === 'NON_LIVE',
          `${requestedMode}/${effectiveMode}/${capability}`,
        );
      }
    }
  }
});

test('operator state loader maps HTTP, JSON, and timeout failures to unavailable', async (t) => {
  const cases = [
    ['401', async () => new Response(null, { status: 401 })],
    ['503', async () => new Response(null, { status: 503 })],
    ['invalid JSON', async () => new Response('{not-json', {
      status: 200,
      headers: { 'content-type': 'application/json' },
    })],
    ['JSON null', async () => Response.json(null)],
  ];

  for (const [name, fetcher] of cases) {
    await t.test(name, async () => {
      assert.deepEqual(await loadOperatorState(fetcher), UNAVAILABLE_OPERATOR_STATE);
    });
  }

  await t.test('timeout', async () => {
    let aborted = false;
    const fetcher = async (_input, init) => new Promise((_resolve, reject) => {
      init.signal.addEventListener('abort', () => {
        aborted = true;
        reject(new DOMException('Aborted', 'AbortError'));
      }, { once: true });
    });

    assert.deepEqual(
      await loadOperatorState(fetcher, { timeoutMs: 20 }),
      UNAVAILABLE_OPERATOR_STATE,
    );
    assert.equal(aborted, true);
  });
});

test('latest-state coordination rejects stale completions and suspends polling while invalidated', async () => {
  const pending = [];
  const published = [];
  const coordinator = createLatestStateCoordinator({
    load: async () => new Promise((resolve) => pending.push(resolve)),
    publish: (value) => published.push(value),
  });

  const older = coordinator.run();
  const newer = coordinator.run();
  pending[1]('UNAVAILABLE');
  await newer;
  pending[0]('AVAILABLE');
  await older;
  assert.deepEqual(published, ['UNAVAILABLE']);

  const stale = coordinator.run();
  coordinator.invalidate('UNKNOWN');
  pending[2]('AVAILABLE');
  await stale;
  assert.deepEqual(published, ['UNAVAILABLE', 'UNKNOWN']);

  await coordinator.run();
  assert.equal(pending.length, 3);

  const resumed = coordinator.resume();
  assert.equal(pending.length, 4);
  pending[3]('ACTIVE');
  await resumed;
  assert.deepEqual(published, ['UNAVAILABLE', 'UNKNOWN', 'ACTIVE']);
});

test('safety revision rejects ABA intents without churning on identical authority polls', () => {
  assert.equal(typeof operatorStateModule.advanceSafetyAuthorityState, 'function');

  const inactive = projectOperatorState(authoritativeMeta({
    kill_switch_state: 'INACTIVE',
  }));
  const sameInactive = projectOperatorState(authoritativeMeta({
    kill_switch_state: 'INACTIVE',
  }));
  const active = projectOperatorState(authoritativeMeta({
    kill_switch_state: 'ACTIVE',
  }));

  let snapshot = operatorStateModule.advanceSafetyAuthorityState(
    operatorStateModule.INITIAL_SAFETY_AUTHORITY_STATE,
    inactive,
  );
  assert.equal(snapshot.safetyRevision, 1);
  const intent = createKillSwitchIntent('INACTIVE', snapshot.safetyRevision);
  assert.notEqual(intent, null);

  snapshot = operatorStateModule.advanceSafetyAuthorityState(snapshot, sameInactive);
  assert.equal(snapshot.safetyRevision, 1);
  assert.deepEqual(
    validateKillSwitchIntent(intent, snapshot.state.killSwitchState, snapshot.safetyRevision),
    intent,
  );

  snapshot = operatorStateModule.advanceSafetyAuthorityState(snapshot, active);
  assert.equal(snapshot.safetyRevision, 2);
  snapshot = operatorStateModule.advanceSafetyAuthorityState(snapshot, inactive);
  assert.equal(snapshot.safetyRevision, 3);
  assert.equal(snapshot.state.killSwitchState, 'INACTIVE');
  assert.equal(
    validateKillSwitchIntent(intent, snapshot.state.killSwitchState, snapshot.safetyRevision),
    null,
  );
});

test('valid canonical truth stays authoritative without changing false values', async () => {
  const body = authoritativeMeta();
  const projected = projectOperatorState(body);

  assert.equal(projected.availability, 'AVAILABLE');
  assert.equal(projected.health, 'UNKNOWN');
  assert.equal(projected.requestedMode, 'PAPER');
  assert.equal(projected.mode, 'PAPER');
  assert.equal(projected.killSwitchState, 'INACTIVE');
  assert.equal(projected.liveExecutionEnabled, false);
  assert.equal(projected.liveTradingApproved, false);
  assert.equal(projected.metrics, null);
  assert.equal(projected.controlsEnabled, true);

  const loaded = await loadOperatorState(async () => Response.json(body));
  assert.deepEqual(loaded, projected);
});

test('strict execution parsing preserves authoritative numeric zero', () => {
  const payload = {
    mode: 'paper',
    halted: false,
    open_orders: [],
    recent_trades: [],
    positions: [],
    pnl: {
      total_realized_pnl: 0,
      total_unrealized_pnl: 0,
    },
    alerts: [],
    slippage_history: [],
    avg_slippage: 0,
  };

  const parsed = parseExecutionPayload(payload);
  assert.notEqual(parsed, null);
  assert.equal(parsed.pnl.total_realized_pnl, 0);
  assert.equal(parsed.pnl.total_unrealized_pnl, 0);
  assert.equal(parsed.avg_slippage, 0);
  assert.equal(parsed.open_orders.length, 0);

  const missing = { ...payload };
  delete missing.halted;
  assert.equal(parseExecutionPayload(missing), null);
  assert.equal(parseExecutionPayload({ ...payload, extra: 0 }), null);
});

test('execution truth requires exact known operator mode and kill state', () => {
  const payload = parseExecutionPayload({
    mode: 'paper',
    halted: false,
    open_orders: [],
    recent_trades: [],
    positions: [],
    pnl: { total_realized_pnl: 0, total_unrealized_pnl: 0 },
    alerts: [],
    slippage_history: [],
    avg_slippage: 0,
  });
  assert.notEqual(payload, null);

  const inactive = projectOperatorState(authoritativeMeta());
  assert.equal(executionMatchesOperator(payload, inactive), true);
  assert.equal(
    executionMatchesOperator(
      { ...payload, halted: true },
      projectOperatorState(authoritativeMeta({ kill_switch_state: 'ACTIVE' })),
    ),
    true,
  );
  assert.equal(
    executionMatchesOperator(
      payload,
      projectOperatorState(authoritativeMeta({ kill_switch_state: 'UNKNOWN' })),
    ),
    false,
  );
  assert.equal(executionMatchesOperator(payload, UNAVAILABLE_OPERATOR_STATE), false);
  assert.equal(executionMatchesOperator({ ...payload, mode: 'dryrun' }, inactive), false);
  assert.equal(executionMatchesOperator({ ...payload, halted: true }, inactive), false);
});

test('execution state is unknown-first and fails closed on transport or schema errors', async (t) => {
  assert.deepEqual(INITIAL_EXECUTION_STATE, { availability: 'LOADING', data: null });
  assert.deepEqual(UNAVAILABLE_EXECUTION_STATE, { availability: 'UNAVAILABLE', data: null });

  const payload = {
    mode: 'paper',
    halted: false,
    open_orders: [],
    recent_trades: [],
    positions: [],
    pnl: { total_realized_pnl: 0, total_unrealized_pnl: 0 },
    alerts: [],
    slippage_history: [],
    avg_slippage: 0,
  };
  const missing = { ...payload };
  delete missing.pnl;

  const cases = [
    ['503', async () => new Response(null, { status: 503 })],
    ['null', async () => Response.json(null)],
    ['invalid JSON', async () => new Response('{bad-json', { status: 200 })],
    ['missing', async () => Response.json(missing)],
    ['extra', async () => Response.json({ ...payload, debug: true })],
  ];
  for (const [name, fetcher] of cases) {
    await t.test(name, async () => {
      assert.deepEqual(await loadExecutionState(fetcher), UNAVAILABLE_EXECUTION_STATE);
    });
  }

  const available = await loadExecutionState(async () => Response.json(payload));
  assert.equal(available.availability, 'AVAILABLE');
  assert.equal(available.data.pnl.total_realized_pnl, 0);
  assert.equal(available.data.pnl.total_unrealized_pnl, 0);
  assert.equal(available.data.avg_slippage, 0);
});

test('execution loading honors caller cancellation', async () => {
  const caller = new AbortController();
  let requestAborted = false;
  const fetcher = async (_input, init) => new Promise((_resolve, reject) => {
    init.signal.addEventListener('abort', () => {
      requestAborted = true;
      reject(new DOMException('Aborted', 'AbortError'));
    }, { once: true });
  });

  const pending = loadExecutionState(fetcher, {
    signal: caller.signal,
    timeoutMs: 100,
  });
  caller.abort();

  const result = await Promise.race([
    pending,
    new Promise((resolve) => setTimeout(() => resolve('NOT_ABORTED'), 20)),
  ]);
  assert.deepEqual(result, UNAVAILABLE_EXECUTION_STATE);
  assert.equal(requestAborted, true);
});

test('exchange status is unknown-first, strict, and preserves authoritative zero balances', async (t) => {
  assert.deepEqual(INITIAL_EXCHANGE_STATUS_STATE, { availability: 'LOADING', data: null });
  assert.deepEqual(
    UNAVAILABLE_EXCHANGE_STATUS_STATE,
    { availability: 'UNAVAILABLE', data: null },
  );

  const valid = {
    mode: 'paper',
    exchanges: {
      binance: { connected: true, sandbox: true, balance_usd: 0 },
    },
    live_execution_enabled: false,
  };
  const parsed = parseExchangeStatusPayload(valid);
  assert.notEqual(parsed, null);
  assert.equal(parsed.exchanges.binance.balance_usd, 0);
  assert.equal(
    exchangeStatusMatchesOperator(parsed, projectOperatorState(authoritativeMeta())),
    true,
  );
  assert.equal(
    exchangeStatusMatchesOperator(parsed, UNAVAILABLE_OPERATOR_STATE),
    false,
  );
  assert.equal(
    exchangeStatusMatchesOperator(
      { ...parsed, mode: 'dryrun' },
      projectOperatorState(authoritativeMeta()),
    ),
    false,
  );

  const missing = { ...valid };
  delete missing.live_execution_enabled;
  const cases = [
    ['503', async () => new Response(null, { status: 503 })],
    ['null', async () => Response.json(null)],
    ['missing', async () => Response.json(missing)],
    ['extra', async () => Response.json({ ...valid, error: 'ignored' })],
    ['invalid nested field', async () => Response.json({
      ...valid,
      exchanges: { binance: { ...valid.exchanges.binance, debug: true } },
    })],
  ];
  for (const [name, fetcher] of cases) {
    await t.test(name, async () => {
      assert.deepEqual(
        await loadExchangeStatusState(fetcher),
        UNAVAILABLE_EXCHANGE_STATUS_STATE,
      );
    });
  }

  const availableEmpty = await loadExchangeStatusState(async () => Response.json({
    ...valid,
    exchanges: {},
  }));
  assert.equal(availableEmpty.availability, 'AVAILABLE');
  assert.deepEqual(availableEmpty.data.exchanges, {});
});

test('service status is unknown-first and only authoritative false means stopped', async (t) => {
  assert.deepEqual(INITIAL_SERVICE_STATE, { availability: 'LOADING', data: null });
  assert.deepEqual(UNAVAILABLE_SERVICE_STATE, { availability: 'UNAVAILABLE', data: null });

  const stopped = { active: false, pid: 0, started: '' };
  assert.deepEqual(parseServicePayload(stopped), stopped);
  assert.deepEqual(
    parseServicePayload({ active: true, pid: 42, started: '2026-07-16T12:00:00Z' }),
    { active: true, pid: 42, started: '2026-07-16T12:00:00Z' },
  );
  assert.equal(parseServicePayload({ active: true, pid: 0, started: '' }), null);

  const missing = { ...stopped };
  delete missing.started;
  const cases = [
    ['503', async () => new Response(null, { status: 503 })],
    ['null', async () => Response.json(null)],
    ['missing', async () => Response.json(missing)],
    ['extra', async () => Response.json({ ...stopped, message: 'ignored' })],
  ];
  for (const [name, fetcher] of cases) {
    await t.test(name, async () => {
      assert.deepEqual(await loadServiceState(fetcher), UNAVAILABLE_SERVICE_STATE);
    });
  }

  const available = await loadServiceState(async () => Response.json(stopped));
  assert.equal(available.availability, 'AVAILABLE');
  assert.equal(available.data.active, false);
  assert.equal(available.data.pid, 0);
});
