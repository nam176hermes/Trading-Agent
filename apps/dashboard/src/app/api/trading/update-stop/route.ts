import { authorizeMutation } from '@/lib/trading/auth';
import { commandUnavailable } from '@/lib/trading/source-unavailable';

export function POST(request: Request) {
  const authError = authorizeMutation(request, 'position.update_stop', 'MUTATION_EXECUTION_SENSITIVE', 'operator');
  return authError ?? commandUnavailable('Stop-loss mutation');
}

export const dynamic = 'force-dynamic';
export const revalidate = 0;
