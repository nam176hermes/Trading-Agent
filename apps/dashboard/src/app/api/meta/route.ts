import { GET as getTradingMeta } from '../trading/meta/route';

export async function GET() {
  return getTradingMeta();
}

export const dynamic = 'force-dynamic';
export const revalidate = 0;
