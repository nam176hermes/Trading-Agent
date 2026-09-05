"""Explicit operator entry point for the separately authorized P3 acquisition operation."""

from __future__ import annotations

import sys
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.alpha_lifecycle.acquisition import RetryableTransportError, acquire_day
from packages.data_catalog.artifact_store import LocalArtifactStore


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class PublicArchiveTransport:
    def __init__(self) -> None:
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirect()
        )

    def get(self, url: str, *, timeout_seconds: int, max_bytes: int) -> bytes:
        if not url.startswith(
            "https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1d/"
        ):
            raise ValueError("URL is outside the accepted public archive prefix")
        request = urllib.request.Request(url, headers={"User-Agent": "trading-agent-p3/1"})
        try:
            with self._opener.open(request, timeout=timeout_seconds) as response:
                value = response.read(max_bytes + 1)
        except urllib.error.HTTPError as error:
            if error.code == 429 or 500 <= error.code <= 599:
                raise RetryableTransportError from error
            raise
        except TimeoutError as error:
            raise RetryableTransportError from error
        if len(value) > max_bytes:
            raise ValueError("response exceeds the accepted byte limit")
        return value


def main(start: date, end: date, root: Path) -> None:
    if start > end:
        raise ValueError("inclusive date range must be ordered")
    store = LocalArtifactStore(root)
    transport = PublicArchiveTransport()
    current = start
    while current <= end:
        print(current.isoformat(), acquire_day(current, transport, store).content_sha256)
        current += timedelta(days=1)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--store", type=Path, required=True)
    args = parser.parse_args()
    main(args.start, args.end, args.store)
