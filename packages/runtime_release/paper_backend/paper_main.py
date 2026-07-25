"""Fixed canonical paper snapshot entrypoint.

This module deliberately has no mode catalog. It consumes only the protected
semantic snapshot and unauthenticated public market data, then writes one
attributed research report. Legacy execution modules are not imported.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Mapping
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def _bootstrap_isolated_imports() -> None:
    if not sys.flags.isolated:
        return
    entrypoint = Path(__file__)
    try:
        if entrypoint.is_symlink():
            raise RuntimeError("paper entrypoint cannot be a symlink")
        resolved_entrypoint = entrypoint.resolve(strict=True)
        resolved_cwd = Path.cwd().resolve(strict=True)
    except OSError as exc:
        raise RuntimeError("paper entrypoint cannot be resolved") from exc
    backend_root = resolved_entrypoint.parent
    if resolved_entrypoint.name != "paper_main.py" or resolved_cwd != backend_root:
        raise RuntimeError("paper entrypoint must run from its sealed backend root")
    backend_root_text = str(backend_root)
    sys.path[:] = [item for item in sys.path if item != backend_root_text]
    sys.path.insert(0, backend_root_text)


_bootstrap_isolated_imports()
del _bootstrap_isolated_imports

from job_attribution import (  # noqa: E402
    ResearchInvocationError,
    bootstrap_strict_worker_invocation,
    with_lineage,
    write_json_exclusive,
)
from research_semantics import (  # noqa: E402
    APPROVED_RESEARCH_INPUT_ROOT,
    SnapshotSemanticInputs,
    load_snapshot_semantic_inputs,
)


WATCHLIST = ("BTC", "ETH", "SOL", "TON", "DOGE", "ADA", "AVAX", "DOT", "LINK", "MATIC")
_COIN_IDS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "TON": "the-open-network",
    "DOGE": "dogecoin",
    "ADA": "cardano",
    "AVAX": "avalanche-2",
    "DOT": "polkadot",
    "LINK": "chainlink",
    "MATIC": "matic-network",
}
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024


def _public_market_snapshot() -> Mapping[str, Mapping[str, object]]:
    query = urlencode(
        {
            "vs_currency": "usd",
            "ids": ",".join(_COIN_IDS[symbol] for symbol in WATCHLIST),
            "price_change_percentage": "24h",
            "per_page": str(len(WATCHLIST)),
            "page": "1",
        }
    )
    request = Request(
        f"https://api.coingecko.com/api/v3/coins/markets?{query}",
        headers={"Accept": "application/json", "User-Agent": "trading-agent-paper/1"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=15) as response:  # noqa: S310
            if response.status != 200:
                raise ValueError("public market response was not successful")
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise ValueError("public market response exceeded the limit")
        decoded = json.loads(raw)
        if not isinstance(decoded, list):
            raise ValueError("public market response was not a list")
    except Exception:
        return {}
    by_id = {
        item.get("id"): item
        for item in decoded
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    return {
        symbol: by_id[coin_id]
        for symbol, coin_id in _COIN_IDS.items()
        if coin_id in by_id
    }


def build_snapshot_report(
    semantic: SnapshotSemanticInputs,
    market: Mapping[str, Mapping[str, object]],
) -> dict[str, object]:
    assets: list[dict[str, object]] = []
    for symbol in WATCHLIST:
        observed = market.get(symbol, {})
        assets.append(
            {
                "symbol": symbol,
                "source": "coingecko_public" if observed else "unavailable",
                "current_price": observed.get("current_price"),
                "market_cap": observed.get("market_cap"),
                "volume_24h": observed.get("total_volume"),
                "price_change_24h_pct": observed.get("price_change_percentage_24h"),
                "sentiment": dict(semantic.sentiment_for(symbol)),
                "onchain": dict(semantic.onchain_for(symbol)),
                "macro_regime": semantic.macro_regime,
                "suggestion": "NO SIGNAL",
                "confidence": 0.0,
            }
        )
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "artifact_class": "CANONICAL_PAPER_V1",
        "assets": assets,
        "semantic_input_fingerprint": semantic.source_fingerprint,
    }


def main() -> int:
    if sys.argv[1:]:
        raise ResearchInvocationError("canonical paper snapshot accepts no command arguments")
    invocation = bootstrap_strict_worker_invocation()
    if invocation is None or invocation.reports_dir is None:
        raise ResearchInvocationError("canonical paper snapshot requires attributed worker context")
    semantic = load_snapshot_semantic_inputs(APPROVED_RESEARCH_INPUT_ROOT)
    report = with_lineage(build_snapshot_report(semantic, _public_market_snapshot()), invocation)
    suffix = invocation.attempt_id
    if suffix is None:
        raise ResearchInvocationError("canonical paper snapshot requires an attempt ID")
    write_json_exclusive(invocation.reports_dir, f"report_{suffix}.json", report)
    print(json.dumps(report, ensure_ascii=False, separators=(",", ":"), default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
