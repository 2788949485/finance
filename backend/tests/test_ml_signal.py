"""ML 信号诊断管线冒烟测试。

用合成数据（带可学习信号的几何布朗运动 + 动量效应）验证：
  1. 各模块独立可用
  2. 全流程串联无异常
  3. sklearn 与 numpy 兜底两条路径都能跑通
  4. 输出格式正确

不依赖网络，纯本地合成数据。
"""
from __future__ import annotations

import sys
import numpy as np
import pandas as pd


def make_synthetic_ohlcv(n: int = 500, seed: int = 42) -> pd.DataFrame:
    """生成带动量效应的合成 K 线（让 ML 有可学信号）。"""
    rng = np.random.default_rng(seed)
    # 几何布朗运动 + 自回归动量（涨的更可能继续涨）
    log_ret = np.zeros(n)
    momentum = 0.0
    for i in range(1, n):
        # 动量项：过去5日收益的 0.1 倍 + 噪声
        shock = rng.normal(0, 0.02)
        log_ret[i] = 0.0003 + 0.1 * momentum + shock
        if i >= 5:
            momentum = log_ret[i - 5:i].sum()
    close = 100 * np.exp(np.cumsum(log_ret))
    # 构造 OHLCV
    high = close * (1 + np.abs(rng.normal(0, 0.008, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.008, n)))
    open_ = close * (1 + rng.normal(0, 0.005, n))
    volume = rng.lognormal(15, 0.5, n) * (1 + np.abs(log_ret) * 50)
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.DataFrame({
        "date": dates, "open": open_, "close": close,
        "high": high, "low": low, "volume": volume,
    })


def test_features():
    print("\n[1] 特征工程...")
    df = make_synthetic_ohlcv(500)
    from app.ml_signal.features import add_features, DEFAULT_FEATURE_COLUMNS
    feats = add_features(df)
    assert len(feats) > 400, f"特征行数过少: {len(feats)}"
    for col in DEFAULT_FEATURE_COLUMNS:
        assert col in feats.columns, f"缺少特征列: {col}"
    nan_ratio = feats[DEFAULT_FEATURE_COLUMNS].isna().mean().mean()
    assert nan_ratio < 0.05, f"NaN 比例过高: {nan_ratio:.2%}"
    print(f"    OK  行数={len(feats)}  特征数={len(DEFAULT_FEATURE_COLUMNS)}  "
          f"NaN率={nan_ratio:.2%}")


def test_labels():
    print("\n[2] 标签生成（三重壁垒）...")
    df = make_synthetic_ohlcv(500)
    from app.ml_signal.labels import triple_barrier_labels, binary_labels
    lab = triple_barrier_labels(df, take_profit_pct=0.05, stop_loss_pct=0.05,
                                 max_holding_days=10)
    assert len(lab) == len(df)
    valid = lab.dropna()
    assert len(valid) > 400
    counts = valid.value_counts().to_dict()
    assert all(k in [-1, 0, 1] for k in counts.keys()), f"非法标签: {counts}"
    print(f"    OK  有效标签={len(valid)}  分布={counts}")

    # 二分类
    blab = binary_labels(df, forward_days=5)
    assert len(blab) == len(df)
    print(f"    OK  二分类标签生成正常")


def test_split():
    print("\n[3] 时间序列切分...")
    from app.ml_signal.split import time_series_split, walk_forward_split, purge_overlap
    sp = time_series_split(500, test_size=0.2, val_size=0.15, purge_window=10)
    assert len(sp.train_idx) > 300
    assert len(sp.test_idx) > 50
    # 验证无重叠（位置索引）
    all_idx = np.concatenate([sp.train_idx, sp.val_idx, sp.test_idx])
    assert len(all_idx) == len(set(all_idx.tolist())), "索引有重叠!"
    print(f"    OK  单次切分: {sp.sizes()}")

    folds = list(walk_forward_split(500, train_size=200, test_size=50, step=50))
    assert len(folds) >= 2, f"walk-forward 折数过少: {len(folds)}"
    print(f"    OK  walk-forward 折数={len(folds)}")

    purged = purge_overlap(np.arange(100), label_horizon=10,
                           forbidden=np.arange(80, 100))
    assert len(purged) < 100
    print(f"    OK  purge 后剩余={len(purged)}/100")


