const RISK_LEVELS = new Set(['LOW', 'MEDIUM', 'HIGH', 'CRITICAL']);

export interface RiskEvidenceAsset {
  risk_assessment?: { risk_level?: unknown } | null;
}

export interface AssetRiskSummary {
  availability: 'AVAILABLE' | 'UNKNOWN';
  highRisk: number | null;
  tracked: number | null;
}

export function dashboardReportAssets<T>(
  report: { assets: T[] } | null,
): T[] | null {
  return report === null ? null : report.assets;
}

export function summarizeAssetRisk(
  assets: readonly RiskEvidenceAsset[] | null,
): AssetRiskSummary {
  if (assets === null || assets.some((asset) => {
    const level = asset.risk_assessment?.risk_level;
    return typeof level !== 'string' || !RISK_LEVELS.has(level);
  })) {
    return { availability: 'UNKNOWN', highRisk: null, tracked: null };
  }

  return {
    availability: 'AVAILABLE',
    highRisk: assets.filter((asset) => {
      const level = asset.risk_assessment?.risk_level;
      return level === 'HIGH' || level === 'CRITICAL';
    }).length,
    tracked: assets.length,
  };
}
