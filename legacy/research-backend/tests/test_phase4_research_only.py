import argparse
import asyncio
import builtins
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


FORBIDDEN_RESEARCH_IMPORTS = {
    "paper_trader",
    "broker",
    "execute_live",
    "exchange.ccxt_bridge",
    "backtest_gate",
    "ccxt",
    "ccxt.async_support",
}

FORBIDDEN_TRADING_ENV = {
    "BINANCE_API_KEY",
    "BINANCE_API_SECRET",
    "KALSHI_API_KEY_ID",
    "KALSHI_PRIVATE_KEY_PATH",
    "ALPACA_API_KEY",
    "ALPACA_SECRET_KEY",
    "TRADING_MASTER_KEY",
    "LIVE_EXECUTION_ENABLED",
    "LIVE_TRADING_ENABLED",
}


class FakeScratchpad:
    def init_session(self, **_kwargs):
        pass

    def log_thinking(self, *_args, **_kwargs):
        pass

    def log_tool_call(self, *_args, **_kwargs):
        pass

    def log_validation(self, *_args, **_kwargs):
        pass

    def log_final_decision(self, *_args, **_kwargs):
        pass

    def save(self):
        pass


@pytest.fixture
def main_module():
    import main

    return main


@pytest.fixture
def isolated_pipeline(monkeypatch, main_module):
    main = main_module
    calls = []

    async def collect_all(_symbols, allow_exchange=True):
        calls.append(("collect_all", allow_exchange))
        return {"BTC": {"current_price": 100.0, "ohlcv": None}}

    async def empty_async(*_args, **_kwargs):
        return {}

    monkeypatch.setattr(main, "collect_all", collect_all)
    monkeypatch.setattr(main, "fetch_sentiment", empty_async)
    monkeypatch.setattr(main, "fetch_onchain_risk", empty_async)
    monkeypatch.setattr(main, "fetch_derivatives", empty_async)
    monkeypatch.setattr(main, "build_memory_context", lambda _symbol: "")
    monkeypatch.setattr(main, "build_enriched_context", lambda _symbol: "")
    monkeypatch.setattr(main, "assemble_asset_json", lambda **_kwargs: {
        "symbol": "BTC",
        "suggestion": "BUY",
        "confidence": 0.8,
        "price": 100.0,
        "market_regime": "trending_up",
        "rationale": "fixture",
        "stop_loss_suggestion": 90.0,
        "target_suggestion": 120.0,
    })
    monkeypatch.setattr(main, "assemble_full_report", lambda assets: {
        "schema_version": "legacy-fixture",
        "assets": assets,
    })
    monkeypatch.setattr(main, "validate_data_completeness", lambda _asset: (True, "ok"))
    monkeypatch.setattr(main, "validate_consistency", lambda _report: (True, []))
    monkeypatch.setattr(main, "parse_report", lambda _report: [])
    monkeypatch.setattr(main, "score_decision", lambda *_args: 1.0)
    monkeypatch.setattr(main, "store_decision", lambda **_kwargs: None)

    import kill_switch

    monkeypatch.setattr(kill_switch, "is_kill_switch_active", lambda: False)

    for collector_name in (
        "adanos_collector",
        "kalshi_collector",
        "orderflow_collector",
        "polymarket_collector",
    ):
        module = ModuleType(collector_name)
        module.collect = lambda name=collector_name: calls.append(name)
        monkeypatch.setitem(sys.modules, collector_name, module)

    paper_trader = ModuleType("paper_trader")
    paper_trader._load_report_prices = lambda: {"BTC": 100.0}
    paper_trader.check_stops = lambda _prices: calls.append("check_stops") or []
    paper_trader.execute_signal = lambda signal, _prices: (
        calls.append("paper_execute")
        or {
            "status": "filled",
            "symbol": signal["asset"],
            "shares": 1.0,
            "side": signal["action"],
        }
    )
    paper_trader.load_portfolio = lambda: {}
    paper_trader.get_portfolio_value = lambda *_args: 0.0
    paper_trader.save_portfolio = lambda *_args: None
    monkeypatch.setitem(sys.modules, "paper_trader", paper_trader)

    broker = ModuleType("broker")
    broker.execute = lambda *_args: calls.append("broker_execute") or {"status": "filled"}
    broker.is_configured = lambda: True
    monkeypatch.setitem(sys.modules, "broker", broker)

    alert_manager = ModuleType("alert_manager")
    alert_manager.send_telegram_text = lambda *_args, **_kwargs: None
    alert_manager.process_alerts = lambda _signals: 0
    monkeypatch.setitem(sys.modules, "alert_manager", alert_manager)

    execute_live = ModuleType("execute_live")
    execute_live.execute_signal = lambda *_args: calls.append("live_execute") or {"status": "filled"}
    monkeypatch.setitem(sys.modules, "execute_live", execute_live)

    bridge = ModuleType("exchange.ccxt_bridge")
    bridge.get_mode = lambda: "paper"
    monkeypatch.setitem(sys.modules, "exchange.ccxt_bridge", bridge)

    gate = ModuleType("backtest_gate")
    gate.check = lambda _symbol: calls.append("backtest_gate") or {
        "status": "allow",
        "position_modifier": 1.0,
    }
    monkeypatch.setitem(sys.modules, "backtest_gate", gate)

    # Existing globals are present before the production imports become lazy.
    for name, value in {
        "check_stops": paper_trader.check_stops,
        "paper_execute": paper_trader.execute_signal,
        "broker_execute": broker.execute,
        "broker_configured": broker.is_configured,
        "live_execute": execute_live.execute_signal,
        "get_execution_mode": bridge.get_mode,
    }.items():
        if hasattr(main, name):
            monkeypatch.setattr(main, name, value)

    return main, calls


def test_importing_main_does_not_import_execution_modules():
    code = (
        "import json, sys; import main; "
        f"print(json.dumps(sorted(set({sorted(FORBIDDEN_RESEARCH_IMPORTS)!r}) & set(sys.modules))))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(result.stdout.strip()) == []


def test_real_research_import_graph_never_reads_trading_credentials_or_env_files():
    code = f'''\
import builtins, os, pathlib

forbidden_env = {FORBIDDEN_TRADING_ENV!r}
class GuardedEnviron(dict):
    def __getitem__(self, key):
        if key in forbidden_env:
            raise AssertionError("trading credential read: " + key)
        return super().__getitem__(key)
    def get(self, key, default=None):
        if key in forbidden_env:
            raise AssertionError("trading credential read: " + key)
        return super().get(key, default)
os.environ = GuardedEnviron(os.environ)

real_read_text = pathlib.Path.read_text
def guarded_read_text(path, *args, **kwargs):
    resolved = str(path.expanduser())
    if path.name == ".env":
        raise AssertionError("env file read: " + resolved)
    return real_read_text(path, *args, **kwargs)
pathlib.Path.read_text = guarded_read_text

real_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name == "ccxt" or name.startswith("ccxt.") or name == "exchange.ccxt_bridge":
        raise AssertionError("exchange import: " + name)
    return real_import(name, *args, **kwargs)
builtins.__import__ = guarded_import

import main
assert not any(name == "ccxt" or name.startswith("ccxt.") for name in __import__("sys").modules)
'''
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True)


def test_real_research_pipeline_debate_and_vendor_fallback_are_exchange_free():
    code = f'''\
import asyncio, builtins, json, os, pathlib, sys
from types import ModuleType

forbidden_env = {FORBIDDEN_TRADING_ENV!r}
class GuardedEnviron(dict):
    def __getitem__(self, key):
        if key in forbidden_env:
            raise AssertionError("trading credential read: " + key)
        return super().__getitem__(key)
    def get(self, key, default=None):
        if key in forbidden_env:
            raise AssertionError("trading credential read: " + key)
        return super().get(key, default)
os.environ = GuardedEnviron(os.environ)

real_read_text = pathlib.Path.read_text
def guarded_read_text(path, *args, **kwargs):
    resolved = str(path.expanduser())
    if path.name == ".env":
        raise AssertionError("env file read: " + resolved)
    return real_read_text(path, *args, **kwargs)
pathlib.Path.read_text = guarded_read_text

restricted_collectors = {{"adanos_collector", "kalshi_collector", "orderflow_collector"}}
forbidden_import_attempts = []
real_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if (name == "ccxt" or name.startswith("ccxt.")
            or name == "exchange.ccxt_bridge" or name in restricted_collectors):
        forbidden_import_attempts.append(name)
        raise AssertionError("forbidden research import: " + name)
    return real_import(name, *args, **kwargs)
builtins.__import__ = guarded_import

import main, data_collector, data_vendors, derivatives_collector, kill_switch
import live_data, macro, macro_data, urllib.request
from schemas import RiskAssessment
kill_switch.is_kill_switch_active = lambda: False

attempted = []
def vendor(name, result=None):
    async def fake(_session, symbol, **_kwargs):
        attempted.append(name)
        if callable(result):
            return result(symbol)
        return result
    return fake
for vendor_name, methods in data_vendors.VENDOR_FUNCTIONS.items():
    for method in tuple(methods):
        value = None
        if vendor_name == "coingecko" and method == "get_price":
            value = lambda symbol: {{
                "symbol": symbol, "current_price": 100.0, "source": "fixture"
            }}
        methods[method] = vendor(vendor_name, value)

class FakeSession:
    async def __aenter__(self): return self
    async def __aexit__(self, *_args): return False
    def get(self, url, *_args, **_kwargs):
        network_urls.append(url)
        raise AssertionError("aiohttp network call: " + url)
network_urls = []
data_collector.aiohttp.ClientSession = FakeSession

real_urlopen = urllib.request.urlopen
def guarded_urlopen(request, *_args, **_kwargs):
    url = getattr(request, "full_url", str(request))
    network_urls.append(url)
    raise AssertionError("urllib network call: " + url)
urllib.request.urlopen = guarded_urlopen

# Keep the real debate -> live_data -> collect_macro nesting, replacing only
# macro's network boundaries with deterministic empty data.
macro.fetch_worldbank = lambda: {{}}
macro.fetch_yfinance = lambda: {{}}
macro_data.get_macro_snapshot = lambda: {{}}
macro_data.format_macro_context = lambda _snapshot: "fixture macro snapshot"

class Pad:
    def __getattr__(self, _name): return lambda *_args, **_kwargs: None

class FakeRisk:
    def __init__(self, *_args): pass
    async def run(self, *_args):
        return [RiskAssessment(
            persona="neutral", accept_signal=True, position_size_pct=2.0,
            rationale="The deterministic fixture accepts this reward to risk.",
        )]

async def fake_llm(_prompt, system="", **_kwargs):
    if "technical analyst" in system:
        return json.dumps({{
            "asset": "BTC", "trend": "bullish", "support_levels": [90.0],
            "resistance_levels": [130.0], "volume_profile": "normal",
            "key_signals": ["fixture momentum"],
            "summary": "A deterministic technical fixture with bullish momentum."
        }})
    if "sentiment analyst" in system:
        return json.dumps({{
            "asset": "BTC", "social_volume": "normal", "funding_bias": "neutral",
            "key_signals": ["fixture sentiment"],
            "summary": "A deterministic sentiment fixture with neutral positioning."
        }})
    if "macro analyst" in system:
        return json.dumps({{
            "asset": "BTC", "etf_flows": "neutral", "regulatory_status": "neutral",
            "macro_correlation": "moderate", "key_events": ["fixture macro"],
            "summary": "A deterministic macro fixture with neutral conditions."
        }})
    if "arguing the bullish case" in system:
        return json.dumps({{
            "thesis": "Bull fixture", "supporting_signals": ["fixture momentum"],
            "conviction": 0.8
        }})
    if "arguing the bearish case" in system:
        return json.dumps({{
            "thesis": "Bear fixture", "risk_factors": ["fixture volatility"],
            "conviction": 0.4
        }})
    if "synthesize debate arguments" in system:
        return "A deterministic synthesis that preserves the fixture thesis."
    return json.dumps({{
        "asset": "BTC",
        "original_signal": {{
            "asset": "BTC", "action": "BUY", "confidence": 0.8,
            "entry_price": 100.0, "stop_loss": 90.0, "take_profit": 130.0,
            "time_horizon": "swing",
            "reasoning": "A sufficiently detailed fixture rationale for a trade."
        }},
        "action": "ratify",
        "rationale": "The accepted reward to risk supports this fixture decision.",
        "conviction": 0.8
    }})

main.RiskDebate = FakeRisk
main.call_llm = fake_llm
main.calculate_indicators = lambda *_args: None
main.detect_regime = lambda *_args: None
main.fetch_sentiment = lambda *_args, **_kwargs: asyncio.sleep(0, result={{}})
main.fetch_onchain_risk = lambda *_args: asyncio.sleep(0, result={{}})
main.build_memory_context = lambda *_args: ""
main.build_enriched_context = lambda *_args: ""
main.get_memory_for_bull = lambda *_args, **_kwargs: ""
main.get_memory_for_bear = lambda *_args, **_kwargs: ""
main.assemble_asset_json = lambda **_kwargs: {{
    "symbol": "BTC", "suggestion": "BUY", "confidence": 0.8,
    "price": 100.0, "market_regime": "trending_up",
    "rationale": "A sufficiently detailed fixture rationale for a trade.",
    "stop_loss_suggestion": 90.0, "target_suggestion": 130.0,
}}
main.assemble_full_report = lambda assets: {{"schema_version": "fixture", "assets": assets}}
main.validate_data_completeness = lambda *_args: (True, "ok")
main.validate_consistency = lambda *_args: (True, [])
main.parse_report = lambda *_args: []
main.store_decision = lambda **_kwargs: None
main.score_decision = lambda *_args: 1.0

polymarket = ModuleType("polymarket_collector")
polymarket.collect = lambda: None
sys.modules["polymarket_collector"] = polymarket

report = asyncio.run(main.run_pipeline(
    ["BTC"], enable_debate=True, enable_risk_personas=True,
    pad=Pad(), allow_execution=False,
))
asset = report["assets"][0]
assert asset["portfolio_decision"]["action"] == "ratify"
assert "binance" not in attempted and "ccxt_binance" not in attempted
assert attempted == ["coingecko", "coingecko"]
assert not any("fapi.binance.com" in url for url in network_urls), network_urls
assert not any("api.binance.com" in url for url in network_urls), network_urls
assert forbidden_import_attempts == [], forbidden_import_attempts
assert not any(name == "ccxt" or name.startswith("ccxt.") for name in sys.modules)
'''
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True)


