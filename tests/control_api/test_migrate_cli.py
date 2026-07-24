import pytest

from trading_control.migrate import build_parser, main


def test_cli_defaults_to_dry_run_and_supports_required_options() -> None:
    parser = build_parser()
    default = parser.parse_args(["--source-root", "/tmp/source"])
    explicit = parser.parse_args(["--source-root", "/tmp/source", "--dry-run"])
    assert default.apply is False and explicit.apply is False
    parsed = parser.parse_args([
        "--source-root", "/tmp/source", "--domain", "decisions",
        "--limit", "100", "--source-file", "/tmp/source/memory/decisions.jsonl",
        "--resume", "run-id",
    ])
    assert parsed.domain == "decisions" and parsed.limit == 100


def test_real_root_apply_is_blocked_without_scoped_approval_before_source_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TRADING_REAL_APPLY_APPROVED", raising=False)
    with pytest.raises(SystemExit, match="real apply approval is not enabled"):
        main([
            "--source-root", "/home/thenam176/.hermes/crypto-research",
            "--apply",
        ])
