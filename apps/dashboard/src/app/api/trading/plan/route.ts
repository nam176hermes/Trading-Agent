import { NextResponse } from 'next/server';
import type { ResearchPlan } from '@/lib/trading/types';
import { authorizeMutation } from '@/lib/trading/auth';
import { readBoundedJsonBody } from '@/lib/trading/request-body';

const MAX_PLAN_BODY_BYTES = 16 * 1024;

function isPlanRequest(value: unknown): value is { query: string; keywords: string[] } {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const record = value as Record<string, unknown>;
  const keys = Object.keys(record).sort();
  if (keys.length !== 2 || keys[0] !== 'keywords' || keys[1] !== 'query') return false;
  return typeof record.query === 'string' && record.query.trim().length > 0 && record.query.length <= 512
    && Array.isArray(record.keywords) && record.keywords.length <= 16
    && record.keywords.every((keyword) => typeof keyword === 'string' && keyword.length > 0 && keyword.length <= 64);
}

export async function POST(request: Request) {
  const authError = authorizeMutation(request, 'plan.create', 'MUTATION_LOW_RISK', 'operator');
  if (authError) return authError;

  try {
    const parsed = await readBoundedJsonBody(request, MAX_PLAN_BODY_BYTES);
    if (!parsed.ok) {
      return NextResponse.json(
        { error: parsed.reason === 'too_large' ? 'Request body too large' : 'Invalid request body' },
        { status: parsed.reason === 'too_large' ? 413 : 400 },
      );
    }
    if (!isPlanRequest(parsed.value)) {
      return NextResponse.json(
        { error: 'Query is required' },
        { status: 400 }
      );
    }
    const { query, keywords } = parsed.value;

    // Generate a simple research plan (in a real implementation, this would use an LLM)
    const plan: ResearchPlan = {
      id: `${Date.now()}_${Math.random().toString(36).substr(2, 9)}`,
      query,
      keywords,
      steps: generatePlanSteps(query, keywords),
      created_at: new Date().toISOString(),
    };

    return NextResponse.json(plan);
  } catch (error) {
    console.error('Error in /api/trading/plan:', error);
    return NextResponse.json(
      { error: 'Failed to generate research plan' },
      { status: 500 }
    );
  }
}

function generatePlanSteps(query: string, keywords: string[]) {
  const baseSteps = [
    {
      id: '1',
      title: 'Data Collection',
      description: 'Gather market data for specified assets',
      dependencies: [],
      status: 'pending' as const,
    },
    {
      id: '2',
      title: 'Technical Analysis',
      description: 'Analyze technical indicators (RSI, MACD, SMA)',
      dependencies: ['1'],
      status: 'pending' as const,
    },
    {
      id: '3',
      title: 'Sentiment Analysis',
      description: 'Collect and analyze market sentiment',
      dependencies: ['1'],
      status: 'pending' as const,
    },
    {
      id: '4',
      title: 'Signal Generation',
      description: 'Generate trading signals based on analysis',
      dependencies: ['2', '3'],
      status: 'pending' as const,
    },
  ];

  // Add conditional steps based on keywords
  if (keywords.includes('Risk')) {
    baseSteps.push({
      id: '5',
      title: 'Risk Assessment',
      description: 'Evaluate risk levels and position sizing',
      dependencies: ['4'],
      status: 'pending' as const,
    });
  }

  if (keywords.includes('On-chain')) {
    baseSteps.push({
      id: '6',
      title: 'On-chain Analysis',
      description: 'Analyze on-chain metrics and activity',
      dependencies: ['1'],
      status: 'pending' as const,
    });
  }

  if (keywords.includes('Debate')) {
    baseSteps.push({
      id: '7',
      title: 'Adversarial Debate',
      description: 'Run bull vs bear case analysis',
      dependencies: ['4'],
      status: 'pending' as const,
    });
  }

  return baseSteps;
}

export const dynamic = 'force-dynamic';
export const revalidate = 0;