def test_importing_main_does_not_load_shared_dotenv():
    code = (
        "import dotenv; "
        "dotenv.load_dotenv=lambda *a, **k: (_ for _ in ()).throw(AssertionError('dotenv loaded')); "
        "import main"
    )

    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True)


def test_strict_worker_import_never_writes_under_immutable_cwd(tmp_path):
    """A valid worker invocation must import from a read-only release tree."""
    immutable_cwd = Path.cwd()
    reports = tmp_path / "reports"
    signals = tmp_path / "signals"
    reports.mkdir()
    signals.mkdir()
    code = f'''
import builtins, logging, os, pathlib
from types import SimpleNamespace

cwd = pathlib.Path({str(immutable_cwd)!r}).resolve()
reports = pathlib.Path({str(reports)!r}).resolve()
signals = pathlib.Path({str(signals)!r}).resolve()
os.environ.update({{
    "TRADING_JOB_ID": "job_0123456789abcdef0123456789abcdef",
    "TRADING_JOB_ATTEMPT_ID": "attempt_fedcba9876543210fedcba9876543210",
    "TRADING_RESEARCH_BACKEND_COMMIT": "a" * 40,
    "TRADING_REPORTS_DIR": str(reports),
    "TRADING_SIGNAL_OUTPUT_DIR": str(signals),
}})

def under_cwd(value):
    path = pathlib.Path(value)
    resolved = (path if path.is_absolute() else cwd / path).resolve()
    return resolved == cwd or cwd in resolved.parents

real_mkdir = pathlib.Path.mkdir
def guarded_mkdir(path, *args, **kwargs):
    if under_cwd(path):
        raise AssertionError("mkdir under immutable cwd: " + str(path))
    return real_mkdir(path, *args, **kwargs)
pathlib.Path.mkdir = guarded_mkdir

real_open = builtins.open
def guarded_open(file, mode="r", *args, **kwargs):
    if any(flag in mode for flag in "wax+") and under_cwd(file):
        raise AssertionError("write under immutable cwd: " + str(file))
    return real_open(file, mode, *args, **kwargs)
builtins.open = guarded_open

real_file_handler = logging.FileHandler
def guarded_file_handler(filename, *args, **kwargs):
    if under_cwd(filename):
        raise AssertionError("file log under immutable cwd: " + str(filename))
    return real_file_handler(filename, *args, **kwargs)
logging.FileHandler = guarded_file_handler

import job_attribution
job_attribution.resolve_research_invocation = lambda *_a, **_k: SimpleNamespace(
    job_id=os.environ["TRADING_JOB_ID"],
    attempt_id=os.environ["TRADING_JOB_ATTEMPT_ID"],
    research_only=True,
    backend_commit=os.environ["TRADING_RESEARCH_BACKEND_COMMIT"],
    reports_dir=reports,
    signal_output_dir=signals,
    replay_scratchpad_root=None,
)
import main
assert main.STRICT_WORKER_INVOCATION.job_id == os.environ["TRADING_JOB_ID"]
import macro, news_collector, onchain_collector, sentiment_collector, yfinance_collector
'''
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True)


def test_malformed_worker_environment_is_rejected_before_import_write():
    code = r'''
import os, pathlib
cwd = pathlib.Path.cwd().resolve()
os.environ.update({
    "TRADING_JOB_ID": "invalid",
    "TRADING_JOB_ATTEMPT_ID": "attempt_fedcba9876543210fedcba9876543210",
    "TRADING_RESEARCH_BACKEND_COMMIT": "a" * 40,
    "TRADING_REPORTS_DIR": "/tmp/arbitrary-reports",
    "TRADING_SIGNAL_OUTPUT_DIR": "/tmp/arbitrary-signals",
})
real_mkdir = pathlib.Path.mkdir
def guarded_mkdir(path, *args, **kwargs):
    resolved = (path if path.is_absolute() else cwd / path).resolve()
    if resolved == cwd or cwd in resolved.parents:
        raise AssertionError("write happened before validation")
    return real_mkdir(path, *args, **kwargs)
pathlib.Path.mkdir = guarded_mkdir
try:
    import main
except Exception as exc:
    assert exc.__class__.__name__ == "ResearchInvocationError", repr(exc)
    assert "job ID" in str(exc)
else:
    raise AssertionError("malformed worker environment accepted")
'''
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True)


def test_unattributed_output_override_is_rejected_before_import_write():
    code = r'''
import os, pathlib
cwd = pathlib.Path.cwd().resolve()
os.environ.update({
    "TRADING_REPORTS_DIR": "/tmp/arbitrary-reports",
    "TRADING_SIGNAL_OUTPUT_DIR": "/tmp/arbitrary-signals",
})
def guarded_mkdir(path, *_args, **_kwargs):
    resolved = (path if path.is_absolute() else cwd / path).resolve()
    if resolved == cwd or cwd in resolved.parents:
        raise AssertionError("write happened before validation")
    raise AssertionError("unexpected mkdir: " + str(path))
pathlib.Path.mkdir = guarded_mkdir
try:
    import main
except Exception as exc:
    assert exc.__class__.__name__ == "ResearchInvocationError", repr(exc)
    assert "job ID" in str(exc)
else:
    raise AssertionError("unattributed worker output override accepted")
'''
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True)


def test_parser_accepts_research_only_for_approved_modes(monkeypatch, main_module):
    for mode in ("snapshot", "debate", "backtest", "replay"):
        monkeypatch.setattr(sys, "argv", ["main.py", "--mode", mode, "--research-only"])
        args = main_module.parse_args()
        assert args.research_only is True


@pytest.mark.parametrize(
    "mode",
    ["poll", "brief", "entry", "risk", "reflect", "plan", "risk-check", "health-check", "pairs"],
)
def test_parser_rejects_research_only_for_nonapproved_modes(monkeypatch, main_module, mode):
    monkeypatch.setattr(sys, "argv", ["main.py", "--mode", mode, "--research-only"])
    with pytest.raises(SystemExit) as exc_info:
        main_module.parse_args()
    assert exc_info.value.code == 2


def _approved_output_tree(tmp_path, monkeypatch):
    import job_attribution

    root = tmp_path / "research-output"
    reports = root / "reports"
    signals = root / "signals"
    for directory in (root, reports, signals):
        directory.mkdir(mode=0o700)
        directory.chmod(0o700)
    monkeypatch.setattr(job_attribution, "APPROVED_RESEARCH_OUTPUT_ROOT", root)
    return reports, signals


