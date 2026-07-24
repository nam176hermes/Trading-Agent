import { CollapsibleSection } from '@/components/trading/collapsible-section';
import { MacroDashboard } from '@/components/trading/macro-dashboard';
import { NewsFeed } from '@/components/trading/news-feed';
import PredictionMarketCard from '@/components/trading/prediction-market-card';
import SocialSentimentCard from '@/components/trading/social-sentiment-card';
import { BacktestResultsCard } from '@/components/trading/backtest-results-card';
import { ExchangeStatusCard } from '@/components/trading/exchange-status-card';
import { LivePositionsCard } from '@/components/trading/live-positions-card';

export function DashboardExtras() {
  return (
    <div className="space-y-3">
      <CollapsibleSection title="Macro Overview" defaultOpen={true}>
        <MacroDashboard collapsed={false} />
      </CollapsibleSection>
      <CollapsibleSection title="News & Sentiment" defaultOpen={true}>
        <NewsFeed collapsed={false} />
      </CollapsibleSection>
      <CollapsibleSection title="Prediction Markets" defaultOpen={true}>
        <PredictionMarketCard />
      </CollapsibleSection>
      <CollapsibleSection title="Social Sentiment" defaultOpen={true}>
        <SocialSentimentCard />
      </CollapsibleSection>
      <CollapsibleSection title="Backtest Results" defaultOpen={false}>
        <BacktestResultsCard />
      </CollapsibleSection>
      <CollapsibleSection title="Exchange Status" defaultOpen={false}>
        <ExchangeStatusCard />
      </CollapsibleSection>
      <CollapsibleSection title="Live Positions" defaultOpen={false}>
        <LivePositionsCard />
      </CollapsibleSection>
    </div>
  );
}
