import { checkAuth } from '@/lib/trading/auth';
import { getJob } from '@/lib/trading/job-api';

export async function GET(request: Request, { params }: { params: Promise<{ id: string }> }) {
  const authError = checkAuth(request);
  if (authError) return authError;
  const { id } = await params;
  return (await getJob(id)).response;
}

export const dynamic = 'force-dynamic';
export const revalidate = 0;
