import { authorizeMutation } from '@/lib/trading/auth';
import { commandUnavailable } from '@/lib/trading/source-unavailable';

export function POST(request: Request) {
  const authError = authorizeMutation(request, 'plan.create', 'MUTATION_LOW_RISK', 'operator');
  return authError ?? commandUnavailable('Research planning');
}

export const dynamic = 'force-dynamic';
export const revalidate = 0;
