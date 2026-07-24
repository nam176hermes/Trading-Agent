import { sourceUnavailable } from '@/lib/trading/source-unavailable';

export async function GET() {
  return sourceUnavailable('Exchange connectivity status');
}

export const dynamic = 'force-dynamic';
export const revalidate = 0;
