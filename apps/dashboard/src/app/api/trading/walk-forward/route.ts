import { sourceUnavailable } from '@/lib/trading/source-unavailable';

export async function GET() {
  return sourceUnavailable('Walk-forward evidence');
}

export const dynamic = 'force-dynamic';
export const revalidate = 0;