@pytest.fixture
def secure_tmp_path():
    path = Path(tempfile.mkdtemp(prefix="phase4-backend-output-", dir="/home/thenam176/.cache"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _worker_environment(reports, signals):
    return {
        "TRADING_JOB_ID": "job_0123456789abcdef0123456789abcdef",
        "TRADING_JOB_ATTEMPT_ID": "attempt_fedcba9876543210fedcba9876543210",
        "TRADING_RESEARCH_BACKEND_COMMIT": "a" * 40,
        "TRADING_REPORTS_DIR": str(reports),
        "TRADING_SIGNAL_OUTPUT_DIR": str(signals),
    }


def _worker_replay_environment(reports, signals, scratchpad_root):
    source = _worker_environment(reports, signals)
    source["TRADING_RESEARCH_SCRATCHPAD_ROOT"] = str(scratchpad_root)
    return source


def _write_private_replay(path, payload):
    if isinstance(payload, bytes):
        path.write_bytes(payload)
    else:
        path.write_text(payload)
    path.chmod(0o600)


def test_phase4_worker_accepts_only_dedicated_scratchpad_boundary(
    secure_tmp_path, monkeypatch,
):
    import job_attribution

    reports, signals = _approved_output_tree(secure_tmp_path, monkeypatch)
    approved = Path(
        "/home/thenam176/.local/run/trading-agent/research-home/scratchpad"
    )
    assert job_attribution.APPROVED_WORKER_SCRATCHPAD_ROOT == approved

    invocation = job_attribution.resolve_research_invocation(
        True, _worker_replay_environment(reports, signals, approved),
    )
    assert invocation.replay_scratchpad_root == approved

    for rejected in (
        Path("/srv/legacy-research/.dexter/scratchpad"),
        secure_tmp_path / "arbitrary-scratchpad",
    ):
        with pytest.raises(job_attribution.ResearchInvocationError, match="scratchpad"):
            job_attribution.resolve_research_invocation(
                True, _worker_replay_environment(reports, signals, rejected),
            )


def test_manual_research_invocation_without_worker_environment_is_compatible():
    from job_attribution import resolve_research_invocation

    invocation = resolve_research_invocation(True, {})

    assert invocation.job_id is None
    assert invocation.attempt_id is None
    assert invocation.reports_dir is None
    assert invocation.signal_output_dir is None
    assert invocation.research_only is True


def test_worker_uses_attested_backend_commit_without_git(secure_tmp_path, monkeypatch):
    import job_attribution

    reports, signals = _approved_output_tree(secure_tmp_path, monkeypatch)
    monkeypatch.setattr(job_attribution, "_read_backend_commit", lambda: None)

    invocation = job_attribution.resolve_research_invocation(
        True, _worker_environment(reports, signals),
    )

    assert invocation.backend_commit == "a" * 40


@pytest.mark.parametrize("commit", [None, "A" * 40, "a" * 39, "g" * 40, "a" * 41])
def test_worker_rejects_missing_or_invalid_attested_backend_commit(
    secure_tmp_path, monkeypatch, commit,
):
    from job_attribution import ResearchInvocationError, resolve_research_invocation

    reports, signals = _approved_output_tree(secure_tmp_path, monkeypatch)
    source = _worker_environment(reports, signals)
    if commit is None:
        del source["TRADING_RESEARCH_BACKEND_COMMIT"]
    else:
        source["TRADING_RESEARCH_BACKEND_COMMIT"] = commit

    with pytest.raises(ResearchInvocationError, match="backend commit"):
        resolve_research_invocation(True, source)


def test_manual_invocation_ignores_no_worker_attestation_aliases():
    from job_attribution import ResearchInvocationError, resolve_research_invocation

    with pytest.raises(ResearchInvocationError, match="worker"):
        resolve_research_invocation(True, {"TRADING_RESEARCH_BACKEND_COMMIT": "a" * 40})
    with pytest.raises(ResearchInvocationError, match="worker"):
        resolve_research_invocation(True, {
            "TRADING_RESEARCH_SCRATCHPAD_ROOT":
                "/srv/legacy-research/.dexter/scratchpad",
        })


@pytest.mark.parametrize(
    "source",
    [
        {"TRADING_JOB_ID": "job_0123456789abcdef0123456789abcdef"},
        {"TRADING_JOB_ATTEMPT_ID": "attempt_fedcba9876543210fedcba9876543210"},
        {
            "TRADING_JOB_ID": "JOB_0123456789abcdef0123456789abcdef",
            "TRADING_JOB_ATTEMPT_ID": "attempt_fedcba9876543210fedcba9876543210",
        },
        {
            "TRADING_JOB_ID": "job_0123456789abcdef0123456789abcdeg",
            "TRADING_JOB_ATTEMPT_ID": "attempt_fedcba9876543210fedcba9876543210",
        },
        {
            "TRADING_JOB_ID": "job_0123456789abcdef0123456789abcdef",
            "TRADING_JOB_ATTEMPT_ID": "fedcba98-7654-3210-fedc-ba9876543210",
        },
        {"TRADING_ATTEMPT_ID": "attempt_fedcba9876543210fedcba9876543210"},
    ],
)
def test_worker_attribution_rejects_partial_invalid_or_legacy_ids(source):
    from job_attribution import ResearchInvocationError, resolve_research_invocation

    with pytest.raises(ResearchInvocationError):
        resolve_research_invocation(True, source)


@pytest.mark.parametrize(
    "path_case",
    ["relative", "traversal", "outside", "wrong_leaf", "symlink", "root_mode", "mode"],
)
def test_research_output_paths_reject_unsafe_roots(secure_tmp_path, monkeypatch, path_case):
    from job_attribution import ResearchInvocationError, resolve_research_invocation

    tmp_path = secure_tmp_path
    reports, signals = _approved_output_tree(tmp_path, monkeypatch)
    source = _worker_environment(reports, signals)
    if path_case == "relative":
        source["TRADING_REPORTS_DIR"] = "relative/reports"
    elif path_case == "traversal":
        source["TRADING_REPORTS_DIR"] = str(reports.parent / ".." / "reports")
    elif path_case == "outside":
        outside = tmp_path / "outside"
        outside.mkdir(mode=0o700)
        outside.chmod(0o700)
        source["TRADING_REPORTS_DIR"] = str(outside)
    elif path_case == "wrong_leaf":
        arbitrary = reports.parent / "arbitrary"
        arbitrary.mkdir(mode=0o700)
        arbitrary.chmod(0o700)
        source["TRADING_REPORTS_DIR"] = str(arbitrary)
    elif path_case == "symlink":
        real = reports.with_name("real-reports")
        reports.rename(real)
        reports.symlink_to(real, target_is_directory=True)
    elif path_case == "root_mode":
        reports.parent.chmod(0o755)
    else:
        reports.chmod(0o755)

    with pytest.raises(ResearchInvocationError):
        resolve_research_invocation(True, source)


def test_worker_invocation_requires_both_exact_dedicated_output_directories(secure_tmp_path, monkeypatch):
    from job_attribution import ResearchInvocationError, resolve_research_invocation

    reports, signals = _approved_output_tree(secure_tmp_path, monkeypatch)
    source = _worker_environment(reports, signals)
    invocation = resolve_research_invocation(True, source)
    assert invocation.reports_dir == reports
    assert invocation.signal_output_dir == signals

    del source["TRADING_SIGNAL_OUTPUT_DIR"]
    with pytest.raises(ResearchInvocationError):
        resolve_research_invocation(True, source)


def test_exclusive_result_collision_never_removes_existing_output(secure_tmp_path, monkeypatch):
    from job_attribution import resolve_research_invocation, write_json_exclusive

    reports, signals = _approved_output_tree(secure_tmp_path, monkeypatch)
    invocation = resolve_research_invocation(True, _worker_environment(reports, signals))
    filename = f"report_{invocation.attempt_id}.json"
    existing = reports / filename
    existing.write_text("original")

    with pytest.raises(FileExistsError):
        write_json_exclusive(reports, filename, {"replacement": True})

    assert existing.read_text() == "original"


def test_result_publication_rejects_serialized_payload_over_worker_limit(
    secure_tmp_path, monkeypatch,
):
    from job_attribution import MAX_RESULT_BYTES, ResearchInvocationError, write_json_exclusive

    reports, _signals = _approved_output_tree(secure_tmp_path, monkeypatch)

    with pytest.raises(ResearchInvocationError, match="exceeds"):
        write_json_exclusive(reports, "report_large.json", {"value": "x" * MAX_RESULT_BYTES})

    assert list(reports.iterdir()) == []


def test_result_publication_cleans_hidden_temp_after_partial_write(
    secure_tmp_path, monkeypatch,
):
    import job_attribution

    reports, _signals = _approved_output_tree(secure_tmp_path, monkeypatch)
    real_write = os.write
    calls = 0

    def interrupted_write(fd, data):
        nonlocal calls
        calls += 1
        if calls == 1:
            return real_write(fd, data[: max(1, len(data) // 2)])
        assert not (reports / "report_partial.json").exists()
        assert any(path.name.startswith(".") for path in reports.iterdir())
        raise OSError("simulated short write failure")

    monkeypatch.setattr(job_attribution.os, "write", interrupted_write)

    with pytest.raises(OSError, match="simulated"):
        job_attribution.write_json_exclusive(reports, "report_partial.json", {"value": "safe"})

    assert list(reports.iterdir()) == []


def test_result_publication_fsyncs_file_and_directory(secure_tmp_path, monkeypatch):
    import job_attribution

    reports, _signals = _approved_output_tree(secure_tmp_path, monkeypatch)
    real_fsync = os.fsync
    synced_modes = []

    def recording_fsync(fd):
        synced_modes.append(os.fstat(fd).st_mode)
        return real_fsync(fd)

    monkeypatch.setattr(job_attribution.os, "fsync", recording_fsync)
    job_attribution.write_json_exclusive(reports, "report_synced.json", {"value": "safe"})

    assert any(not stat.S_ISDIR(mode) for mode in synced_modes)
    assert any(stat.S_ISDIR(mode) for mode in synced_modes)


def test_result_publication_rejects_directory_swap_without_leaking_fds(
    secure_tmp_path, monkeypatch,
):
    import job_attribution

    reports, _signals = _approved_output_tree(secure_tmp_path, monkeypatch)
    moved = reports.with_name("moved-reports")
    before = len(os.listdir("/proc/self/fd"))
    real_open = os.open
    swapped = False

    def swapping_open(path, flags, *args, **kwargs):
        nonlocal swapped
        if path == "reports" and kwargs.get("dir_fd") is not None and not swapped:
            swapped = True
            reports.rename(moved)
            reports.symlink_to(moved, target_is_directory=True)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(job_attribution.os, "open", swapping_open)

    with pytest.raises((OSError, job_attribution.ResearchInvocationError)):
        job_attribution.write_json_exclusive(reports, "report_swap.json", {"value": "safe"})

    assert not (moved / "report_swap.json").exists()
    assert len(os.listdir("/proc/self/fd")) == before


def test_invalid_worker_environment_rejects_before_mode_or_collectors(monkeypatch, main_module):
    calls = []
    monkeypatch.setattr(
        main_module,
        "parse_args",
        lambda: argparse.Namespace(
            mode="snapshot", symbol="BTC", question="", session="", research_only=True,
        ),
    )
    monkeypatch.setenv("TRADING_JOB_ID", "not-an-id")
    monkeypatch.setenv(
        "TRADING_JOB_ATTEMPT_ID", "attempt_fedcba9876543210fedcba9876543210",
    )
    monkeypatch.setattr(main_module, "mode_snapshot", lambda *_args, **_kwargs: calls.append("mode"))

    with pytest.raises(Exception, match="job ID"):
        asyncio.run(main_module.main())

    assert calls == []


def test_prevalidated_worker_context_still_requires_research_only_cli_flag(
    monkeypatch, main_module,
):
    calls = []
    monkeypatch.setattr(
        main_module,
        "parse_args",
        lambda: argparse.Namespace(
            mode="snapshot", symbol="BTC", question="", session="", research_only=False,
        ),
    )
    monkeypatch.setattr(
        main_module, "STRICT_WORKER_INVOCATION", SimpleNamespace(job_id="job_fixture"),
    )
    monkeypatch.setattr(main_module, "mode_snapshot", lambda *_a, **_k: calls.append(True))

    with pytest.raises(Exception, match="research-only"):
        asyncio.run(main_module.main())

    assert calls == []


@pytest.mark.parametrize("mode", ["snapshot", "debate", "backtest"])
def test_research_pipeline_modes_save_attributed_reports_without_changing_decision(
    secure_tmp_path, monkeypatch, main_module, mode,
):
    from job_attribution import resolve_research_invocation

    reports, signals = _approved_output_tree(secure_tmp_path, monkeypatch)
    invocation = resolve_research_invocation(True, _worker_environment(reports, signals))
    fixture = {
        "timestamp": "2026-07-12T12:00:01+00:00",
        "assets": [{"symbol": "BTC", "suggestion": "BUY", "confidence": 0.8}],
        "job_id": "client-forged",
    }

    async def pipeline(*_args, **_kwargs):
        return fixture

    semantic_inputs = SimpleNamespace(source_fingerprint="b" * 64)
    monkeypatch.setattr(
        main_module, "load_snapshot_semantic_inputs", lambda _root: semantic_inputs,
    )
    monkeypatch.setattr(main_module, "run_pipeline", pipeline)
    monkeypatch.setattr(main_module, "save_typed_decision", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main_module, "parse_report", lambda _report: [])
    if mode == "snapshot":
        portfolio = ModuleType("portfolio_manager")
        portfolio.compute_correlation_matrix = lambda _positions: None
        monkeypatch.setitem(sys.modules, "portfolio_manager", portfolio)
        for module_name in (
            "yfinance_collector", "macro", "news_collector", "sentiment_collector", "onchain_collector",
        ):
            module = ModuleType(module_name)
            module.main = lambda *_args, **_kwargs: None
            monkeypatch.setitem(sys.modules, module_name, module)

    asyncio.run(
        getattr(main_module, f"mode_{mode}")(
            ["BTC"], allow_execution=False, invocation=invocation,
        )
    )

    saved = list(reports.glob("report_*.json"))
    assert len(saved) == 1
    document = json.loads(saved[0].read_text())
    assert document["job_id"] == invocation.job_id
    assert document["attempt_id"] == invocation.attempt_id
    assert document["research_only"] is True
    assert len(document["backend_commit"]) == 40
    assert document["assets"][0]["suggestion"] == "BUY"
    assert fixture == {
        "timestamp": "2026-07-12T12:00:01+00:00",
        "assets": [{"symbol": "BTC", "suggestion": "BUY", "confidence": 0.8}],
        "job_id": "client-forged",
    }


def test_research_only_pipeline_does_not_persist_legacy_scratchpad_or_memory(
    monkeypatch, isolated_pipeline, main_module,
):
    _main, calls = isolated_pipeline
    class NoWriteScratchpad(FakeScratchpad):
        def save(self):
            raise AssertionError("legacy scratchpad write attempted")

    monkeypatch.setattr(
        main_module,
        "store_decision",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("legacy decision memory write attempted")
        ),
    )

    asyncio.run(
        main_module.run_pipeline(
            ["BTC"], pad=NoWriteScratchpad(), allow_execution=False,
        )
    )
    assert "polymarket_collector" not in calls


def test_attributed_report_does_not_persist_legacy_typed_decisions(
    secure_tmp_path, monkeypatch, main_module,
):
    from job_attribution import resolve_research_invocation

    reports, signals = _approved_output_tree(secure_tmp_path, monkeypatch)
    invocation = resolve_research_invocation(True, _worker_environment(reports, signals))
    typed_writes = []
    monkeypatch.setattr(
        main_module, "save_typed_decision", lambda *_a, **_k: typed_writes.append(True),
    )

    saved = main_module.save_report(
        {"assets": []}, allow_notifications=False, invocation=invocation,
    )

    assert saved.parent == reports
    assert typed_writes == []


def test_research_only_snapshot_skips_legacy_artifact_collectors(
    secure_tmp_path, monkeypatch, main_module,
):
    from job_attribution import resolve_research_invocation

    reports, signals = _approved_output_tree(secure_tmp_path, monkeypatch)
    invocation = resolve_research_invocation(True, _worker_environment(reports, signals))
    called = []
    for module_name in (
        "yfinance_collector", "macro", "news_collector", "sentiment_collector",
        "onchain_collector",
    ):
        module = ModuleType(module_name)
        module.main = lambda *_a, _name=module_name, **_k: called.append(_name)
        monkeypatch.setitem(sys.modules, module_name, module)

    semantic_inputs = SimpleNamespace(source_fingerprint="b" * 64)
    pipeline_calls = []
    monkeypatch.setattr(
        main_module, "load_snapshot_semantic_inputs", lambda _root: semantic_inputs,
        raising=False,
    )
    async def pipeline(*_args, **kwargs):
        pipeline_calls.append(kwargs)
        return {"assets": []}

    monkeypatch.setattr(main_module, "run_pipeline", pipeline)
    asyncio.run(
        main_module.mode_snapshot(
            ["BTC"], allow_execution=False, invocation=invocation,
        )
    )

    assert called == []
    assert pipeline_calls == [{
        "allow_execution": False,
        "semantic_inputs": semantic_inputs,
    }]


def test_strict_worker_llm_call_does_not_load_external_file_logger(
    monkeypatch, main_module,
):
    class Usage:
        prompt_tokens = 1
        completion_tokens = 1

    class Response:
        usage = Usage()
        choices = [SimpleNamespace(message=SimpleNamespace(content="ok"))]

    class Completions:
        async def create(self, **_kwargs):
            return Response()

    class AsyncOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=Completions())

    openai = ModuleType("openai")
    openai.AsyncOpenAI = AsyncOpenAI
    monkeypatch.setitem(sys.modules, "openai", openai)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "dedicated-research-fixture")
    monkeypatch.setenv("TRADING_LLM_LOGGER_MODULE", "must.not.load")
    monkeypatch.setattr(main_module, "STRICT_WORKER_INVOCATION", SimpleNamespace(job_id="job"))
    monkeypatch.setattr(
        main_module.importlib,
        "import_module",
        lambda _name: (_ for _ in ()).throw(AssertionError("external logger loaded")),
    )

    assert asyncio.run(main_module.call_llm("fixture", allow_shared_env=False)) == "ok"


def test_manual_llm_uses_standard_logging_without_explicit_logger(
    monkeypatch, main_module,
):
    response = SimpleNamespace(
        usage=None,
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
    )
    completions = SimpleNamespace(create=lambda **_kwargs: None)

    async def create(**_kwargs):
        return response

    completions.create = create
    openai = ModuleType("openai")
    openai.AsyncOpenAI = lambda **_kwargs: SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
    )
    monkeypatch.setitem(sys.modules, "openai", openai)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture")
    monkeypatch.delenv("TRADING_LLM_LOGGER_MODULE", raising=False)
    monkeypatch.setattr(main_module, "STRICT_WORKER_INVOCATION", None)
    monkeypatch.setattr(
        main_module.importlib,
        "import_module",
        lambda _name: (_ for _ in ()).throw(AssertionError("implicit logger import")),
    )

    assert asyncio.run(main_module.call_llm("fixture", allow_shared_env=False)) == "ok"


def test_manual_llm_uses_only_explicit_logger_module(monkeypatch, main_module):
    usage = SimpleNamespace(prompt_tokens=2, completion_tokens=3)
    response = SimpleNamespace(
        usage=usage,
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
    )

    async def create(**_kwargs):
        return response

    openai = ModuleType("openai")
    openai.AsyncOpenAI = lambda **_kwargs: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
    )
    monkeypatch.setitem(sys.modules, "openai", openai)
    record = SimpleNamespace(tokens_in=0, tokens_out=0, cost_usd=0, status=0)

    class LoggerContext:
        def __enter__(self):
            return record

        def __exit__(self, *_args):
            return False

    logger_module = SimpleNamespace(log_llm_call=lambda **_kwargs: LoggerContext())
    imported: list[str] = []

    def import_module(name: str):
        imported.append(name)
        return logger_module

    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture")
    monkeypatch.setenv("TRADING_LLM_LOGGER_MODULE", "configured.logger")
    monkeypatch.setattr(main_module, "STRICT_WORKER_INVOCATION", None)
    monkeypatch.setattr(main_module.importlib, "import_module", import_module)

    assert asyncio.run(main_module.call_llm("fixture", allow_shared_env=False)) == "ok"
    assert imported == ["configured.logger"]
    assert (record.tokens_in, record.tokens_out, record.status) == (2, 3, 200)


