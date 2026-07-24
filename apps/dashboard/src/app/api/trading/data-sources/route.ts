import { sourceUnavailable } from '@/lib/trading/source-unavailable';

export async function GET() {
  return sourceUnavailable('Data-source reports');
}

export const dynamic = 'force-dynamic';
export const revalidate = 0;
