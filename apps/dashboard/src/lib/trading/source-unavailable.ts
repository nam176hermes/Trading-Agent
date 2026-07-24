import 'server-only';

export function sourceUnavailable(domain: string): Response {
  return Response.json(
    {
      schema_version: '1.0.0',
      trace_id: `trace_dashboard_${crypto.randomUUID().replaceAll('-', '')}`,
      generated_at: new Date().toISOString(),
      error: {
        code: 'SOURCE_UNAVAILABLE',
        message: `${domain} is not exposed by the canonical Control API/PostgreSQL read contract.`,
        details: {},
      },
    },
    { status: 503, headers: { 'cache-control': 'no-store' } },
  );
}
