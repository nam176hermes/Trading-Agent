import { NextResponse } from 'next/server';
import { getControlMeta, type ControlDeploymentMeta } from '@/lib/trading/control-api';

export async function GET() {
  let canonical: ControlDeploymentMeta | null = null;
  try {
    canonical = (await getControlMeta()).data;
  } catch {
    // Deployment identity remains observable when the loopback dependency is down.
  }
  return NextResponse.json({
    service: 'legacy-trading-dashboard',
    git_commit: process.env.GIT_COMMIT ?? 'unknown',
    build_time: process.env.BUILD_TIME ?? 'unknown',
    deployment_id: process.env.DEPLOYMENT_ID ?? 'dashboard-systemd-port-3002',
    control_api_available: canonical !== null,
    requested_mode: canonical?.requested_mode.toLowerCase() ?? null,
    effective_mode: canonical?.effective_mode.toLowerCase() ?? null,
    execution_capability: canonical?.execution_capability ?? null,
    live_execution_enabled: canonical?.live_execution_enabled ?? false,
    live_trading_approved: canonical?.live_trading_approved ?? false,
    kill_switch_state: canonical?.kill_switch_state ?? 'UNKNOWN',
    canonical_deployment_id: canonical?.deployment_id ?? null,
  }, { headers: { 'cache-control': 'no-store' } });
}

export const dynamic = 'force-dynamic';
export const revalidate = 0;
