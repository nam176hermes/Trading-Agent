import { sourceUnavailable } from '@/lib/trading/source-unavailable';

export async function GET() {
  return sourceUnavailable('Paper orders');
}

export const dynamic = 'force-dynamic';
export const revalidate = 0;
