from unittest.mock import Mock

from services import sentry


def test_sentry_is_disabled_without_a_dsn(monkeypatch) -> None:
    initialize = Mock()
    monkeypatch.setattr(sentry.sentry_sdk, "init", initialize)

    assert not sentry.configure_sentry({})
    initialize.assert_not_called()


def test_sentry_uses_the_configured_dsn_and_environment(monkeypatch) -> None:
    initialize = Mock()
    monkeypatch.setattr(sentry.sentry_sdk, "init", initialize)

    assert sentry.configure_sentry(
        {"SENTRY_DSN": "https://public@example.ingest.sentry.io/1", "SENTRY_ENVIRONMENT": "prod"}
    )
    initialize.assert_called_once_with(
        dsn="https://public@example.ingest.sentry.io/1",
        environment="prod",
        send_default_pii=False,
    )
