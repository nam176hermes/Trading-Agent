import { NextResponse } from 'next/server';

import { controlApiUnavailableResponse, getControlCosts } from '@/lib/trading/control-api';

export async function GET() {
  try {
    const response = await getControlCosts();
    const data = response.data;
    const llmCalls = data.total_llm_calls;
    const toolCalls = data.total_tool_calls;
    const estimatedCost = data.amount;
    return NextResponse.json({
      summary: {
        totalSessions: data.total_sessions,
        totalLLMCalls: llmCalls,
        totalToolCalls: toolCalls,
        estimatedCost,
        optimizerTokensSaved: null,
        optimizerCostSaved: null,
        evidenceQuality: data.evidence_quality,
        note: data.note,
      },
      sessions: data.sessions.slice(0, 10).map((session) => ({
        session: session.session,
        symbols: session.symbols,
        steps: session.steps,
        llmCalls: session.llm_calls,
        toolCalls: session.tool_calls,
        decisions: session.decisions,
        duration: null,
        estimatedCost: session.estimated_cost,
      })),
      costModel: null,
      efficiency: {
        avgLLMCallsPerSession: data.total_sessions && llmCalls !== null
          ? llmCalls / data.total_sessions : null,
        avgCostPerSession: data.total_sessions && estimatedCost !== null
          ? estimatedCost / data.total_sessions : null,
        avgToolCallsPerLLM: llmCalls && toolCalls !== null ? toolCalls / llmCalls : null,
      },
    }, { headers: { 'cache-control': 'no-store' } });
  } catch {
    return controlApiUnavailableResponse();
  }
}

export const dynamic = 'force-dynamic';
export const revalidate = 0;
