"""
collector_utils.py — Shared provider cooldown + exponential backoff for data collectors.
Each collector imports _fetch_with_retry (sync) or _async_fetch_with_retry (async).
"""

import asyncio
import time

_provider_cooldowns: dict[str, float] = {}

PRICE_FETCH_ERROR_COOLDOWN = 20.0
PRICE_FETCH_RATE_LIMIT_COOLDOWN = 60.0
RETRY_BACKOFF_BASE = 0.5
MAX_RETRIES = 2


def _provider_cooldown_remaining(provider: str) -> float:
    return max(0.0, _provider_cooldowns.get(provider, 0.0) - time.time())


def _activate_provider_cooldown(provider: str, duration: float, reason: str):
    if duration <= 0:
        return
    until = time.time() + duration
    _provider_cooldowns[provider] = max(_provider_cooldowns.get(provider, 0.0), until)
    print(f"[{provider}] cooldown {duration:.0f}s ({reason})")


def _fetch_with_retry(provider: str, fetch_fn, *args, **kwargs):
    """Wrap a sync fetch function with cooldown check, retry+backoff, and cooldown on failure.
    Returns the fetch result on success, or None if cooldown active or all retries exhausted."""
    remaining = _provider_cooldown_remaining(provider)
    if remaining > 0:
        print(f"[{provider}] skipping — cooldown {remaining:.0f}s remaining")
        return None

    for attempt in range(MAX_RETRIES + 1):
        try:
            return fetch_fn(*args, **kwargs)
        except Exception as e:
            if attempt < MAX_RETRIES:
                delay = RETRY_BACKOFF_BASE * (2 ** attempt)
                print(f"[{provider}] retry {attempt+1}/{MAX_RETRIES} after {e.__class__.__name__}")
                time.sleep(delay)
                continue
            # Final failure — activate cooldown
            status_code = None
            if hasattr(e, "code"):
                status_code = e.code
            elif hasattr(e, "status"):
                status_code = e.status
            # requests.Response has .status_code on the response object
            if hasattr(e, "response") and hasattr(e.response, "status_code"):
                status_code = e.response.status_code

            if status_code == 429:
                _activate_provider_cooldown(provider, PRICE_FETCH_RATE_LIMIT_COOLDOWN, "429")
            elif status_code is not None and status_code >= 500:
                _activate_provider_cooldown(provider, PRICE_FETCH_ERROR_COOLDOWN, f"5xx:{status_code}")
            else:
                _activate_provider_cooldown(provider, PRICE_FETCH_ERROR_COOLDOWN, e.__class__.__name__)

    return None


async def _async_fetch_with_retry(provider: str, fetch_fn, *args, **kwargs):
    """Async version — wrap an async fetch coroutine with cooldown, retry+backoff."""
    remaining = _provider_cooldown_remaining(provider)
    if remaining > 0:
        print(f"[{provider}] skipping — cooldown {remaining:.0f}s remaining")
        return None

    for attempt in range(MAX_RETRIES + 1):
        try:
            return await fetch_fn(*args, **kwargs)
        except Exception as e:
            if attempt < MAX_RETRIES:
                delay = RETRY_BACKOFF_BASE * (2 ** attempt)
                print(f"[{provider}] retry {attempt+1}/{MAX_RETRIES} after {e.__class__.__name__}")
                await asyncio.sleep(delay)
                continue
            status_code = None
            if hasattr(e, "status"):
                status_code = e.status
            elif hasattr(e, "code"):
                status_code = e.code

            if status_code == 429:
                _activate_provider_cooldown(provider, PRICE_FETCH_RATE_LIMIT_COOLDOWN, "429")
            elif status_code is not None and status_code >= 500:
                _activate_provider_cooldown(provider, PRICE_FETCH_ERROR_COOLDOWN, f"5xx:{status_code}")
            else:
                _activate_provider_cooldown(provider, PRICE_FETCH_ERROR_COOLDOWN, e.__class__.__name__)

    return None
