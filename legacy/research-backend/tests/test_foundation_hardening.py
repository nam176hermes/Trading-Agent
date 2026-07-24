"""Regression coverage for bounded local artifacts and safe model loading."""

import json
import os
import stat

import numpy as np
import pytest


def test_marketaux_missing_key_fails_closed_before_retry(monkeypatch):
    import news_collector

    monkeypatch.delenv("MARKETAUX_API_KEY", raising=False)
    calls = []
    monkeypatch.setattr(news_collector, "_fetch_with_retry", lambda *args: calls.append(args))

    assert news_collector.fetch_news(symbols="BTC") is None
    assert calls == []


def test_marketaux_uses_encoded_runtime_key_without_exposing_it(monkeypatch):
    import news_collector

    key = "safe-test-key_123"
    monkeypatch.setenv("MARKETAUX_API_KEY", key)
    captured = {}
    monkeypatch.setattr(
        news_collector,
        "_fetch_with_retry",
        lambda _provider, _func, url: captured.setdefault("url", url) or {"data": []},
    )

    news_collector.fetch_news(symbols="BTC/USDT", search="rate & risk", limit=2)

    assert "symbols=BTC%2FUSDT" in captured["url"]
    assert "search=rate+%26+risk" in captured["url"]
    assert key in captured["url"]


def test_marketaux_http_helper_rejects_non_provider_urls_before_open(monkeypatch):
    import news_collector

    opened = []
    monkeypatch.setattr(news_collector, "urlopen", lambda *args, **kwargs: opened.append((args, kwargs)))

    for url in (
        "file:///etc/passwd",
        "http://api.marketaux.com/v1/news/all",
        "https://api.marketaux.com.evil.test/v1/news/all",
        "https://api.marketaux.com/v1/other",
    ):
        with pytest.raises(ValueError, match="Marketaux endpoint"):
            news_collector._http_fetch_news(url)

    assert opened == []


def test_twelve_data_missing_key_does_not_retry_or_open(monkeypatch):
    import twelve_data
    import fallback

    monkeypatch.delenv("TWELVE_DATA_API_KEY", raising=False)
    retry_calls = []
    monkeypatch.setattr(twelve_data, "_fetch_with_retry", lambda *args: retry_calls.append(args))
    monkeypatch.setattr(fallback, "fetch_with_fallback", lambda symbol: {"symbol": symbol})

    assert twelve_data.fetch_quote("AAPL") == {"symbol": "AAPL"}
    assert twelve_data.fetch_ohlcv("AAPL") is None
    assert retry_calls == []


def test_fallback_missing_provider_keys_does_not_open_network(monkeypatch):
    import fallback

    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    monkeypatch.delenv("POLYGON_API_KEY", raising=False)
    opened = []
    monkeypatch.setattr(
        fallback.urllib.request,
        "urlopen",
        lambda *args, **kwargs: opened.append((args, kwargs)),
    )

    assert fallback.fetch_finnhub_quote("AAPL") is None
    assert fallback.fetch_polygon_prev("AAPL") is None
    assert fallback.fetch_with_fallback("AAPL") is None
    assert opened == []


def test_fallback_reads_provider_keys_at_call_time(monkeypatch):
    import fallback

    monkeypatch.setenv("FINNHUB_API_KEY", "synthetic-finnhub-runtime-value")
    monkeypatch.setenv("POLYGON_API_KEY", "synthetic-polygon-runtime-value")
    requested_urls = []

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(self.payload).encode("utf-8")

    def fake_open(request, timeout):
        requested_urls.append((request.full_url, timeout))
        if "finnhub.io" in request.full_url:
            return Response({"c": 10, "pc": 8, "t": 1})
        return Response({"results": [{"c": 10, "o": 8, "v": 1}]})

    monkeypatch.setattr(fallback.urllib.request, "urlopen", fake_open)

    finnhub = fallback.fetch_finnhub_quote("AAPL")
    polygon = fallback.fetch_polygon_prev("AAPL")
    assert finnhub is not None
    assert polygon is not None
    assert finnhub["_data_source"] == "finnhub"
    assert polygon["_data_source"] == "polygon"
    assert len(requested_urls) == 2
    assert all(timeout == 10 for _, timeout in requested_urls)