def test_research_only_macro_analyst_preserves_approved_snapshot_prompt(monkeypatch):
    from analysts import MacroAnalyst

    snapshot = {
        "fred": {"fed_funds_rate": {"value": 5.25, "trend": "flat"}},
        "cross_asset": {"vix": {"price": 18.0, "change_24h_pct": -1.0}},
        "crypto_global": {"fear_greed_index": 60},
        "timestamp": "2026-07-12T12:00:00+00:00",
    }
    prompts = []

    async def llm(prompt, *_args, **_kwargs):
        prompts.append(prompt)
        return "{}"

    analyst = MacroAnalyst()
    legacy = asyncio.run(
        analyst.analyze(llm, "BTC", {"price": 100}, macro_snapshot=snapshot)
    )
    strict = asyncio.run(
        analyst.analyze(
            llm,
            "BTC",
            {"price": 100},
            allow_persistent_cache=False,
            macro_snapshot=snapshot,
        )
    )

    assert legacy == strict
    assert prompts[0] == prompts[1]
    assert "Persistent macro cache disabled" not in prompts[1]


def _semantic_input_fixture(
    secure_tmp_path, monkeypatch, *, validity_minutes=15,
    manifest_version="fixture-20260712T120000Z", marker="risk_off",
):
    import research_semantics
    root = secure_tmp_path / "research-input"
    root.mkdir(mode=0o700, exist_ok=True)
    version_root = root / f"snapshot-{manifest_version}"
    reports = version_root / "reports"
    macro_cache = version_root / "memory" / "macro"
    reports.mkdir(parents=True, mode=0o700)
    macro_cache.mkdir(parents=True, mode=0o700)
    fixtures = {
        reports / "macro_report.json": {
            "regime": marker, "regime_confidence": 0.83,
        },
        reports / "sentiment_report.json": {
            "source": "vader",
            "assets": {"BTC": {"sentiment": "positive", "avg_score": 0.4}},
        },
        reports / "onchain_report.json": {
            "assets": {"BTC": {"onchain_risk": "medium", "onchain_source": "fixture"}},
        },
        macro_cache / "fred_cache.json": {
            "fed_funds_rate": {"value": 5.25, "trend": "flat"},
        },
        macro_cache / "yf_macro_cache.json": {
            "vix": {"price": 18.0, "change_24h_pct": -1.0},
        },
        macro_cache / "coingecko_global_cache.json": {"fear_greed_index": 60},
    }
    for path, payload in fixtures.items():
        path.write_text(json.dumps(payload), encoding="utf-8")
        path.chmod(0o400)
    for directory in (macro_cache, version_root / "memory", reports, version_root):
        directory.chmod(0o500)
    manifest_dir = secure_tmp_path / "external-manifests"
    manifest_dir.mkdir(mode=0o700, exist_ok=True)
    active_path = manifest_dir / "phase4-v1.json"
    now = datetime.now(timezone.utc)
    generated_at = (now - timedelta(minutes=1)).isoformat()
    def directory_attestation(path):
        info = path.stat()
        return {
            "device": info.st_dev,
            "inode": info.st_ino,
            "uid": info.st_uid,
            "gid": info.st_gid,
            "mode": stat.S_IMODE(info.st_mode),
        }
    plan = {
        "schema_version": "phase4-semantic-publication-plan/v1",
        "classification": "READ_ONLY_EXTERNAL_INPUT",
        "command": "SNAPSHOT",
        "destination_root": str(root),
        "active_authority_path": str(active_path),
        "input_parent_attestation": directory_attestation(root),
        "authority_parent_attestation": directory_attestation(manifest_dir),
        "manifest_version": manifest_version,
        "backend_commit": "a" * 40,
        "runtime_uid": os.geteuid(),
        "runtime_gid": os.getegid(),
        "generated_at": generated_at,
        "validity_minutes": validity_minutes + 1,
        "sources": {
            logical_name: {
                "path": str(path),
                "runtime_path": str(path.relative_to(version_root)),
                "device": path.stat().st_dev,
                "inode": path.stat().st_ino,
                "size": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for logical_name, path in zip(
                (
                    "macro_report", "sentiment_report", "onchain_report",
                    "fred_cache", "cross_asset_cache", "crypto_global_cache",
                ),
                fixtures,
            )
        },
    }
    plan_raw = (json.dumps(plan, sort_keys=True, separators=(",", ":")) + "\n").encode()
    plan_digest = hashlib.sha256(plan_raw).hexdigest()
    manifest = {
        "schema_version": 1,
        "manifest_version": manifest_version,
        "classification": "READ_ONLY_EXTERNAL_INPUT",
        "command": "SNAPSHOT",
        "backend_commit": "a" * 40,
        "approved_root": str(version_root),
        "generated_at": generated_at,
        "valid_until": (now + timedelta(minutes=validity_minutes)).isoformat(),
        "plan_digest": plan_digest,
        "plan_path": f"phase4-v1.{manifest_version}.plan.json",
        "plan_sha256": plan_digest,
        "files": {
            logical_name: {
                "path": str(path.relative_to(version_root)),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "required": True,
                "read_only": True,
            }
            for logical_name, path in zip(
                (
                    "macro_report", "sentiment_report", "onchain_report",
                    "fred_cache", "cross_asset_cache", "crypto_global_cache",
                ),
                fixtures,
            )
        },
    }
    plan_path = manifest_dir / manifest["plan_path"]
    plan_path.write_bytes(plan_raw)
    plan_path.chmod(0o444)
    version_manifest = manifest_dir / f"phase4-v1.{manifest_version}.manifest.json"
    version_manifest.write_text(json.dumps(manifest), encoding="utf-8")
    version_manifest.chmod(0o444)
    active = {
        "schema_version": 1,
        "classification": "READ_ONLY_EXTERNAL_INPUT",
        "generated_at": generated_at,
        "manifest_version": manifest_version,
        "manifest_path": version_manifest.name,
        "manifest_sha256": hashlib.sha256(version_manifest.read_bytes()).hexdigest(),
        "input_directory": version_root.name,
        "plan_digest": plan_digest,
        "plan_path": plan_path.name,
        "plan_sha256": plan_digest,
    }
    active_path.chmod(0o600) if active_path.exists() else None
    active_path.write_text(json.dumps(active), encoding="utf-8")
    active_path.chmod(0o444)
    monkeypatch.setattr(research_semantics, "APPROVED_RESEARCH_INPUT_ROOT", root)
    monkeypatch.setattr(research_semantics, "APPROVED_MANIFEST_PATH", active_path)
    monkeypatch.setattr(research_semantics, "TRUSTED_MANIFEST_OWNER_UID", os.geteuid())
    monkeypatch.setattr(
        research_semantics, "TRUSTED_INPUT_PARENT_OWNER_UID", os.geteuid(), raising=False,
    )
    monkeypatch.setattr(research_semantics, "EXPECTED_INPUT_OWNER_UID", os.geteuid())
    monkeypatch.setattr(
        research_semantics, "EXPECTED_INPUT_OWNER_GID", os.getegid(), raising=False,
    )
    monkeypatch.setenv("TRADING_RESEARCH_BACKEND_COMMIT", "a" * 40)
    return research_semantics, root, fixtures, active_path, manifest


def test_read_only_semantic_inputs_preserve_cached_report_values_without_writes(
    secure_tmp_path, monkeypatch,
):
    research_semantics, root, fixtures, _manifest_path, _manifest = (
        _semantic_input_fixture(secure_tmp_path, monkeypatch)
    )
    active = json.loads(_manifest_path.read_text())
    version_root = root / active["input_directory"]
    reports = version_root / "reports"
    macro_cache = version_root / "memory" / "macro"
    before = {path: (path.stat().st_mtime_ns, path.read_bytes()) for path in fixtures}

    def no_network(*_args, **_kwargs):
        raise AssertionError("read-only semantic inputs attempted network access")

    monkeypatch.setattr("urllib.request.urlopen", no_network)
    inputs = research_semantics.load_snapshot_semantic_inputs(root)
    assert stat.S_IMODE(_manifest_path.stat().st_mode) == 0o444
    assert os.access(_manifest_path, os.R_OK)

    assert inputs.macro_regime == ("risk_off", 0.83)
    assert inputs.sentiment_for("BTC")["sentiment_score"] == 0.4
    assert inputs.onchain_for("BTC") == {
        "onchain_risk": "medium", "onchain_source": "fixture",
    }
    assert inputs.macro_snapshot["fred"] == fixtures[macro_cache / "fred_cache.json"]
    assert {path: (path.stat().st_mtime_ns, path.read_bytes()) for path in fixtures} == before


def test_read_only_semantic_inputs_fail_closed_without_active_authority(
    secure_tmp_path, monkeypatch,
):
    research_semantics, root, _fixtures, manifest_path, _manifest = (
        _semantic_input_fixture(secure_tmp_path, monkeypatch)
    )
    manifest_path.chmod(0o600)
    manifest_path.unlink()
    with pytest.raises(research_semantics.ResearchSemanticInputError, match="active|authority"):
        research_semantics.load_snapshot_semantic_inputs(root)


def test_semantic_manifest_rejects_digest_mismatch_and_far_future_window(
    secure_tmp_path, monkeypatch,
):
    research_semantics, root, _fixtures, manifest_path, manifest = (
        _semantic_input_fixture(secure_tmp_path, monkeypatch)
    )
    active = json.loads(manifest_path.read_text())
    active["manifest_sha256"] = "0" * 64
    manifest_path.chmod(0o600)
    manifest_path.write_text(json.dumps(active))
    manifest_path.chmod(0o444)
    with pytest.raises(research_semantics.ResearchSemanticInputError, match="digest"):
        research_semantics.load_snapshot_semantic_inputs(root)

    manifest["valid_until"] = (datetime.now(timezone.utc) + timedelta(days=365)).isoformat()
    version_manifest = manifest_path.parent / active["manifest_path"]
    version_manifest.chmod(0o600)
    version_manifest.write_text(json.dumps(manifest), encoding="utf-8")
    version_manifest.chmod(0o444)
    active["manifest_sha256"] = hashlib.sha256(version_manifest.read_bytes()).hexdigest()
    manifest_path.chmod(0o600)
    manifest_path.write_text(json.dumps(active))
    manifest_path.chmod(0o444)
    with pytest.raises(research_semantics.ResearchSemanticInputError, match="window"):
        research_semantics.load_snapshot_semantic_inputs(root)


@pytest.mark.parametrize(
    "mutation",
    ["classification", "command", "backend_commit", "required", "read_only", "manifest_version"],
)
def test_semantic_manifest_rejects_wrong_classification_command_lineage_or_policy(
    secure_tmp_path, monkeypatch, mutation,
):
    research_semantics, root, _fixtures, active_path, manifest = (
        _semantic_input_fixture(secure_tmp_path, monkeypatch)
    )
    active = json.loads(active_path.read_text())
    if mutation in {"required", "read_only"}:
        manifest["files"]["macro_report"][mutation] = False
    elif mutation == "manifest_version":
        manifest[mutation] = "different-version"
    else:
        manifest[mutation] = "wrong"
    version_manifest = active_path.parent / active["manifest_path"]
    version_manifest.chmod(0o600)
    version_manifest.write_text(json.dumps(manifest))
    version_manifest.chmod(0o444)
    active["manifest_sha256"] = hashlib.sha256(version_manifest.read_bytes()).hexdigest()
    active_path.chmod(0o600)
    active_path.write_text(json.dumps(active))
    active_path.chmod(0o444)
    with pytest.raises(research_semantics.ResearchSemanticInputError):
        research_semantics.load_snapshot_semantic_inputs(root)


def test_active_rotation_loads_new_complete_snapshot_and_never_falls_back(
    secure_tmp_path, monkeypatch,
):
    research_semantics, root, _fixtures, active_path, _manifest = (
        _semantic_input_fixture(secure_tmp_path, monkeypatch, marker="old")
    )
    assert research_semantics.load_snapshot_semantic_inputs(root).macro_regime[0] == "old"
    old_active = json.loads(active_path.read_text())
    old_manifest = active_path.parent / old_active["manifest_path"]
    old_tree = root / old_active["input_directory"]

    bad = dict(old_active, manifest_version="missing", manifest_path="missing.manifest.json")
    bad_path = active_path.with_suffix(".next")
    bad_path.write_text(json.dumps(bad))
    bad_path.chmod(0o444)
    os.replace(bad_path, active_path)
    with pytest.raises(research_semantics.ResearchSemanticInputError):
        research_semantics.load_snapshot_semantic_inputs(root)
    assert old_manifest.exists() and old_tree.exists()

    _semantic_input_fixture(
        secure_tmp_path, monkeypatch, manifest_version="fixture-20260712T122900Z", marker="new",
    )
    assert research_semantics.load_snapshot_semantic_inputs(root).macro_regime[0] == "new"


def test_semantic_plan_archive_digest_is_bound_to_active_and_manifest(
    secure_tmp_path, monkeypatch,
):
    research_semantics, root, _fixtures, active_path, _manifest = (
        _semantic_input_fixture(secure_tmp_path, monkeypatch)
    )
    active = json.loads(active_path.read_text())
    plan_path = active_path.parent / active["plan_path"]
    plan_path.chmod(0o600)
    plan_path.write_text('{"tampered":true}\n')
    plan_path.chmod(0o444)
    with pytest.raises(research_semantics.ResearchSemanticInputError, match="plan|digest"):
        research_semantics.load_snapshot_semantic_inputs(root)


@pytest.mark.parametrize(
    "probe",
    [
        "unknown_root", "missing_runtime_uid", "destination", "authority_path",
        "runtime_uid", "runtime_gid", "validity", "backend_commit",
        "source_unknown", "source_missing", "source_fields", "runtime_path",
        "source_hash", "duplicate_inode", "source_size", "parent_attestation",
    ],
)
def test_backend_rejects_every_noncanonical_or_unbound_plan_shape(
    secure_tmp_path, monkeypatch, probe,
):
    research_semantics, root, fixtures, active_path, _manifest = (
        _semantic_input_fixture(secure_tmp_path, monkeypatch)
    )
    active = json.loads(active_path.read_text())
    plan_path = active_path.parent / active["plan_path"]
    plan = json.loads(plan_path.read_text())
    if probe == "unknown_root":
        plan["unexpected"] = True
    elif probe == "missing_runtime_uid":
        plan.pop("runtime_uid")
    elif probe == "destination":
        plan["destination_root"] = "/tmp/not-approved"
    elif probe == "authority_path":
        plan["active_authority_path"] = "/tmp/not-approved.json"
    elif probe == "runtime_uid":
        plan["runtime_uid"] += 1
    elif probe == "runtime_gid":
        plan["runtime_gid"] += 1
    elif probe == "validity":
        plan["validity_minutes"] = 30
    elif probe == "backend_commit":
        plan["backend_commit"] = "c" * 40
    elif probe == "source_unknown":
        plan["sources"]["unknown"] = dict(plan["sources"]["macro_report"])
    elif probe == "source_missing":
        plan["sources"].pop("fred_cache")
    elif probe == "source_fields":
        plan["sources"]["macro_report"]["unexpected"] = True
    elif probe == "runtime_path":
        plan["sources"]["macro_report"]["runtime_path"] = "reports/other.json"
    elif probe == "source_hash":
        plan["sources"]["macro_report"]["sha256"] = "c" * 64
    elif probe == "duplicate_inode":
        plan["sources"]["sentiment_report"]["device"] = plan["sources"]["macro_report"]["device"]
        plan["sources"]["sentiment_report"]["inode"] = plan["sources"]["macro_report"]["inode"]
    elif probe == "source_size":
        plan["sources"]["macro_report"]["size"] = -1
    else:
        plan["input_parent_attestation"]["unexpected"] = True

    plan_raw = (json.dumps(plan, sort_keys=True, separators=(",", ":")) + "\n").encode()
    plan_digest = hashlib.sha256(plan_raw).hexdigest()
    plan_path.chmod(0o600)
    plan_path.write_bytes(plan_raw)
    plan_path.chmod(0o444)
    manifest_path = active_path.parent / active["manifest_path"]
    manifest = json.loads(manifest_path.read_text())
    manifest["plan_digest"] = plan_digest
    manifest["plan_sha256"] = plan_digest
    manifest_path.chmod(0o600)
    manifest_path.write_text(json.dumps(manifest))
    manifest_path.chmod(0o444)
    active["plan_digest"] = plan_digest
    active["plan_sha256"] = plan_digest
    active["manifest_sha256"] = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    active_path.chmod(0o600)
    active_path.write_text(json.dumps(active))
    active_path.chmod(0o444)

    with pytest.raises(research_semantics.ResearchSemanticInputError) as raised:
        research_semantics.load_snapshot_semantic_inputs(root)
    message = str(raised.value)
    assert all(str(path) not in message for path in fixtures)


def test_semantic_inputs_reject_symlinked_parent_and_read_each_file_once(
    secure_tmp_path, monkeypatch,
):
    research_semantics, root, fixtures, _manifest_path, _manifest = (
        _semantic_input_fixture(secure_tmp_path, monkeypatch)
    )
    active = json.loads(_manifest_path.read_text())
    version_root = root / active["input_directory"]
    real_reports = version_root / "reports"
    moved_reports = version_root / "reports-real"
    version_root.chmod(0o700)
    real_reports.rename(moved_reports)
    real_reports.symlink_to(moved_reports, target_is_directory=True)
    version_root.chmod(0o500)
    with pytest.raises(
        research_semantics.ResearchSemanticInputError,
        match="symlink|directory|opened safely",
    ):
        research_semantics.load_snapshot_semantic_inputs(root)

    version_root.chmod(0o700)
    real_reports.unlink()
    moved_reports.rename(real_reports)
    version_root.chmod(0o500)
    open_counts = {}
    real_open = os.open
    macro_path = next(path for path in fixtures if path.name == "macro_report.json")
    swapped = False

    def counted_open(path, flags, *args, **kwargs):
        nonlocal swapped
        if isinstance(path, str) and path.endswith(".json"):
            open_counts[path] = open_counts.get(path, 0) + 1
        fd = real_open(path, flags, *args, **kwargs)
        if path == macro_path.name and not swapped:
            real_reports.chmod(0o700)
            replacement = macro_path.with_suffix(".replacement")
            replacement.write_text(
                json.dumps({"regime": "attacker", "regime_confidence": 1}),
                encoding="utf-8",
            )
            replacement.chmod(0o400)
            os.replace(replacement, macro_path)
            real_reports.chmod(0o500)
            swapped = True
        return fd

    monkeypatch.setattr(research_semantics.os, "open", counted_open)
    inputs = research_semantics.load_snapshot_semantic_inputs(root)
    assert inputs.macro_regime == ("risk_off", 0.83)
    assert json.loads(macro_path.read_text())["regime"] == "attacker"
    for path in fixtures:
        assert open_counts[path.name] == 1


def test_semantic_absolute_roots_are_descriptor_anchored_across_path_replacement(
    secure_tmp_path, monkeypatch,
):
    research_semantics, root, _fixtures, _manifest_path, _manifest = (
        _semantic_input_fixture(secure_tmp_path, monkeypatch)
    )
    original_root = root.with_name("research-input-opened")
    attacker_root = root.with_name("research-input-attacker")
    attacker_root.mkdir(mode=0o700)
    swapped = False
    real_open = os.open

    def swap_after_root_open(path, flags, *args, **kwargs):
        nonlocal swapped
        fd = real_open(path, flags, *args, **kwargs)
        if path == root.name and kwargs.get("dir_fd") is not None and not swapped:
            root.rename(original_root)
            root.symlink_to(attacker_root, target_is_directory=True)
            swapped = True
        return fd

    monkeypatch.setattr(research_semantics.os, "open", swap_after_root_open)
    inputs = research_semantics.load_snapshot_semantic_inputs(root)

    assert swapped is True
    assert inputs.macro_regime == ("risk_off", 0.83)


def test_semantic_external_manifest_parent_symlink_is_rejected(
    secure_tmp_path, monkeypatch,
):
    research_semantics, root, _fixtures, manifest_path, _manifest = (
        _semantic_input_fixture(secure_tmp_path, monkeypatch)
    )
    manifest_dir = manifest_path.parent
    real_dir = manifest_dir.with_name("external-manifests-real")
    manifest_dir.rename(real_dir)
    manifest_dir.symlink_to(real_dir, target_is_directory=True)

    with pytest.raises(
        research_semantics.ResearchSemanticInputError,
        match="opened safely|directory|symlink",
    ):
        research_semantics.load_snapshot_semantic_inputs(root)


def test_research_replay_writes_attributed_sidecar_and_does_not_print_paths(
    secure_tmp_path, monkeypatch, main_module, capsys,
):
    import job_attribution
    from job_attribution import resolve_research_invocation

    tmp_path = secure_tmp_path
    reports, signals = _approved_output_tree(tmp_path, monkeypatch)
    scratchpad_dir = tmp_path / "scratchpad"
    scratchpad_dir.mkdir(mode=0o700)
    scratchpad_dir.chmod(0o700)
    monkeypatch.setattr(job_attribution, "APPROVED_WORKER_SCRATCHPAD_ROOT", scratchpad_dir)
    invocation = resolve_research_invocation(
        True, _worker_replay_environment(reports, signals, scratchpad_dir),
    )
    session_id = "Session_2026-07-12"
    session_file = scratchpad_dir / f"{session_id}.jsonl"
    init_event = {
        "type": "init", "timestamp": "2026-07-12T12:00:00+00:00",
        "query": "fixture replay SECRET_QUERY", "symbols": ["BTC"], "mode": "replay",
    }
    secret_event = {
        "type": "tool_result", "timestamp": "2026-07-12T12:00:01+00:00",
        "success": True, "toolName": "secret_tool",
        "args": {"api_key": "SECRET_ARGUMENT"}, "result": "SECRET_RESULT",
        "llmSummary": "SECRET_SUMMARY", "prompt": "SECRET_PROMPT",
        "response": "SECRET_RESPONSE",
    }
    raw_lines = [json.dumps(init_event), json.dumps(secret_event)]
    _write_private_replay(session_file, "\n".join(raw_lines) + "\n")

    asyncio.run(main_module.mode_replay(session_id, research_only=True, invocation=invocation))

    output = capsys.readouterr().out
    assert "fixture replay" in output
    assert str(scratchpad_dir) not in output
    sidecars = list(signals.glob("replay_*.json"))
    assert len(sidecars) == 1
    document = json.loads(sidecars[0].read_text())
    assert document == {
        "job_id": invocation.job_id,
        "attempt_id": invocation.attempt_id,
        "backend_commit": invocation.backend_commit,
        "session_id": session_id,
        "event_count": 2,
        "events": [
            {
                "type": "init",
                "timestamp": "2026-07-12T12:00:00+00:00",
                "size_bytes": len(raw_lines[0].encode()),
            },
            {
                "type": "tool_result",
                "timestamp": "2026-07-12T12:00:01+00:00",
                "status": "success",
                "size_bytes": len(raw_lines[1].encode()),
            },
        ],
    }
    serialized = sidecars[0].read_text()
    for secret in (
        "SECRET_QUERY", "SECRET_ARGUMENT", "SECRET_RESULT", "SECRET_SUMMARY",
        "SECRET_PROMPT", "SECRET_RESPONSE", "secret_tool",
    ):
        assert secret not in serialized


def test_worker_replay_requires_exact_attested_scratchpad_root(
    secure_tmp_path, monkeypatch, main_module,
):
    import job_attribution

    reports, signals = _approved_output_tree(secure_tmp_path, monkeypatch)
    scratchpad_root = secure_tmp_path / "scratchpad"
    scratchpad_root.mkdir(mode=0o700)
    scratchpad_root.chmod(0o700)
    monkeypatch.setattr(job_attribution, "APPROVED_WORKER_SCRATCHPAD_ROOT", scratchpad_root)

    missing = _worker_environment(reports, signals)
    invocation = job_attribution.resolve_research_invocation(True, missing)
    with pytest.raises(job_attribution.ResearchInvocationError, match="scratchpad"):
        asyncio.run(main_module.mode_replay("session", True, invocation))

    wrong = _worker_replay_environment(reports, signals, scratchpad_root / "other")
    with pytest.raises(job_attribution.ResearchInvocationError, match="scratchpad"):
        job_attribution.resolve_research_invocation(True, wrong)


@pytest.mark.parametrize("path_case", [
    "ancestor_symlink", "root_symlink", "unsafe_root_mode",
    "nonprivate_root_mode", "file_symlink", "unsafe_file_mode",
])
def test_worker_replay_rejects_unsafe_scratchpad_components(
    secure_tmp_path, monkeypatch, main_module, path_case,
):
    import job_attribution

    reports, signals = _approved_output_tree(secure_tmp_path, monkeypatch)
    parent = secure_tmp_path / "legacy"
    parent.mkdir(mode=0o700)
    parent.chmod(0o700)
    real_root = parent / "scratchpad-real"
    real_root.mkdir(mode=0o700)
    real_root.chmod(0o700)
    approved_root = parent / "scratchpad"
    if path_case == "root_symlink":
        approved_root.symlink_to(real_root, target_is_directory=True)
    elif path_case == "ancestor_symlink":
        real_parent = secure_tmp_path / "legacy-real"
        shutil.rmtree(parent)
        real_parent.mkdir(mode=0o700)
        real_parent.chmod(0o700)
        real_root = real_parent / "scratchpad"
        real_root.mkdir(mode=0o700)
        real_root.chmod(0o700)
        parent.symlink_to(real_parent, target_is_directory=True)
        approved_root = parent / "scratchpad"
    else:
        real_root.rename(approved_root)
        real_root = approved_root
    session_id = "worker_session"
    session_file = real_root / f"{session_id}.jsonl"
    session_file.write_text('{"type":"session_end","timestamp":"2026-07-12T00:00:00+00:00"}\n')
    if path_case == "file_symlink":
        target = session_file.with_name("real.jsonl")
        session_file.rename(target)
        session_file.symlink_to(target)
    elif path_case == "unsafe_root_mode":
        real_root.chmod(0o777)
    elif path_case == "nonprivate_root_mode":
        real_root.chmod(0o755)
    elif path_case == "unsafe_file_mode":
        session_file.chmod(0o666)

    monkeypatch.setattr(job_attribution, "APPROVED_WORKER_SCRATCHPAD_ROOT", approved_root)
    invocation = job_attribution.resolve_research_invocation(
        True, _worker_replay_environment(reports, signals, approved_root),
    )

    with pytest.raises(job_attribution.ResearchInvocationError):
        asyncio.run(main_module.mode_replay(session_id, True, invocation))
    assert list(signals.iterdir()) == []


def test_worker_replay_reads_once_with_worker_bound_and_rejects_large_file(
    secure_tmp_path, monkeypatch, main_module,
):
    import job_attribution

    reports, signals = _approved_output_tree(secure_tmp_path, monkeypatch)
    scratchpad_root = secure_tmp_path / "scratchpad"
    scratchpad_root.mkdir(mode=0o700)
    scratchpad_root.chmod(0o700)
    monkeypatch.setattr(job_attribution, "APPROVED_WORKER_SCRATCHPAD_ROOT", scratchpad_root)
    invocation = job_attribution.resolve_research_invocation(
        True, _worker_replay_environment(reports, signals, scratchpad_root),
    )
    session_id = "large_session"
    session_file = scratchpad_root / f"{session_id}.jsonl"
    _write_private_replay(
        session_file, b"x" * (job_attribution.MAX_RESULT_BYTES + 1)
    )
    reads = []
    real_read = os.read

    def recording_read(fd, size):
        reads.append(size)
        return real_read(fd, size)

    monkeypatch.setattr(job_attribution.os, "read", recording_read)

    with pytest.raises(job_attribution.ResearchInvocationError, match="exceeds"):
        asyncio.run(main_module.mode_replay(session_id, True, invocation))

    assert reads == []
    assert list(signals.iterdir()) == []


def test_worker_replay_rejects_event_count_over_worker_limit(
    secure_tmp_path, monkeypatch, main_module,
):
    import job_attribution

    reports, signals = _approved_output_tree(secure_tmp_path, monkeypatch)
    scratchpad_root = secure_tmp_path / "scratchpad"
    scratchpad_root.mkdir(mode=0o700)
    scratchpad_root.chmod(0o700)
    monkeypatch.setattr(job_attribution, "APPROVED_WORKER_SCRATCHPAD_ROOT", scratchpad_root)
    invocation = job_attribution.resolve_research_invocation(
        True, _worker_replay_environment(reports, signals, scratchpad_root),
    )
    event = b'{"type":"thinking"}\n'
    _write_private_replay(
        scratchpad_root / "event_flood.jsonl",
        event * (job_attribution.MAX_REPLAY_EVENTS + 1),
    )

    with pytest.raises(job_attribution.ResearchInvocationError, match="event count"):
        asyncio.run(main_module.mode_replay("event_flood", True, invocation))

    assert list(signals.iterdir()) == []


def test_worker_replay_uses_open_fd_when_path_is_swapped(
    secure_tmp_path, monkeypatch, main_module, capsys,
):
    import job_attribution

    reports, signals = _approved_output_tree(secure_tmp_path, monkeypatch)
    scratchpad_root = secure_tmp_path / "scratchpad"
    scratchpad_root.mkdir(mode=0o700)
    scratchpad_root.chmod(0o700)
    monkeypatch.setattr(job_attribution, "APPROVED_WORKER_SCRATCHPAD_ROOT", scratchpad_root)
    invocation = job_attribution.resolve_research_invocation(
        True, _worker_replay_environment(reports, signals, scratchpad_root),
    )
    session_id = "swap_session"
    session_file = scratchpad_root / f"{session_id}.jsonl"
    _write_private_replay(
        session_file,
        '{"type":"init","timestamp":"2026-07-12T00:00:00+00:00","query":"ORIGINAL"}\n',
    )
    replacement = scratchpad_root / "replacement.jsonl"
    replacement.write_text('{"type":"init","timestamp":"2026-07-12T00:00:00+00:00","query":"SECRET_REPLACEMENT"}\n')
    real_read = os.read
    read_sizes = []

    def swapping_read(fd, size):
        read_sizes.append(size)
        session_file.unlink()
        replacement.rename(session_file)
        return real_read(fd, size)

    monkeypatch.setattr(job_attribution.os, "read", swapping_read)
    asyncio.run(main_module.mode_replay(session_id, True, invocation))

    output = capsys.readouterr().out
    assert read_sizes == [job_attribution.MAX_RESULT_BYTES]
    assert "ORIGINAL" in output
    assert "SECRET_REPLACEMENT" not in output


@pytest.mark.parametrize("mode", ["snapshot", "debate", "backtest"])
def test_cli_propagates_research_only_to_approved_pipeline_modes(monkeypatch, main_module, mode):
    received = []

    async def mode_recorder(_symbols, allow_execution=True, invocation=None):
        received.append((allow_execution, invocation))

    monkeypatch.setattr(
        main_module,
        "parse_args",
        lambda: argparse.Namespace(
            mode=mode,
            symbol="BTC",
            question="",
            session="",
            research_only=True,
        ),
    )
    monkeypatch.setattr(main_module, f"mode_{mode}", mode_recorder)

    asyncio.run(main_module.main())

    assert len(received) == 1
    assert received[0][0] is False
    assert received[0][1].research_only is True


def test_cli_without_flag_cannot_enter_legacy_execution_path(monkeypatch, main_module):
    received = []

    async def mode_recorder(_symbols, allow_execution=True):
        received.append(allow_execution)

    monkeypatch.setattr(
        main_module,
        "parse_args",
        lambda: argparse.Namespace(
            mode="snapshot",
            symbol="BTC",
            question="",
            session="",
            research_only=False,
        ),
    )
    monkeypatch.setattr(main_module, "mode_snapshot", mode_recorder)

    with pytest.raises(main_module.ResearchInvocationError, match="requires --research-only"):
        asyncio.run(main_module.main())

    assert received == []


def test_research_replay_uses_validated_session_id_without_unsafe_access():
    session_id = "2026-05-17-112701_004220402b8d"
    code = f'''\
import asyncio, builtins, contextlib, io, os, pathlib, sys, tempfile

forbidden_env = {FORBIDDEN_TRADING_ENV!r}
class GuardedEnviron(dict):
    def __getitem__(self, key):
        if key in forbidden_env:
            raise AssertionError("trading credential read: " + key)
        return super().__getitem__(key)
    def get(self, key, default=None):
        if key in forbidden_env:
            raise AssertionError("trading credential read: " + key)
        return super().get(key, default)
os.environ = GuardedEnviron(os.environ)

real_read_text = pathlib.Path.read_text
def guarded_read_text(path, *args, **kwargs):
    resolved = str(path.expanduser())
    if path.name == ".env":
        raise AssertionError("env file read: " + resolved)
    return real_read_text(path, *args, **kwargs)
pathlib.Path.read_text = guarded_read_text

forbidden_imports = {FORBIDDEN_RESEARCH_IMPORTS!r}
real_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if (name in forbidden_imports or name == "ccxt" or name.startswith("ccxt.")
            or name.startswith("exchange.")):
        raise AssertionError("unsafe replay import: " + name)
    return real_import(name, *args, **kwargs)
builtins.__import__ = guarded_import

import main
temporary = tempfile.TemporaryDirectory()
scratchpad_root = pathlib.Path(temporary.name) / "scratchpad"
scratchpad_root.mkdir()
expected = scratchpad_root / "{session_id}.jsonl"
expected.write_text('{{"event":"fixture"}}\\n')
import scratchpad
scratchpad.SCRATCHPAD_DIR = scratchpad_root
replayed = []
main.list_recent_sessions = lambda _limit: [expected]
main.replay_session = lambda path: replayed.append(path) or "REPLAY OUTPUT"
exists_calls = []
main.os.path.exists = lambda path: exists_calls.append(path) or False
sys.argv = ["main.py", "--mode", "replay", "--session", "{session_id}", "--research-only"]

stdout = io.StringIO()
with contextlib.redirect_stdout(stdout):
    asyncio.run(main.main())
assert replayed == [str(expected.resolve())], replayed
assert stdout.getvalue().strip() == "REPLAY OUTPUT", stdout.getvalue()
assert "{session_id}" not in exists_calls, exists_calls

stdout = io.StringIO()
with contextlib.redirect_stdout(stdout):
    asyncio.run(main.mode_replay("../.env", research_only=True))
assert "Invalid session ID" in stdout.getvalue(), stdout.getvalue()
assert replayed == [str(expected.resolve())], replayed
assert not any(name == "ccxt" or name.startswith("ccxt.") for name in sys.modules)
'''
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True)


