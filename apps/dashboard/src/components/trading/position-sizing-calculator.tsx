'use client';

import { useCallback, useState, useEffect } from 'react';
import { Calculator, ChevronDown, ChevronUp, Info, TrendingUp } from 'lucide-react';

interface PositionSizingCalculatorProps {
  accountBalance?: number;
  currentPrice?: number;
  initialData?: ApiPositionSizingResult | null;
}

interface ApiPositionSizingResult {
  account_balance: number;
  current_price: number;
  win_rate: number;
  avg_win: number;
  avg_loss: number;
  kelly_percentage: number;
  half_kelly_percentage: number;
  recommended_position_size: number;
  shares: number;
  stop_loss_price: number;
  take_profit_price: number;
  risk_amount: number;
  reward_amount: number;
  reward_risk_ratio: number;
  historical_performance?: {
    total_trades: number;
    winning_trades: number;
    losing_trades: number;
    win_rate: number;
    avg_win_pct: number;
    avg_loss_pct: number;
  } | null;
}

export function PositionSizingCalculator({ accountBalance = 100000, currentPrice = 67500, initialData }: PositionSizingCalculatorProps) {
  const hasServerData = initialData !== undefined;
  const [loading, setLoading] = useState(!hasServerData);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [customWinRate, setCustomWinRate] = useState(initialData ? initialData.win_rate * 100 : 55);
  const [customAvgWin, setCustomAvgWin] = useState(initialData?.avg_win ?? 8);
  const [customAvgLoss, setCustomAvgLoss] = useState(initialData?.avg_loss ?? 4);
  const [customAccountBalance, setCustomAccountBalance] = useState(initialData?.account_balance ?? accountBalance);
  const [customCurrentPrice, setCustomCurrentPrice] = useState(initialData?.current_price ?? currentPrice);

  const fetchPositionSizing = useCallback(async () => {
    if (hasServerData) return;
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 10000);
    try {
      setError(null);
      const res = await fetch(`/api/trading/position-sizing?accountBalance=${accountBalance}&currentPrice=${currentPrice}`, { signal: controller.signal });
      if (res.ok) {
        const data: ApiPositionSizingResult = await res.json();
        setCustomWinRate(data.win_rate * 100);
        setCustomAvgWin(data.avg_win);
        setCustomAvgLoss(data.avg_loss);
        setCustomAccountBalance(data.account_balance);
        setCustomCurrentPrice(data.current_price);
      } else {
        setError('Failed to load position sizing data');
      }
    } catch {
      setError('Failed to load position sizing data');
    } finally {
      clearTimeout(timeoutId);
      setLoading(false);
    }
  }, [accountBalance, currentPrice, hasServerData]);

  useEffect(() => {
    const initialFetch = setTimeout(fetchPositionSizing, 0);
    return () => clearTimeout(initialFetch);
  }, [fetchPositionSizing]);

  // Kelly Criterion Formula: f* = (bp - q) / b
  const calculateKelly = () => {
    const p = customWinRate / 100;
    const q = 1 - p;
    const b = customAvgWin / customAvgLoss;

    const kelly = (b * p - q) / b;
    const halfKelly = Math.max(0, kelly / 2);
    const positionSize = customAccountBalance * halfKelly;
    const shares = Math.floor(positionSize / customCurrentPrice);
    const riskAmount = positionSize * (customAvgLoss / 100);

    return {
      kellyPercentage: kelly,
      halfKellyPercentage: halfKelly,
      suggestedPositionSize: positionSize,
      suggestedShares: shares,
      riskAmount
    };
  };

  const calculated = calculateKelly();

  const isNegativeKelly = calculated.kellyPercentage < 0;

  if (error) {
    return (
      <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-4">
        <div className="flex items-center gap-3">
          <p className="text-sm text-red-400">{error}</p>
          <button onClick={fetchPositionSizing} className="text-xs text-blue-400 hover:text-blue-300 underline">Retry</button>
        </div>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-4">
        <div className="flex items-center gap-2 text-zinc-500">
          <div className="h-4 w-4 animate-pulse rounded bg-zinc-800" />
          <span className="text-sm">Loading position sizing data...</span>
        </div>
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 overflow-hidden">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center justify-between px-4 py-2.5 border-b border-zinc-800 bg-zinc-900 hover:bg-zinc-800/60 transition-colors"
      >
        <div className="flex items-center gap-2">
          <Calculator className="h-4 w-4 text-amber-400" />
          <span className="text-xs font-bold text-zinc-200">Position Sizing</span>
          <span className="text-[10px] text-zinc-600 hidden sm:inline">Kelly Criterion</span>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <span className={`text-xs font-mono font-medium ${isNegativeKelly ? 'text-red-400' : 'text-green-400'}`}>
            {isNegativeKelly ? 'NO EDGE' : `${Number.isFinite(Number(calculated.halfKellyPercentage)) ? (Number(calculated.halfKellyPercentage) * 100).toFixed(1) : '—'}%`}
          </span>
          {expanded ? <ChevronUp className="h-3.5 w-3.5 text-zinc-500" /> : <ChevronDown className="h-3.5 w-3.5 text-zinc-500" />}
        </div>
      </button>

      {expanded && (
        <div className="p-4 space-y-4">
          {/* Kelly Result */}
          <div className={`rounded-lg border p-3 ${isNegativeKelly ? 'border-red-500/30 bg-red-500/5' : 'border-green-500/30 bg-green-500/5'}`}>
            <div className="flex items-center gap-2 mb-2">
              <Info className={`h-3.5 w-3.5 ${isNegativeKelly ? 'text-red-400' : 'text-green-400'}`} />
              <span className="text-xs font-bold text-zinc-200">Half-Kelly Recommendation</span>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <p className="text-[10px] text-zinc-500">Position Size</p>
                <p className={`text-sm font-mono font-bold ${isNegativeKelly ? 'text-red-400' : 'text-green-400'}`}>
                  {isNegativeKelly ? '0%' : `${Number.isFinite(Number(calculated.halfKellyPercentage)) ? (Number(calculated.halfKellyPercentage) * 100).toFixed(2) : '—'}%`}
                </p>
              </div>
              <div>
                <p className="text-[10px] text-zinc-500">Dollar Amount</p>
                <p className="text-sm font-mono font-bold text-zinc-200">
                  ${isNegativeKelly ? '0' : calculated.suggestedPositionSize.toLocaleString()}
                </p>
              </div>
              <div>
                <p className="text-[10px] text-zinc-500">Shares @ ${currentPrice.toLocaleString()}</p>
                <p className="text-sm font-mono font-bold text-zinc-200">{calculated.suggestedShares}</p>
              </div>
              <div>
                <p className="text-[10px] text-zinc-500">Risk Amount</p>
                <p className="text-sm font-mono font-bold text-red-400">${calculated.riskAmount.toLocaleString()}</p>
              </div>
            </div>
          </div>

          {/* Custom Inputs */}
          <div>
            <p className="text-[10px] font-semibold text-zinc-500 mb-2 flex items-center gap-1">
              <TrendingUp className="h-3 w-3" />
              CUSTOMIZE PARAMETERS
            </p>
            <div className="grid grid-cols-5 gap-2">
              <div>
                <label className="text-[10px] text-zinc-500 block mb-1">Win Rate %</label>
                <input
                  type="number"
                  min="0"
                  max="100"
                  step="1"
                  value={customWinRate}
                  onChange={(e) => setCustomWinRate(parseFloat(e.target.value) || 0)}
                  className="w-full bg-zinc-800 border border-zinc-700 rounded px-2 py-1.5 text-xs font-mono text-zinc-200 focus:outline-none focus:border-amber-500/50"
                />
              </div>
              <div>
                <label className="text-[10px] text-zinc-500 block mb-1">Avg Win %</label>
                <input
                  type="number"
                  min="0"
                  step="0.1"
                  value={customAvgWin}
                  onChange={(e) => setCustomAvgWin(parseFloat(e.target.value) || 0)}
                  className="w-full bg-zinc-800 border border-zinc-700 rounded px-2 py-1.5 text-xs font-mono text-zinc-200 focus:outline-none focus:border-amber-500/50"
                />
              </div>
              <div>
                <label className="text-[10px] text-zinc-500 block mb-1">Avg Loss %</label>
                <input
                  type="number"
                  min="0"
                  step="0.1"
                  value={customAvgLoss}
                  onChange={(e) => setCustomAvgLoss(parseFloat(e.target.value) || 0.1)}
                  className="w-full bg-zinc-800 border border-zinc-700 rounded px-2 py-1.5 text-xs font-mono text-zinc-200 focus:outline-none focus:border-amber-500/50"
                />
              </div>
              <div>
                <label className="text-[10px] text-zinc-500 block mb-1">Balance ($)</label>
                <input
                  type="number"
                  min="0"
                  step="1000"
                  value={customAccountBalance}
                  onChange={(e) => setCustomAccountBalance(parseFloat(e.target.value) || 100000)}
                  className="w-full bg-zinc-800 border border-zinc-700 rounded px-2 py-1.5 text-xs font-mono text-zinc-200 focus:outline-none focus:border-amber-500/50"
                />
              </div>
              <div>
                <label className="text-[10px] text-zinc-500 block mb-1">Price ($)</label>
                <input
                  type="number"
                  min="0"
                  step="0.01"
                  value={customCurrentPrice}
                  onChange={(e) => setCustomCurrentPrice(parseFloat(e.target.value) || 0)}
                  className="w-full bg-zinc-800 border border-zinc-700 rounded px-2 py-1.5 text-xs font-mono text-zinc-200 focus:outline-none focus:border-amber-500/50"
                />
              </div>
            </div>
          </div>

          {/* Full Kelly Display */}
          <div className="rounded bg-zinc-800/30 p-2">
            <div className="flex items-center justify-between text-[10px]">
              <span className="text-zinc-500">Full Kelly: {Number.isFinite(Number(calculated.kellyPercentage)) ? (Number(calculated.kellyPercentage) * 100).toFixed(2) : '—'}%</span>
              <span className="text-zinc-600">Half-Kelly used for safety</span>
            </div>
          </div>

          {/* Formula Info */}
          <div className="rounded bg-zinc-800/20 p-2">
            <p className="text-[10px] text-zinc-500 font-mono">
              f* = (b×p - q) / b &nbsp;&nbsp; where b=Win/Loss ratio, p=win prob, q=loss prob
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