def test_train():
    print("\n[4] 模型训练...")
    df = make_synthetic_ohlcv(500)
    from app.ml_signal.features import add_features, DEFAULT_FEATURE_COLUMNS
    from app.ml_signal.labels import triple_barrier_labels
    from app.ml_signal.train import ModelTrainer

    feats = add_features(df)
    lab = triple_barrier_labels(feats, max_holding_days=10)
    feats = feats.assign(label=lab).dropna(subset=["label"])
    X = feats[DEFAULT_FEATURE_COLUMNS].to_numpy()
    y = feats["label"].to_numpy()

    for model_name in ["auto", "rf", "logit", "numpy"]:
        trainer = ModelTrainer(model=model_name)
        trainer.fit(X[:300], y[:300], feature_names=DEFAULT_FEATURE_COLUMNS)
        X_test, y_test = X[300:], y[300:]
        preds = trainer.predict(X_test)
        proba = trainer.predict_proba(X_test)
        assert preds.shape[0] == X_test.shape[0]
        assert proba.shape == (X_test.shape[0], 3)
        assert np.allclose(proba.sum(axis=1), 1.0, atol=1e-4)
        assert set(np.unique(preds).tolist()) <= {-1, 0, 1}
        imp = trainer.feature_importance()
        print(f"    OK  model={model_name:6s} backend={trainer.backend:14s} "
              f"preds_shape={preds.shape}  特征重要性={len(imp)}")


def test_evaluate():
    print("\n[5] 评估...")
    from app.ml_signal.evaluate import evaluate_predictions, evaluate_strategy
    y_true = np.array([1, 1, 1, -1, -1, 0, 0, 1, -1, 0])
    y_pred = np.array([1, 1, -1, -1, 0, 0, 1, 1, -1, 0])
    y_proba = np.random.dirichlet([1, 1, 1], size=10)
    res = evaluate_predictions(y_true, y_pred, y_proba)
    assert "accuracy" in res
    assert "buy_precision" in res
    assert "confusion" in res
    print(f"    OK  分类评估: acc={res['accuracy']}  "
          f"buy_p={res['buy_precision']}  confusion={res['confusion']}")

    df = make_synthetic_ohlcv(200)
    preds = np.random.choice([-1, 0, 1], size=200)
    strat = evaluate_strategy(df, preds)
    assert "total_return_pct" in strat
    assert "sharpe" in strat
    print(f"    OK  策略评估: ret={strat['total_return_pct']}%  "
          f"sharpe={strat['sharpe']}")


def test_full_pipeline():
    print("\n[6] 全流程串联...")
    df = make_synthetic_ohlcv(500)
    from app.ml_signal import run_ml_pipeline, PipelineConfig
    cfg = PipelineConfig(model="rf")
    result = run_ml_pipeline(df, cfg)
    assert result is not None, "管线返回 None"
    assert result.n_samples > 400
    assert result.classification["accuracy"] > 0.2  # 三分类基线 ~0.33
    assert "total_return_pct" in result.strategy
    print("    " + "\n    ".join(result.summary().split("\n")[:20]))


def main():
    print("=" * 60)
    print("ML 信号诊断管线 冒烟测试")
    print("=" * 60)
    try:
        test_features()
        test_labels()
        test_split()
        test_train()
        test_evaluate()
        test_full_pipeline()
        print("\n" + "=" * 60)
        print("✅ 全部测试通过")
        print("=" * 60)
        return 0
    except AssertionError as e:
        print(f"\n❌ 断言失败: {e}")
        import traceback
        traceback.print_exc()
        return 1
    except Exception as e:
        print(f"\n❌ 异常: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
