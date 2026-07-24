import { authorizeMutation, checkAuth } from '@/lib/trading/auth';
import { commandsDisabledResponse, commandsEnabled, createJob, listJobs, readDashboardAction } from '@/lib/trading/job-api';

export async function GET(request: Request) {
  const authError = checkAuth(request);
  if (authError) return authError;
  return (await listJobs('limit=10&offset=0')).response;
}

export async function POST(request: Request) {
  const authError = authorizeMutation(request, 'pipeline.run', 'MUTATION_EXECUTION_SENSITIVE', 'operator');
  if (authError) return authError;
  if (!commandsEnabled()) return commandsDisabledResponse();
  const action = await readDashboardAction(request);
  if (!action) return Response.json({ ok: false, code: 'INVALID_REQUEST', message: 'Research action is invalid.' }, { status: 400 });
  return (await createJob(action)).response;
}

export const dynamic = 'force-dynamic';
export const revalidate = 0;
