"""ML 信号诊断系统：从历史 K 线学习买卖信号，诊断策略有效性。

模块结构：
  features  -- 特征工程（动量/波动/量能/技术指标）
  labels    -- 三重壁垒标签生成（双向边界 + 止损）
  split     -- 时间序列切分（walk-forward / purged k-fold）
  train     -- 模型训练（sklearn 优先，无依赖时退化为 numpy 逻辑回归）
  evaluate  -- 分类指标 + 交易相关评估（精度/召回/夏普/胜率）
  pipeline  -- 串联全流程：数据 → 特征 → 标签 → 切分 → 训练 → 评估

设计原则：
  1. 仅依赖 pandas + numpy（venv 必备），sklearn 为可选增强
  2. 所有函数对缺失数据 / 短样本容错降级，绝不抛异常中断
  3. 与 backtest.py 共享 OHLCV 数据格式，可直接接入
"""
from __future__ import annotations

from .features import add_features, DEFAULT_FEATURE_COLUMNS
from .labels import triple_barrier_labels
from .split import walk_forward_split, time_series_split, purge_overlap
from .train import train_model, ModelTrainer
from .evaluate import evaluate_predictions, evaluate_strategy
from .pipeline import run_ml_pipeline, PipelineConfig, PipelineResult, diagnose_symbols

__all__ = [
    "add_features",
    "DEFAULT_FEATURE_COLUMNS",
    "triple_barrier_labels",
    "walk_forward_split",
    "time_series_split",
    "purge_overlap",
    "train_model",
    "ModelTrainer",
    "evaluate_predictions",
    "evaluate_strategy",
    "run_ml_pipeline",
    "PipelineConfig",
    "PipelineResult",
    "diagnose_symbols",
]
