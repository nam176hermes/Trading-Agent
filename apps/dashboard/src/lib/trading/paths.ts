import 'server-only';

import os from 'os';
import path from 'path';

function configuredPath(name: string): string | null {
  const configured = process.env[name]?.trim();
  return configured ? path.resolve(configured) : null;
}

export function researchDataRoot(): string {
  const configuredHome = process.env.HOME?.trim();
  const home = configuredHome ? path.resolve(configuredHome) : os.homedir();
  return configuredPath('TRADING_DATA_ROOT')
    ?? path.join(home, '.local', 'share', 'trading-agent');
}

export function reportsDir(): string {
  return path.join(researchDataRoot(), 'reports');
}

export function decisionsDir(): string {
  return path.join(researchDataRoot(), 'decisions');
}

export function memoryDir(): string {
  return path.join(researchDataRoot(), 'memory');
}

export function modeFile(): string {
  return configuredPath('TRADING_MODE_FILE') ?? path.join(researchDataRoot(), '.mode');
}

export function killSwitchFile(): string {
  return configuredPath('TRADING_KILL_SWITCH_PATH')
    ?? path.join(researchDataRoot(), '.kill_switch');
}