def test_scratchpad_ids_are_unique_and_filename_is_session_id(monkeypatch, tmp_path):
    import scratchpad

    monkeypatch.setattr(scratchpad, "SCRATCHPAD_DIR", tmp_path)
    pads = [scratchpad.Scratchpad() for _ in range(100)]

    assert len({pad.session_id for pad in pads}) == 100
    assert all(pad.filepath == tmp_path / f"{pad.session_id}.jsonl" for pad in pads)


def test_scratchpad_rejects_path_traversal_session_id():
    import scratchpad

    with pytest.raises(ValueError):
        scratchpad.Scratchpad(session_id="../../outside")


def test_scratchpad_save_is_private_and_exclusive(monkeypatch, tmp_path):
    import scratchpad

    monkeypatch.setattr(scratchpad, "SCRATCHPAD_DIR", tmp_path)
    pad = scratchpad.Scratchpad(session_id="audit_01")
    pad.init_session("q", ["BTC"])
    saved = pad.save()

    assert stat.S_IMODE(saved.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        pad.save()


def test_scratchpad_load_rejects_symlink_oversized_line_and_file(monkeypatch, tmp_path):
    import scratchpad

    monkeypatch.setattr(scratchpad, "SCRATCHPAD_DIR", tmp_path)
    target = tmp_path / "target.jsonl"
    target.write_text('{"type":"init"}\n', encoding="utf-8")
    link = tmp_path / "link.jsonl"
    link.symlink_to(target)
    assert scratchpad.load_session(str(link)) == []

    line = tmp_path / "line.jsonl"
    line.write_text('{"blob":"' + "x" * (scratchpad.MAX_SESSION_LINE_BYTES + 1) + '"}\n', encoding="utf-8")
    assert scratchpad.load_session(str(line)) == []

    oversized = tmp_path / "oversized.jsonl"
    oversized.write_bytes(b"x" * (scratchpad.MAX_SESSION_FILE_BYTES + 1))
    assert scratchpad.load_session(str(oversized)) == []


def test_scratchpad_recent_listing_validates_limit_and_enumeration_cap(monkeypatch, tmp_path):
    import scratchpad

    monkeypatch.setattr(scratchpad, "SCRATCHPAD_DIR", tmp_path)
    monkeypatch.setattr(scratchpad, "MAX_SESSION_ENUM", 3)
    for name in ("one.jsonl", "two.jsonl", "three.jsonl", "four.jsonl"):
        (tmp_path / name).write_text('{"type":"init"}\n', encoding="utf-8")
    link = tmp_path / "link.jsonl"
    link.symlink_to(tmp_path / "one.jsonl")

    recent = scratchpad.list_recent_sessions(10)
    assert len(recent) <= 3
    assert scratchpad.list_recent_sessions(0) == []


def test_scratchpad_rejects_nonstandard_nan_on_save_and_load(monkeypatch, tmp_path):
    import scratchpad

    monkeypatch.setattr(scratchpad, "SCRATCHPAD_DIR", tmp_path)
    pad = scratchpad.Scratchpad(session_id="finite_audit")
    pad.log_tool_call("model", result={"score": float("nan")})
    with pytest.raises(ValueError):
        pad.save()

    malformed = tmp_path / "nan.jsonl"
    malformed.write_text('{"score":NaN}\n', encoding="utf-8")
    assert scratchpad.load_session(str(malformed)) == []


def test_scratchpad_rejects_recursive_json_and_streams_save(monkeypatch, tmp_path):
    import scratchpad

    monkeypatch.setattr(scratchpad, "SCRATCHPAD_DIR", tmp_path)
    recursive = tmp_path / "recursive.jsonl"
    recursive.write_text("[" * 1100 + "0" + "]" * 1100 + "\n", encoding="utf-8")
    assert scratchpad.load_session(str(recursive)) == []

    pad = scratchpad.Scratchpad(session_id="streamed_audit")
    pad.init_session("q", ["BTC"])
    monkeypatch.setattr(
        scratchpad.json,
        "dumps",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("save must stream JSON")),
    )
    assert pad.save() == tmp_path / "streamed_audit.jsonl"


def test_scratchpad_save_enforces_replay_line_and_record_bounds(monkeypatch, tmp_path):
    import scratchpad

    monkeypatch.setattr(scratchpad, "SCRATCHPAD_DIR", tmp_path)
    oversized_line = scratchpad.Scratchpad(session_id="oversized_line")
    oversized_line.log_thinking("x" * (scratchpad.MAX_SESSION_LINE_BYTES + 1))
    with pytest.raises(ValueError, match="line"):
        oversized_line.save()

    too_many = scratchpad.Scratchpad(session_id="too_many")
    too_many.steps = [{"type": "step"}] * scratchpad.MAX_SESSION_RECORDS
    with pytest.raises(ValueError, match="record"):
        too_many.save()


