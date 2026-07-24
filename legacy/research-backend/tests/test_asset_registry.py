import pytest

from asset_registry import (
    ASSET_REGISTRY,
    AssetRoute,
    AssetRoutingError,
    CRYPTO_SYMBOLS,
    require_execution_route,
    resolve_asset,
)


@pytest.mark.parametrize("symbol", CRYPTO_SYMBOLS)
def test_all_baseline_crypto_assets_are_classified_only_as_crypto(symbol):
    route = resolve_asset(symbol)
    assert route is not None
    assert route.execution_adapter == "crypto"
    assert route.asset_class == "crypto"
    assert route.execution_adapter != "alpaca"


def test_unknown_asset_is_denied():
    with pytest.raises(AssetRoutingError, match="REJECT_UNKNOWN_ASSET"):
        require_execution_route("UNKNOWN", "paper")


def test_disabled_asset_is_denied_without_fallback():
    with pytest.raises(AssetRoutingError, match="REJECT_DISABLED_ASSET"):
        require_execution_route("ADA", "paper")


def test_missing_route_metadata_is_denied(monkeypatch):
    route = AssetRoute("test:X", "X", "equity", "stock", "X", "USD", "test", "X", "alpaca", None, "X", None, ("paper",), "ACTIVE")
    monkeypatch.setitem(ASSET_REGISTRY, "X", route)
    with pytest.raises(AssetRoutingError, match="REJECT_ROUTE_UNAVAILABLE"):
        require_execution_route("X", "paper")


def test_mode_not_allowed_is_denied():
    with pytest.raises(AssetRoutingError, match="REJECT_MODE_NOT_ALLOWED"):
        require_execution_route("AAPL", "live")
