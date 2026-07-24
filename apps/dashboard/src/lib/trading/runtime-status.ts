import 'server-only';

import { getControlStatus } from './control-api';

const TRUTHY = new Set(['1', 'true', 'yes', 'on']);

export function runtimeFlag(name: string): boolean {
  return TRUTHY.has((process.env[name] ?? '').trim().toLowerCase());
}

export function dataRoot(): string {
  return 'postgres-operational-store';
}

export async function runtimeStatus() {
  const response = await getControlStatus();
  const status = response.data;
  return {
    requested_mode: status.requested_mode.toLowerCase() as 'paper' | 'dryrun' | 'live',
    effective_mode: status.effective_mode.toLowerCase() as 'paper' | 'dryrun' | 'live',
    execution_capability: status.execution_capability,
    live_execution_enabled: status.execution_capability === 'LIVE_AVAILABLE',
    kill_switch_state: status.kill_switch_state,
  } as const;
}
