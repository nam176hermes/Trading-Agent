import { authorizeMutation } from '@/lib/trading/auth';
import { cancelJob, commandsDisabledResponse, commandsEnabled } from '@/lib/trading/job-api';

export async function POST(request: Request, { params }: { params: Promise<{ id: string }> }) {
  const authError = authorizeMutation(request, 'jobs.cancel', 'MUTATION_EXECUTION_SENSITIVE', 'operator');
  if (authError) return authError;
  if (!commandsEnabled()) return commandsDisabledResponse();
  const { id } = await params;
  return (await cancelJob(id)).response;
}

export const dynamic = 'force-dynamic';
export const revalidate = 0;
