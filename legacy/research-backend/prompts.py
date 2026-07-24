"""
prompts.py
----------
Callable prompt generators matching main.py's imports.
Each function takes keyword args matching its template variables.
"""


def morning_brief(report: str) -> str:
    return f"""
You have the following crypto market report JSON:

{report}

Give me a morning brief covering:
1. The one trade I should be watching most closely today — and why
2. Any risk warnings I need to act on before markets open
3. A quick summary table: symbol | price | 24h change | signal | confidence
4. One unexpected insight I might miss scanning manually

Be concise. No filler. I read this before coffee.
"""


def entry_check(report: str, symbol: str, stop_loss, target) -> str:
    return f"""
You have the following crypto market report JSON:

{report}

I'm considering entering {symbol}. Based on the current signals:

1. Is there a clear entry signal right now, or should I wait?
2. What's the RSI telling me — is there room to run or am I buying the top?
3. The suggested stop is {stop_loss} and target is {target}. Do these look reasonable given the asset's recent volatility?
4. What would change my mind — what specific condition would flip the signal from whatever it is now?

Be honest. If the signal is weak, say so. Don't talk me into anything.
"""


def risk_scan(report: str) -> str:
    return f"""
You have the following crypto market report JSON:

{report}

Scan every asset and tell me:

1. Which asset has the most conflicting signals right now? Explain the conflict.
2. Is any asset showing a critical on-chain risk flag, and what does MistTrack's data say?
3. Which asset has the highest confidence signal and does the suggestion actually match the alert conditions?
4. If I could only act on one thing today (entry, exit, or just paying attention), what should it be?

Rate risk as: LOW | MEDIUM | HIGH | CRITICAL for each asset, with a one-line reason.
"""


def weekly_recap(reports: str) -> str:
    return f"""
You have the following crypto market report JSON from the past 7 days:

{reports}

Review the week:

1. Which signals were correct? Which were false starts? Be honest about misses.
2. Did any asset show a pattern — e.g., RSI bouncing off 30 repeatedly, or volume spiking before a move?
3. What's changed in the macro sentiment from Exa data over the week?
4. Based on this week's data, should I adjust any trigger thresholds (RSI, volume spike multiplier)?

Output a scorecard: asset | signals given | hit rate | lessons
"""


def full_pipeline(report: str) -> str:
    return f"""
Run a full research check against the current market data:

{report}

For each asset in my watchlist (BTC, ETH, SOL, TON, DOGE), tell me:

1. Is this a buy, sell, or wait zone? Why?
2. What's the one thing that would change your answer?
3. If you had to put a confidence % on that call, what would it be?

Then give me your best overall play for today — the asset with the clearest signal and highest confidence. Explain it in one paragraph.
"""