def test_bounded_directory_cap_counts_all_inspected_entries(monkeypatch, tmp_path):
    import local_artifacts

    class Entry:
        def __init__(self, name):
            self.name = name

    class Entries:
        def __enter__(self):
            return iter([Entry("a.tmp"), Entry("b.tmp"), Entry("c.tmp"), Entry("late.jsonl")])

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(local_artifacts.os, "scandir", lambda _root: Entries())
    assert local_artifacts.bounded_directory_entries(tmp_path, suffix=".jsonl", max_entries=3) == []


def test_atomic_private_write_rejects_symlinked_parent_without_touching_target(tmp_path):
    import local_artifacts

    def write_checkpoint(stream):
        stream.write(b"checkpoint")

    outside = tmp_path / "outside"
    outside.mkdir()
    linked_parent = tmp_path / "models"
    linked_parent.symlink_to(outside, target_is_directory=True)

    with pytest.raises(local_artifacts.UnsafeLocalArtifactError):
        local_artifacts.atomic_private_write(
            linked_parent / "checkpoint.bin",
            write_checkpoint,
        )

    assert not (outside / "checkpoint.bin").exists()


def test_atomic_private_write_creates_private_nested_parent_and_file(tmp_path):
    import local_artifacts

    def write_checkpoint(stream):
        stream.write(b"checkpoint")

    artifact = tmp_path / "models" / "nested" / "checkpoint.bin"
    local_artifacts.atomic_private_write(artifact, write_checkpoint)

    assert artifact.read_bytes() == b"checkpoint"
    assert stat.S_IMODE(artifact.stat().st_mode) == 0o600
    assert stat.S_IMODE(artifact.parent.stat().st_mode) == 0o700


def test_exclusive_private_write_rejects_symlinked_parent_without_touching_target(tmp_path):
    import local_artifacts

    outside = tmp_path / "outside"
    outside.mkdir()
    linked_parent = tmp_path / "scratchpad"
    linked_parent.symlink_to(outside, target_is_directory=True)

    with pytest.raises(local_artifacts.UnsafeLocalArtifactError):
        local_artifacts.exclusive_private_write(linked_parent / "audit.jsonl", b"audit\n")

    assert not (outside / "audit.jsonl").exists()


def test_local_artifact_read_rejects_symlinked_parent(tmp_path):
    import local_artifacts

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "pipeline.json").write_text("{}", encoding="utf-8")
    linked_parent = tmp_path / "models"
    linked_parent.symlink_to(outside, target_is_directory=True)

    with pytest.raises(local_artifacts.UnsafeLocalArtifactError):
        local_artifacts.read_utf8_text(linked_parent / "pipeline.json", max_bytes=64)


def test_dl_checkpoint_save_call_uses_current_two_argument_contract():
    import ast
    from pathlib import Path

    tree = ast.parse((Path(__file__).parents[1] / "dl_predictor.py").read_text(encoding="utf-8"))
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_save_checkpoint"
    ]
    assert calls
    assert all(len(call.args) == 2 and not call.keywords for call in calls)


def test_dl_training_normalizers_match_checkpoint_vector_schema():
    import dl_predictor

    feature_count = len(dl_predictor.FEATURE_COLS)

    class Dataset:
        X = np.arange(20 * 3 * feature_count, dtype=np.float32).reshape(20, 3, feature_count)
        y = np.arange(20, dtype=np.float32) % 2
        returns = np.linspace(-0.1, 0.1, 20, dtype=np.float32)

        def __len__(self):
            return len(self.X)

    *_splits, normalizers = dl_predictor._chronological_split(Dataset())  # type: ignore[arg-type]

    assert normalizers["mean"].shape == (feature_count,)
    assert normalizers["std"].shape == (feature_count,)
    assert len(normalizers["mean"].tolist()) == feature_count


