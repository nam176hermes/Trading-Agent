import { checkAuth } from '@/lib/trading/auth';
import { listJobs } from '@/lib/trading/job-api';

export async function GET(request: Request) {
  const authError = checkAuth(request);
  if (authError) return authError;
  return (await listJobs('limit=10&offset=0')).response;
}

export const dynamic = 'force-dynamic';
export const revalidate = 0;
