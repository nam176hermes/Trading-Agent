# Open-source and official reference map

These links were supplied by the referenced design conversation. They are a
starting map for later technical research; this workspace-preparation pass did
not vendor or execute third-party code.

## Execution and strategy engines

- [QuantConnect LEAN](https://github.com/QuantConnect/Lean)
- [LEAN CLI](https://github.com/QuantConnect/lean-cli)
- [Hummingbot](https://github.com/hummingbot/hummingbot)
- [Hummingbot API](https://github.com/hummingbot/hummingbot-api)
- [Hummingbot Strategy V2](https://hummingbot.org/strategies/v2-strategies/)
- [Freqtrade](https://github.com/freqtrade/freqtrade)
- [FreqAI](https://www.freqtrade.io/en/stable/freqai/)
- [Freqtrade lookahead analysis](https://www.freqtrade.io/en/stable/lookahead-analysis/)

## Research and ensemble patterns

- [TradingAgents](https://github.com/TauricResearch/TradingAgents)
- [Numerai example scripts](https://github.com/numerai/example-scripts)
- [Numerai True Contribution](https://docs.numer.ai/numerai-tournament/scoring/true-contribution-tc)
- [Numerai Feature Neutral Correlation](https://docs.numer.ai/numerai-tournament/scoring/feature-neutral-correlation)

## Product workflow references

- [Composer Symphonies](https://www.composer.trade/learn/how-composer-symphonies-work)
- [Public AI Agents](https://public.com/ai-agents)
- [Public Agents prompting guide](https://public.com/ai-agents/how-it-works)
- [Coinrule rule-based trading](https://coinrule.com/rule-based-trading/)
- [Trade Ideas Holly guide](https://www.trade-ideas.com/hollyguide/)
- [Bitsgap demo mode](https://bitsgap.com/helpdesk/article/13512068818332-How-to-use-Bitsgap-Demo-Mode)

## Validation, governance, and observability

- [Probability of Backtest Overfitting](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253)
- [Deflated Sharpe Ratio](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551)
- [OWASP Prompt Injection](https://genai.owasp.org/llmrisk/llm01-prompt-injection/)
- [OWASP Agentic AI threats](https://genai.owasp.org/resource/agentic-ai-threats-and-mitigations/)
- [NIST AI RMF](https://www.nist.gov/itl/ai-risk-management-framework)
- [LangGraph](https://github.com/langchain-ai/langgraph)
- [MLflow](https://github.com/mlflow/mlflow)
- [OpenTelemetry](https://opentelemetry.io/docs/)
- [Prometheus](https://prometheus.io/docs/introduction/overview/)

Do not add one of these systems merely because it appears here. Each adoption
requires an ADR covering scope, account ownership, operational cost, failure
modes, security, rollback, and compatibility with the one-engine-per-account
rule.
