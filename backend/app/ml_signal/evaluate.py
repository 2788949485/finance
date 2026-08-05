"""模型评估：分类指标 + 交易相关指标。

两层评估：
  1. 分类层：对 ±1 信号的 precision / recall / F1（聚焦 +1 类）
  2. 交易层：把模型预测转成简单策略（+1 做多，-1 平仓），算收益/夏普/胜率

后者用于"诊断"——验证 ML 信号在实际交易里的经济价值，
而不是仅看离线 AUC。
"""
from __future__ import annotations

from typing import Any, Optional

import numpy as np
import pandas as pd


def evaluate_predictions(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    y_proba: Optional[np.ndarray] = None,
) -> dict[str, Any]:
    """分类评估（聚焦 +1 类，即「买入信号」）。

    参数：
      y_true : 真实标签 ∈ {-1, 0, 1}
      y_pred : 预测标签
      y_proba: (n, 3) 概率，列对应 [-1, 0, 1]；可选，用于 AUC

    返回指标字典。
    """
    y_true = np.asarray(y_true)
    y_pred = np.asarray(y_pred)
    valid = ~(np.isnan(y_true) | np.isnan(y_pred))
    y_true, y_pred = y_true[valid], y_pred[valid]

    if len(y_true) == 0:
        return {"error": "无有效样本"}

    result: dict[str, Any] = {
        "n_samples": int(len(y_true)),
        "accuracy": _round(_accuracy(y_true, y_pred)),
    }

    # 各类 precision/recall/f1
    for c in (-1, 0, 1):
        p, r, f1 = _prf(y_true, y_pred, c)
        result[f"precision_{c:+d}"] = _round(p)
        result[f"recall_{c:+d}"] = _round(r)
        result[f"f1_{c:+d}"] = _round(f1)

    # +1 类的 precision 是「买入信号准确率」——最关键
    result["buy_precision"] = result["precision_+1"]
    result["buy_recall"] = result["recall_+1"]

    # 混淆矩阵
    result["confusion"] = _confusion_matrix(y_true, y_pred).tolist()

    # 概率相关指标（AUC 近似）
    if y_proba is not None and y_proba.shape == (len(y_true), 3):
        result["brier_score"] = _round(_brier(y_true, y_proba))
        # +1 类的 AUC（二值化为 +1 vs rest）
        bin_true = (y_true == 1).astype(int)
        if bin_true.sum() > 0 and bin_true.sum() < len(bin_true):
            result["auc_plus1"] = _round(_auc_approx(bin_true, y_proba[:, 2]))

    return result


