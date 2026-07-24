import { sourceUnavailable } from '@/lib/trading/source-unavailable';

export async function GET() {
  return sourceUnavailable('Macro reports');
}

export const dynamic = 'force-dynamic';
export const revalidate = 0;
