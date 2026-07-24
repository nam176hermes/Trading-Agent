import path from 'path';

import {
  readLocalStateFile,
  removePrivateLocalStateFile,
  writePrivateLocalStateFile,
} from './local-state';
import { killSwitchFile } from './paths';

const MAX_KILL_SWITCH_STATE_BYTES = 4 * 1024;

export type KillSwitchState = 'ACTIVE' | 'INACTIVE' | 'UNKNOWN';

export interface KillSwitchStatus {
  state: KillSwitchState;
  reason: string | null;
  activated_at: string | null;
  error_code?: 'READ_ERROR' | 'INVALID_STATE';
}

export function resolveKillSwitchPath(): string {
  return killSwitchFile();
}

export function readKillSwitchState(): KillSwitchStatus {
  const target = resolveKillSwitchPath();
  try {
    const stored = readLocalStateFile(target, MAX_KILL_SWITCH_STATE_BYTES);
    if (stored === null) return { state: 'INACTIVE', reason: null, activated_at: null };
    const content = stored.trim();
    const separator = content.indexOf(': ');
    if (separator < 1) {
      return { state: 'UNKNOWN', reason: null, activated_at: null, error_code: 'INVALID_STATE' };
    }
    const activatedAt = content.slice(0, separator);
    const reason = content.slice(separator + 2).trim();
    if (!reason || Number.isNaN(Date.parse(activatedAt))) {
      return { state: 'UNKNOWN', reason: null, activated_at: null, error_code: 'INVALID_STATE' };
    }
    return { state: 'ACTIVE', reason, activated_at: activatedAt };
  } catch {
    return { state: 'UNKNOWN', reason: null, activated_at: null, error_code: 'READ_ERROR' };
  }
}

export function activateKillSwitch(reason: string): KillSwitchStatus {
  const target = resolveKillSwitchPath();
  writePrivateLocalStateFile(target, `${new Date().toISOString()}: ${reason}\n`);
  return readKillSwitchState();
}

export function clearKillSwitch(): KillSwitchStatus {
  const target = resolveKillSwitchPath();
  removePrivateLocalStateFile(target);
  return readKillSwitchState();
}

export function publicKillSwitchPath(): string {
  return process.env.TRADING_KILL_SWITCH_PATH?.trim()
    ? '$TRADING_KILL_SWITCH_PATH'
    : `$TRADING_DATA_ROOT/${path.basename(resolveKillSwitchPath())}`;
}
