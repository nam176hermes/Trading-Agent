import { sourceUnavailable } from '@/lib/trading/source-unavailable';

export async function GET() {
  return sourceUnavailable('Signal-quality evidence');
}

export const dynamic = 'force-dynamic';
export const revalidate = 0;