def evaluate_strategy(
    df: pd.DataFrame,
    predictions: np.ndarray,
    transaction_cost_pct: float = 0.001,
    benchmark: str = "hold",
) -> dict[str, Any]:
    """把预测转成策略，评估交易表现。

    策略规则：
      pred == +1 → 满仓持有
      pred == -1 → 清仓空仓
      pred ==  0 → 维持上一仓位
    每次 pred 翻转计一次交易成本。

    参数：
      df                   : 带特征与 close 的数据框（用于回测价格序列）
      predictions          : 模型预测，长度 = len(df)
      transaction_cost_pct : 单边交易成本（千一）
      benchmark            : 对照基准，"hold" = 买入持有

    返回策略表现字典。
    """
    if df is None or len(df) == 0:
        return {"error": "无数据"}

    preds = np.asarray(predictions)
    close = df["close"].astype(float).to_numpy()
    n = min(len(preds), len(close))
    preds = preds[:n]
    close = close[:n]

    position = 0.0      # 0 空仓 / 1 满仓
    cash = 1.0          # 归一化资金
    shares = 0.0
    equity = []
    trades = 0
    wins = 0
    total_closes = 0
    last_buy_price = 0.0
    peak = 1.0
    max_dd = 0.0
    daily_rets = []

    for i in range(n):
        # 应用预测（次日用收盘价执行）
        if i > 0:
            signal = preds[i - 1]  # t-1 信号 → t 执行（无未来函数）
        else:
            signal = 0
        price = close[i]

        new_pos = position
        if signal == 1 and position == 0:
            new_pos = 1.0
        elif signal == -1 and position > 0:
            new_pos = 0.0

        # 换手
        if new_pos != position:
            old_value = cash + shares * price
            trades += 1
            if new_pos > 0:
                last_buy_price = price
                shares = old_value * (1 - transaction_cost_pct) / price
                cash = 0.0
            else:
                total_closes += 1
                if price > last_buy_price:
                    wins += 1
                cash = old_value * (1 - transaction_cost_pct)
                shares = 0.0
            position = new_pos

        value = cash + shares * price
        equity.append(value)
        if value > peak:
            peak = value
        dd = (peak - value) / peak
        if dd > max_dd:
            max_dd = dd
        if i > 0:
            daily_rets.append(value / equity[i - 1] - 1)

    final_value = equity[-1] if equity else 1.0
    total_return = (final_value - 1) * 100

    # 基准
    bm_return = (close[-1] / close[0] - 1) * 100 if n > 1 else 0.0

    # 夏普（年化）
    if daily_rets and len(daily_rets) > 5:
        r = np.array(daily_rets)
        sharpe = (r.mean() / (r.std() + 1e-9)) * np.sqrt(252)
    else:
        sharpe = 0.0

    return {
        "total_return_pct": round(total_return, 2),
        "benchmark_return_pct": round(bm_return, 2),
        "excess_return_pct": round(total_return - bm_return, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "sharpe": round(float(sharpe), 3),
        "n_trades": trades,
        "win_rate_pct": round(wins / total_closes * 100, 1) if total_closes > 0 else 0.0,
        "final_equity": round(final_value, 4),
    }


# ============================================================
# 内部计算函数（避免 sklearn 依赖）
# ============================================================

def _accuracy(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    return float((y_true == y_pred).mean())


def _prf(y_true: np.ndarray, y_pred: np.ndarray, c: int):
    """单类 precision / recall / f1。"""
    tp = int(((y_pred == c) & (y_true == c)).sum())
    fp = int(((y_pred == c) & (y_true != c)).sum())
    fn = int(((y_pred != c) & (y_true == c)).sum())
    p = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    r = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0.0
    return p, r, f1


def _confusion_matrix(y_true: np.ndarray, y_pred: np.ndarray) -> np.ndarray:
    labels = [-1, 0, 1]
    cm = np.zeros((3, 3), dtype=int)
    for i, ti in enumerate(labels):
        for j, pj in enumerate(labels):
            cm[i, j] = int(((y_true == ti) & (y_pred == pj)).sum())
    return cm


def _brier(y_true: np.ndarray, y_proba: np.ndarray) -> float:
    """多分类 Brier 分数（越小越好）。"""
    n = len(y_true)
    onehot = np.zeros_like(y_proba)
    for i, t in enumerate(y_true):
        if -1 <= t <= 1:
            onehot[i, int(t) + 1] = 1.0
    return float(((y_proba - onehot) ** 2).sum(axis=1).mean())


def _auc_approx(y_bin: np.ndarray, scores: np.ndarray) -> float:
    """ROC AUC 的 Mann-Whitney U 近似（无 sklearn 依赖）。"""
    pos = scores[y_bin == 1]
    neg = scores[y_bin == 0]
    if len(pos) == 0 or len(neg) == 0:
        return 0.5
    # 用排序近似
    all_s = np.concatenate([pos, neg])
    ranks = _rankdata(all_s)
    sum_pos = ranks[:len(pos)].sum()
    auc = (sum_pos - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))
    return float(auc)


def _rankdata(a: np.ndarray) -> np.ndarray:
    """简单 rank（average ties）。"""
    order = np.argsort(a)
    ranks = np.empty_like(order, dtype=float)
    ranks[order] = np.arange(1, len(a) + 1)
    # 处理并列
    unique, counts = np.unique(a, return_counts=True)
    for v, c in zip(unique, counts):
        if c > 1:
            mask = (a == v)
            ranks[mask] = ranks[mask].mean()
    return ranks


def _round(x: float, n: int = 4) -> float:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return 0.0
    return round(float(x), n)
