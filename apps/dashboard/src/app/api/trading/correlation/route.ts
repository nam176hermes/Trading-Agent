import { NextResponse } from 'next/server';

interface CorrelationPair {
  asset1: string;
  asset2: string;
  correlation: number;
  status: 'safe' | 'warning' | 'danger';
}

interface SectorExposure {
  sector: string;
  exposure_pct: number;
  limit_pct: number;
  assets: string[];
  is_over_limit: boolean;
}

interface CorrelationMatrix {
  symbols: string[];
  matrix: number[][];
  threshold: number;
  high_correlation_pairs: CorrelationPair[];
  sector_exposures: SectorExposure[];
  calculated_at: string;
}

// Simplified correlation based on asset categories
function calculateMockCorrelation(asset1: string, asset2: string): number {
  const l1Assets = ['BTC', 'ETH'];
  const l2Assets = ['SOL', 'TON'];
  const memeAssets = ['DOGE'];

  if (l1Assets.includes(asset1) && l1Assets.includes(asset2)) return 0.85;
  if (l2Assets.includes(asset1) && l2Assets.includes(asset2)) return 0.68;

  const hasL1 = l1Assets.some((a) => [asset1, asset2].includes(a));
  const hasL2 = l2Assets.some((a) => [asset1, asset2].includes(a));
  if (hasL1 && hasL2) return 0.72;

  if (memeAssets.includes(asset1) || memeAssets.includes(asset2)) return 0.38;
  return 0.45;
}

async function getCorrelationMatrix(): Promise<CorrelationMatrix> {
  const symbols = ['BTC', 'ETH', 'SOL', 'TON', 'DOGE'];
  const threshold = 0.7;

  // Build correlation matrix
  const matrix: number[][] = [];
  for (let i = 0; i < symbols.length; i++) {
    matrix[i] = [];
    for (let j = 0; j < symbols.length; j++) {
      if (i === j) {
        matrix[i][j] = 1.0;
      } else {
        matrix[i][j] = calculateMockCorrelation(symbols[i], symbols[j]);
      }
    }
  }

  // Find high correlation pairs
  const highCorrelationPairs: CorrelationPair[] = [];
  for (let i = 0; i < symbols.length; i++) {
    for (let j = i + 1; j < symbols.length; j++) {
      const corr = matrix[i][j];
      if (Math.abs(corr) >= threshold) {
        highCorrelationPairs.push({
          asset1: symbols[i],
          asset2: symbols[j],
          correlation: corr,
          status: Math.abs(corr) >= 0.85 ? 'danger' : 'warning',
        });
      }
    }
  }

  // Sector exposures (simplified)
  const sectorExposures: SectorExposure[] = [
    {
      sector: 'Layer 1',
      exposure_pct: 50,
      limit_pct: 40,
      assets: ['BTC', 'ETH'],
      is_over_limit: true,
    },
    {
      sector: 'Layer 2',
      exposure_pct: 25,
      limit_pct: 40,
      assets: ['SOL', 'TON'],
      is_over_limit: false,
    },
    {
      sector: 'Memes',
      exposure_pct: 15,
      limit_pct: 20,
      assets: ['DOGE'],
      is_over_limit: false,
    },
  ];

  return {
    symbols,
    matrix,
    threshold,
    high_correlation_pairs: highCorrelationPairs,
    sector_exposures: sectorExposures,
    calculated_at: new Date().toISOString(),
  };
}

export async function GET() {
  try {
    const correlation = await getCorrelationMatrix();
    return NextResponse.json(correlation);
  } catch (error) {
    console.error('Error fetching correlation matrix:', error);
    return NextResponse.json({ error: 'Failed to fetch correlation matrix' }, { status: 500 });
  }
}

export const dynamic = 'force-dynamic';
export const revalidate = 0;