def test_normal_replay_filepath_behavior_and_output_remain(monkeypatch, main_module, tmp_path, capsys):
    session_file = tmp_path / "legacy-session.jsonl"
    session_file.write_text("fixture")
    replayed = []
    monkeypatch.setattr(
        main_module,
        "replay_session",
        lambda path: replayed.append(path) or "LEGACY REPLAY OUTPUT",
    )

    asyncio.run(main_module.mode_replay(str(session_file)))

    assert replayed == [str(session_file)]
    assert capsys.readouterr().out.strip() == "LEGACY REPLAY OUTPUT"


def test_research_replay_exact_regular_session_uses_real_scratchpad(
    monkeypatch, main_module, tmp_path, capsys
):
    import scratchpad

    scratchpad_dir = tmp_path / "scratchpad"
    scratchpad_dir.mkdir()
    session_id = "approved_exact_session"
    session_file = scratchpad_dir / f"{session_id}.jsonl"
    session_file.write_text(json.dumps({
        "type": "init",
        "timestamp": "2026-07-12T00:00:00+00:00",
        "query": "fixture replay",
        "symbols": ["BTC"],
        "mode": "replay",
    }) + "\n")
    monkeypatch.setattr(scratchpad, "SCRATCHPAD_DIR", scratchpad_dir)

    asyncio.run(main_module.mode_replay(session_id, research_only=True))

    output = capsys.readouterr().out
    assert f"# Session Replay: {session_id}" in output
    assert "Query: fixture replay" in output


