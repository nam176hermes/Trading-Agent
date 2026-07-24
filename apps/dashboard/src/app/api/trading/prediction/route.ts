import { sourceUnavailable } from '@/lib/trading/source-unavailable';

export async function GET() {
  return sourceUnavailable('Prediction history');
}

export const dynamic = 'force-dynamic';
export const revalidate = 0;
