"""训练管线编排：数据 → 特征 → 标签 → 切分 → 训练 → 评估 → 诊断报告。

入口函数 run_ml_pipeline 串联全部步骤，返回结构化诊断报告。
报告用于回答：「我的 ML 信号到底有没有 alpha？」
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np
import pandas as pd

from .features import add_features, DEFAULT_FEATURE_COLUMNS
from .labels import triple_barrier_labels
from .split import time_series_split, Split
from .train import ModelTrainer
from .evaluate import evaluate_predictions, evaluate_strategy


@dataclass
class PipelineConfig:
    """管线超参数。"""
    # 特征
    feature_cols: list[str] = field(default_factory=lambda: list(DEFAULT_FEATURE_COLUMNS))
    # 标签（三重壁垒）
    take_profit_pct: float = 0.05
    stop_loss_pct: float = 0.05
    max_holding_days: int = 10
    # 切分
    test_size: float = 0.2
    val_size: float = 0.15
    purge_window: int = 10
    # 模型
    model: str = "auto"          # auto / rf / gb / logit / numpy
    random_state: int = 42
    # 策略评估
    transaction_cost_pct: float = 0.001


@dataclass
class PipelineResult:
    """管线运行结果。"""
    config: PipelineConfig
    split_sizes: dict[str, int]
    classification: dict[str, Any]
    strategy: dict[str, Any]
    feature_importance: dict[str, float]
    backend: str
    n_samples: int
    trainer: Optional[ModelTrainer] = None

    def summary(self) -> str:
        """人类可读的诊断摘要。"""
        lines = [
            f"ML 信号诊断报告",
            f"=" * 50,
            f"样本数: {self.n_samples}  切分: {self.split_sizes}",
            f"后端: {self.backend}",
            f"",
            f"分类指标:",
            f"  准确率:     {self.classification.get('accuracy', 'N/A')}",
            f"  买入精度:   {self.classification.get('buy_precision', 'N/A')}  "
            f"(最关键: 预测买的有多少真涨)",
            f"  买入召回:   {self.classification.get('buy_recall', 'N/A')}",
            f"",
            f"策略表现:",
            f"  策略收益:   {self.strategy.get('total_return_pct', 'N/A')}%",
            f"  基准收益:   {self.strategy.get('benchmark_return_pct', 'N/A')}%",
            f"  超额收益:   {self.strategy.get('excess_return_pct', 'N/A')}%",
            f"  夏普比率:   {self.strategy.get('sharpe', 'N/A')}",
            f"  最大回撤:   {self.strategy.get('max_drawdown_pct', 'N/A')}%",
            f"  交易次数:   {self.strategy.get('n_trades', 'N/A')}",
            f"  胜率:       {self.strategy.get('win_rate_pct', 'N/A')}%",
        ]
        if self.feature_importance:
            top5 = sorted(self.feature_importance.items(),
                          key=lambda x: -x[1])[:5]
            lines.append("")
            lines.append("Top-5 重要特征:")
            for name, imp in top5:
                lines.append(f"  {name}: {imp}")
        return "\n".join(lines)


def run_ml_pipeline(
    df: pd.DataFrame,
    config: Optional[PipelineConfig] = None,
) -> Optional[PipelineResult]:
    """运行完整 ML 信号诊断管线。

    参数：
      df     : OHLCV 数据框（来自 fetcher.get_history）
      config : 管线超参，None 用默认

    返回 PipelineResult，数据不足时返回 None。
    """
    if df is None or len(df) < 100:
        return None

    cfg = config or PipelineConfig()

    # 1) 特征工程
    feats = add_features(df, dropna=True)
    if len(feats) < 60:
        return None

    # 2) 标签生成（在完整 feature df 上做，保证索引对齐）
    labels = triple_barrier_labels(
        feats,
        take_profit_pct=cfg.take_profit_pct,
        stop_loss_pct=cfg.stop_loss_pct,
        max_holding_days=cfg.max_holding_days,
    )
    feats = feats.assign(_label=labels)
    feats = feats.dropna(subset=["_label"]).reset_index(drop=True)

    if len(feats) < 60:
        return None

    # 3) 切分
    n = len(feats)
    split = time_series_split(
        n, test_size=cfg.test_size, val_size=cfg.val_size,
        purge_window=cfg.purge_window,
    )

    # 4) 准备矩阵
    X = feats[cfg.feature_cols].to_numpy(dtype=float)
    y = feats["_label"].to_numpy(dtype=float)
    dates = feats["date"].to_numpy() if "date" in feats.columns else None
    close = feats["close"].to_numpy(dtype=float) if "close" in feats.columns else None

    if len(split.test_idx) == 0:
        return None

    X_train = X[split.train_idx]
    y_train = y[split.train_idx]
    X_test = X[split.test_idx]
    y_test = y[split.test_idx]

    # 5) 训练
    trainer = ModelTrainer(model=cfg.model, random_state=cfg.random_state)
    trainer.fit(X_train, y_train, feature_names=cfg.feature_cols)

    # 6) 预测 + 评估
    y_pred = trainer.predict(X_test)
    y_proba = trainer.predict_proba(X_test)
    classification = evaluate_predictions(y_test, y_pred, y_proba)

    # 7) 策略评估（在测试集上）
    test_df = feats.iloc[split.test_idx].copy()
    test_df = test_df.assign(pred=y_pred)
    strategy = evaluate_strategy(
        test_df, y_pred, transaction_cost_pct=cfg.transaction_cost_pct,
    )

    return PipelineResult(
        config=cfg,
        split_sizes=split.sizes(),
        classification=classification,
        strategy=strategy,
        feature_importance=trainer.feature_importance(),
        backend=trainer.backend,
        n_samples=n,
        trainer=trainer,
    )


# ============================================================
# 多股票批量诊断
# ============================================================

def diagnose_symbols(
    symbols: list[str],
    fetch_fn=None,
    days: int = 400,
    config: Optional[PipelineConfig] = None,
) -> dict[str, PipelineResult]:
    """对多只股票批量运行诊断。

    参数：
      symbols  : 股票代码列表
      fetch_fn : 取数函数 (symbol, days) -> DataFrame；默认用 fetcher.get_history
      days     : 拉取的历史天数
      config   : 管线超参

    返回 {symbol: PipelineResult}，失败的股票跳过。
    """
    if fetch_fn is None:
        from ..data import fetcher as datalayer
        fetch_fn = lambda s, d: datalayer.get_history(datalayer._norm_symbol(s), days=d)

    results: dict[str, PipelineResult] = {}
    for sym in symbols:
        try:
            df = fetch_fn(sym, days)
            if df is None or len(df) < 100:
                continue
            res = run_ml_pipeline(df, config)
            if res is not None:
                results[sym] = res
        except Exception as e:
            # 容错：单只失败不影响其他
            continue
    return results