def test_research_replay_rejects_symlink_escape_without_reading(
    monkeypatch, main_module, tmp_path, capsys
):
    import scratchpad

    scratchpad_dir = tmp_path / "scratchpad"
    scratchpad_dir.mkdir()
    env_like_file = tmp_path / ".env"
    env_like_file.write_text("FAKE_SECRET=never-read")
    session_id = "escaped_session"
    (scratchpad_dir / f"{session_id}.jsonl").symlink_to(env_like_file)
    replayed = []
    monkeypatch.setattr(scratchpad, "SCRATCHPAD_DIR", scratchpad_dir)
    monkeypatch.setattr(
        main_module,
        "replay_session",
        lambda path: replayed.append(path) or "unexpected",
    )

    asyncio.run(main_module.mode_replay(session_id, research_only=True))

    assert replayed == []
    assert "Rejected session" in capsys.readouterr().out


def test_research_replay_rejects_ambiguous_prefix_without_reading(
    monkeypatch, main_module, tmp_path, capsys
):
    import scratchpad

    scratchpad_dir = tmp_path / "scratchpad"
    scratchpad_dir.mkdir()
    for suffix in ("one", "two"):
        (scratchpad_dir / f"approved_{suffix}.jsonl").write_text("not read")
    replayed = []
    monkeypatch.setattr(scratchpad, "SCRATCHPAD_DIR", scratchpad_dir)
    monkeypatch.setattr(
        main_module,
        "replay_session",
        lambda path: replayed.append(path) or "unexpected",
    )

    asyncio.run(main_module.mode_replay("approved", research_only=True))

    assert replayed == []
    assert "exact session" in capsys.readouterr().out


