import { sourceUnavailable } from '@/lib/trading/source-unavailable';

export function GET() {
  return sourceUnavailable('Correlation data');
}

export const dynamic = 'force-dynamic';
export const revalidate = 0;
