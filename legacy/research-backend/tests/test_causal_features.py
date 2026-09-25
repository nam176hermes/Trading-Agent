"""Regime features must be causal and models must carry the feature contract they were trained on (Codex A01)."""
import json

import numpy as np
import pandas as pd
import pytest


def _candles(seed=123, n=400):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    index = pd.date_range('2024-01-01', periods=n, freq='h', tz='UTC')
    return pd.DataFrame(dict(open=close, high=close * 1.002, low=close * 0.998, close=close,
                             volume=rng.uniform(10, 20, n)), index=index)


def test_changing_only_future_prices_never_changes_past_regime_features():
    from ml_predictor import generate_features
    before = _candles()
    after = before.copy()
    rng = np.random.default_rng(7)
    after.iloc[300:, :4] = after.iloc[300:, :4].to_numpy() * rng.uniform(0.7, 1.3, (100, 1))
    a, b = generate_features(before), generate_features(after)
    regime = [c for c in a.columns if c.startswith('regime_prob_')] + ['regime_label']
    assert len(regime) == 4
    pd.testing.assert_frame_equal(a[regime].iloc[:300], b[regime].iloc[:300])
    assert a[regime].iloc[60:300].notna().all().all()


def test_regime_components_are_ordered_by_mean_return():
    from ml_toolkit import causal_gmm_regime_probs
    rng = np.random.default_rng(1)
    down, up = rng.normal(-0.02, 0.001, 200), rng.normal(0.02, 0.001, 200)
    # Column 0 is the lowest-mean regime whatever order the data arrived in.
    rising = causal_gmm_regime_probs(np.concatenate([down, up]), n_components=2, refit_every=50)
    falling = causal_gmm_regime_probs(np.concatenate([up, down]), n_components=2, refit_every=50)
    assert np.isnan(rising[:49]).all() and not np.isnan(rising[49:]).any()
    assert rising[-1, 1] > 0.99 and falling[-1, 0] > 0.99


@pytest.mark.parametrize('contract', [None, 'leaky-gmm-v1'])
def test_models_trained_on_another_feature_contract_are_refused(tmp_path, monkeypatch, contract):
    import backtest_engine
    import ml_predictor
    (tmp_path / 'btc_lightgbm_latest.txt').write_text('not loaded')
    meta = {'features': [], 'platt_a': 1.0, 'platt_b': 0.0}
    if contract:
        meta['feature_contract'] = contract
    (tmp_path / 'btc_lightgbm_latest.json').write_text(json.dumps(meta))
    monkeypatch.setattr(ml_predictor, 'MODELS_DIR', tmp_path)
    monkeypatch.setattr(backtest_engine, 'MODELS_DIR', tmp_path)
    assert ml_predictor.load_model('BTC') is None
    strategy = backtest_engine.MLStrategy(backtest_engine.BacktestConfig(), symbol='BTC')
    strategy.init()
    assert strategy.model is None


def test_a_retrained_model_saves_loads_and_predicts_its_own_probability(tmp_path, monkeypatch):
    # Codex K01: a model with the current contract must round-trip save -> load -> predict.
    import lightgbm as lgb
    import ml_predictor
    frame = _candles(seed=5, n=600)
    feats = ml_predictor.generate_features(frame).dropna()
    cols = [c for c in feats.columns if feats[c].dtype in ('float64', 'float32', 'int64', 'int32')
            and c not in {'target_1h_return', 'target_up', 'target'}]
    model = lgb.LGBMClassifier(n_estimators=10, verbose=-1).fit(feats[cols].to_numpy(), feats['target_up'].to_numpy())
    model.booster_.save_model(str(tmp_path / 'btc_lightgbm_latest.txt'))
    (tmp_path / 'btc_lightgbm_latest.json').write_text(json.dumps(
        {'features': cols, 'feature_contract': ml_predictor.FEATURE_CONTRACT}))
    monkeypatch.setattr(ml_predictor, 'MODELS_DIR', tmp_path)
    loaded = ml_predictor.load_model('BTC')
    assert loaded is not None
    result = ml_predictor.predict_next(loaded, frame)
    expected = float(model.predict_proba(ml_predictor.generate_features(frame)[cols].iloc[-1:].to_numpy())[0, 1])
    assert result['probability'] == pytest.approx(round(max(expected, 1 - expected), 4) if not 0.45 < expected < 0.55 else 0.5)


def test_assembly_loads_models_through_the_contract_check():
    import ast
    from pathlib import Path
    tree = ast.parse((Path(__file__).resolve().parents[1] / 'assembly.py').read_text())
    constructors = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == 'Booster']
    assert constructors == []