def test_research_only_skips_execution_imports_and_calls(monkeypatch, isolated_pipeline):
    main, calls = isolated_pipeline
    imported = []
    real_import = builtins.__import__

    def tracking_import(name, *args, **kwargs):
        if name in FORBIDDEN_RESEARCH_IMPORTS:
            imported.append(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", tracking_import)

    report = asyncio.run(
        main.run_pipeline(["BTC"], pad=FakeScratchpad(), allow_execution=False)
    )

    assert imported == []
    assert calls == [("collect_all", False)]
    assert report["schema_version"] == "legacy-fixture"
    assert report["assets"][0]["suggestion"] == "BUY"
    assert "execution" not in report["assets"][0]
    assert "broker" not in report["assets"][0]


def test_legacy_pipeline_still_runs_execution_when_not_disabled(isolated_pipeline):
    main, calls = isolated_pipeline

    report = asyncio.run(
        main.run_pipeline(["BTC"], pad=FakeScratchpad(), allow_execution=True)
    )

    assert calls == [
        "check_stops",
        "adanos_collector",
        "kalshi_collector",
        "orderflow_collector",
        "polymarket_collector",
        ("collect_all", True),
        "backtest_gate",
        "paper_execute",
        "broker_execute",
    ]
    assert report["assets"][0]["execution"]["status"] == "filled"
    assert report["assets"][0]["broker"]["status"] == "filled"


def test_research_llm_does_not_load_dotenv(monkeypatch, main_module):
    import dotenv

    loaded = []
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *_args, **_kwargs: loaded.append(True))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(
        main_module,
        "get_model_config",
        lambda: SimpleNamespace(default="fixture-model"),
    )

    asyncio.run(main_module.call_llm("fixture", allow_shared_env=False))

    assert loaded == []