def _synthetic_pipeline():
    from sklearn.cluster import KMeans
    from sklearn.decomposition import PCA
    from sklearn.preprocessing import StandardScaler

    features = np.array([[1.0, 2.0], [2.0, 3.0], [3.0, 4.0], [4.0, 5.0]])
    scaler = StandardScaler().fit(features)
    pca = PCA(n_components=1).fit(scaler.transform(features))
    kmeans = KMeans(n_clusters=2, random_state=0, n_init=1).fit(pca.transform(scaler.transform(features)))
    return {
        "scaler": scaler,
        "pca": pca,
        "kmeans": kmeans,
        "feature_names": ["mean_A", "vol_A"],
        "n_components": 1,
        "explained_variance": 0.9,
        "silhouette_score": None,
    }


def test_regime_json_artifact_round_trip_and_never_uses_pickle(monkeypatch, tmp_path):
    import ml_regime

    monkeypatch.setattr(ml_regime, "MODEL_DIR", tmp_path)
    pipeline = _synthetic_pipeline()
    regimes = [
        {"cluster_id": 0, "label": "RISK_OFF", "inherited_regime": "trending_down", "avg_return": -0.1, "avg_volatility": 0.2, "avg_correlation": 0.3},
        {"cluster_id": 1, "label": "LOW_VOL_RANGING", "inherited_regime": "choppy", "avg_return": 0.1, "avg_volatility": 0.1, "avg_correlation": 0.2},
    ]
    ml_regime._save_model(pipeline, regimes, {"n_assets": 2, "lookback_days": 20})

    loaded = ml_regime._load_model()
    assert loaded is not None
    assert (tmp_path / "pipeline.json").exists()
    assert not (tmp_path / "pipeline.pkl").exists()
    assert np.allclose(loaded["scaler_mean"], pipeline["scaler"].mean_)

    (tmp_path / "pipeline.json").unlink()
    (tmp_path / "pipeline.pkl").write_bytes(b"not a safe artifact")
    assert ml_regime._load_model() is None


def test_regime_loader_rejects_symlink_invalid_and_oversized_artifacts(monkeypatch, tmp_path):
    import ml_regime

    monkeypatch.setattr(ml_regime, "MODEL_DIR", tmp_path)
    bad = tmp_path / "bad.json"
    bad.write_text('{"version": 1}', encoding="utf-8")
    artifact = tmp_path / "pipeline.json"
    artifact.symlink_to(bad)
    assert ml_regime._load_model() is None
    artifact.unlink()
    artifact.write_bytes(b"x" * (ml_regime.MAX_MODEL_ARTIFACT_BYTES + 1))
    assert ml_regime._load_model() is None

    artifact.unlink()
    artifact.write_text("[" * 1100 + "0" + "]" * 1100, encoding="utf-8")
    assert ml_regime._load_model() is None


def test_dl_checkpoint_loader_accepts_primitive_weights_only_checkpoint(monkeypatch, tmp_path):
    torch = pytest.importorskip("torch")
    import dl_predictor

    monkeypatch.setattr(dl_predictor, "MODELS_DIR", tmp_path)
    config = dl_predictor.LSTMConfig(seq_len=4, hidden_dim=2, dropout=0.0)
    model = dl_predictor.PriceLSTM(len(dl_predictor.FEATURE_COLS), config)
    checkpoint = {
        "version": dl_predictor.CHECKPOINT_VERSION,
        "state_dict": model.state_dict(),
        "config": dl_predictor._config_to_dict(config),
        "input_dim": len(dl_predictor.FEATURE_COLS),
        "feature_cols": list(dl_predictor.FEATURE_COLS),
        "normalizers": {"mean": [0.0] * len(dl_predictor.FEATURE_COLS), "std": [1.0] * len(dl_predictor.FEATURE_COLS)},
        "trained_at": "2026-01-01T00:00:00+00:00",
        "metrics": {"accuracy": 0.5},
    }
    path = tmp_path / "btc_lstm.pt"
    torch.save(checkpoint, path)

    loaded = dl_predictor._load_checkpoint(path)
    assert loaded["config"] == config
    assert loaded["input_dim"] == len(dl_predictor.FEATURE_COLS)


def test_dl_checkpoint_loader_rejects_legacy_object_checkpoint(monkeypatch, tmp_path):
    torch = pytest.importorskip("torch")
    import dl_predictor

    monkeypatch.setattr(dl_predictor, "MODELS_DIR", tmp_path)
    path = tmp_path / "legacy.pt"
    torch.save({"config": dl_predictor.LSTMConfig()}, path)

    with pytest.raises(ValueError, match="retrain-required"):
        dl_predictor._load_checkpoint(path)
