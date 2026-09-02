import { authorizeMutation } from '@/lib/trading/auth';
import { commandUnavailable, sourceUnavailable } from '@/lib/trading/source-unavailable';

export function GET() {
  return sourceUnavailable('Watchlist data');
}

export function POST(request: Request) {
  const authError = authorizeMutation(request, 'watchlist.update', 'MUTATION_LOW_RISK', 'operator');
  return authError ?? commandUnavailable('Watchlist mutation');
}

export const dynamic = 'force-dynamic';
export const revalidate = 0;