def test_research_report_save_disables_notification_imports(monkeypatch, main_module, tmp_path):
    imported = []
    real_import = builtins.__import__

    def tracking_import(name, *args, **kwargs):
        if name == "alert_manager":
            imported.append(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(main_module, "store_typed_decision", lambda _decision: None)
    monkeypatch.setattr(builtins, "__import__", tracking_import)
    report = {
        "assets": [{
            "symbol": "BTC",
            "suggestion": "BUY",
            "confidence": 0.8,
            "price": 100.0,
            "market_regime": "trending_up",
            "rationale": "fixture",
        }]
    }

    main_module.save_typed_decision(report, "fixture", allow_notifications=False)

    assert imported == []


def test_vendor_router_excludes_all_exchange_fallbacks_in_research_mode(monkeypatch):
    import data_vendors

    attempted = []

    def fake_vendor(name):
        async def vendor(_session, _symbol, **_kwargs):
            attempted.append(name)
            return None
        return vendor

    for vendor_name, methods in data_vendors.VENDOR_FUNCTIONS.items():
        for method in tuple(methods):
            monkeypatch.setitem(methods, method, fake_vendor(vendor_name))

    asyncio.run(data_vendors.route_to_vendor(
        "get_price", "BTC", object(), allow_exchange=False
    ))
    asyncio.run(data_vendors.route_to_vendor(
        "get_technicals", "BTC", object(), allow_exchange=False
    ))

    assert "binance" not in attempted
    assert "ccxt_binance" not in attempted
    assert attempted


def test_ccxt_vendor_is_lazy_and_disabled_without_exchange_permission(monkeypatch):
    import data_vendors

    imported = []
    initialized = []
    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "ccxt" or name.startswith("ccxt."):
            imported.append(name)
            raise AssertionError(name)
        return real_import(name, *args, **kwargs)

    class ExchangeTrap:
        def binance(self, *_args, **_kwargs):
            initialized.append(True)
            raise AssertionError("exchange initialized")

    monkeypatch.setattr(data_vendors, "_ccxt_async", ExchangeTrap(), raising=False)
    monkeypatch.setattr(builtins, "__import__", guarded_import)
    result = asyncio.run(data_vendors._ccxt_get_price(
        object(), "BTC", allow_exchange=False
    ))

    assert result is None
    assert imported == []
    assert initialized == []


def test_portfolio_manager_research_mode_does_not_import_exchange_bridge(monkeypatch):
    from portfolio_manager import PortfolioManager
    from schemas import RiskAssessment, TradingSignal

    signal = TradingSignal(
        asset="BTC",
        action="BUY",
        confidence=0.8,
        entry_price=100.0,
        stop_loss=90.0,
        take_profit=130.0,
        reasoning="A sufficiently detailed fixture rationale for a trade.",
    )
    risks = [RiskAssessment(
        persona="neutral",
        accept_signal=True,
        position_size_pct=2.0,
        rationale="Risk reward is acceptable for this deterministic fixture.",
    )]
    response = json.dumps({
        "asset": "BTC",
        "original_signal": signal.model_dump(),
        "action": "ratify",
        "rationale": "The accepted reward to risk supports this fixture decision.",
        "conviction": 0.8,
    })

    async def fake_llm(*_args, **_kwargs):
        return response

    real_import = builtins.__import__
    imported = []
    def guarded_import(name, *args, **kwargs):
        if name == "exchange.ccxt_bridge":
            imported.append(name)
            raise AssertionError(name)
        return real_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded_import)

    decision = asyncio.run(PortfolioManager().decide(
        fake_llm, signal, [], "bull", "bear", risks,
        execution_mode="paper",
    ))

    assert decision.action == "ratify"
    assert imported == []


def test_macro_regime_research_mode_does_not_import_kalshi(monkeypatch):
    import macro

    real_import = builtins.__import__
    imported = []

    def guarded_import(name, *args, **kwargs):
        if name == "kalshi_collector":
            imported.append(name)
            raise AssertionError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    regime, confidence, _rationale = macro.detect_regime({}, allow_kalshi=False)

    assert regime == "neutral"
    assert confidence == 0.5
    assert imported == []


def test_derivatives_default_preserves_legacy_exchange_collection(monkeypatch):
    import derivatives_collector

    calls = []

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    async def funding(_session, symbol):
        calls.append(("funding", symbol))
        return None

    async def open_interest(_session, symbol):
        calls.append(("open_interest", symbol))
        return None

    monkeypatch.setattr(derivatives_collector.aiohttp, "ClientSession", FakeSession)
    monkeypatch.setattr(derivatives_collector, "fetch_funding_rate", funding)
    monkeypatch.setattr(derivatives_collector, "fetch_open_interest", open_interest)

    asyncio.run(derivatives_collector.fetch_derivatives("BTC"))

    assert calls == [("funding", "BTC"), ("open_interest", "BTC")]


def test_onchain_analyst_default_preserves_legacy_exchange_proxy(monkeypatch):
    import live_data
    from analysts import OnchainAnalyst

    calls = []
    monkeypatch.setattr(
        live_data,
        "fetch_binance_large_trades",
        lambda symbol: calls.append(symbol) or None,
    )

    report = asyncio.run(OnchainAnalyst().analyze(None, "BTC", {}))

    assert calls == ["BTC"]
    assert report.whale_activity == "neutral"


def test_adversarial_debate_default_preserves_legacy_kalshi_context(monkeypatch):
    import debate

    calls = []
    monkeypatch.setattr(
        debate,
        "format_macro_context",
        lambda allow_kalshi=True: calls.append(allow_kalshi) or "fixture macro",
    )

    instance = debate.AdversarialDebate(lambda *_args: None)
    context = instance._format_market_context({"asset": "BTC"})

    assert calls == [True]
    assert "fixture macro" in context


def test_adversarial_debate_approved_macro_override_preserves_exact_context(
    monkeypatch,
):
    import debate

    expected = "Current macro: VIX at 18.0. Regime: risk_off (confidence: 0.83)."
    legacy_calls = []
    monkeypatch.setattr(
        debate,
        "format_macro_context",
        lambda allow_kalshi=True: legacy_calls.append(allow_kalshi) or expected,
    )
    market_data = {"asset": "BTC", "price": 100, "volume_24h": 10, "change_24h": -1}
    legacy = debate.AdversarialDebate(lambda *_args: None)._format_market_context(market_data)

    strict = debate.AdversarialDebate(
        lambda *_args: None,
        allow_kalshi=False,
        macro_context_override=expected,
    )._format_market_context(market_data)

    assert legacy_calls == [True]
    assert strict == legacy


def test_portfolio_manager_default_still_uses_legacy_execution_mode(monkeypatch):
    from portfolio_manager import PortfolioManager
    from schemas import TradingSignal

    bridge = ModuleType("exchange.ccxt_bridge")
    bridge.get_mode = lambda: "dryrun"
    monkeypatch.setitem(sys.modules, "exchange.ccxt_bridge", bridge)
    signal = TradingSignal(
        asset="BTC",
        action="BUY",
        confidence=0.8,
        entry_price=100.0,
        stop_loss=90.0,
        take_profit=130.0,
        reasoning="A sufficiently detailed fixture rationale for a trade.",
    )

    prompt = PortfolioManager()._build_prompt(
        signal, [], "bull", "bear", [], None
    )

    assert "DRYRUN mode" in prompt
