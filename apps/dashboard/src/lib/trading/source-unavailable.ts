import 'server-only';

function unavailable(domain: string, code: 'SOURCE_UNAVAILABLE' | 'COMMAND_UNAVAILABLE', kind: string): Response {
  return Response.json(
    {
      schema_version: '1.0.0',
      trace_id: `trace_dashboard_${crypto.randomUUID().replaceAll('-', '')}`,
      generated_at: new Date().toISOString(),
      error: {
        code,
        message: `${domain} is not exposed by the canonical ${kind} contract.`,
        details: {},
      },
    },
    { status: 503, headers: { 'cache-control': 'no-store' } },
  );
}

export function sourceUnavailable(domain: string): Response {
  return unavailable(domain, 'SOURCE_UNAVAILABLE', 'Control API/PostgreSQL read');
}

export function commandUnavailable(domain: string): Response {
  return unavailable(domain, 'COMMAND_UNAVAILABLE', 'command');
}
