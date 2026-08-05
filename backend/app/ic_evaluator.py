"""IC/Rank IC 信号评估系统。

IC (Information Coefficient) = 信号值与未来收益的Pearson相关性
Rank IC = 用排名计算的Spearman相关性（更鲁棒，不受极端值影响）

IC评估标准：
  |IC| > 0.05  有效信号
  |IC| > 0.1   强信号
  |IC| < 0.02  无预测力

参考: qlib calc_ic / vnpy signal_evaluation
"""
from __future__ import annotations

from typing import Any, Optional
import numpy as np
import pandas as pd


def calc_ic(
    predictions: pd.Series,
    labels: pd.Series,
    date_col: Optional[pd.Series] = None,
) -> dict[str, float]:
    """计算IC和Rank IC。

    predictions: 信号/预测值（如MA金叉信号=1,死叉=-1, 或连续的信念度）
    labels: 未来N日收益率
    date_col: 可选，按日分组的日期列。若提供则计算每日IC的均值

    返回 {
        ic: Pearson相关系数
        rank_ic: Spearman秩相关系数
        ic_std: IC标准差（按日分组时）
        icir: IC信息比率 = ic_mean / ic_std
    }
    """
    # 对齐索引
    valid = predictions.notna() & labels.notna()
    preds = predictions[valid].astype(float)
    labs = labels[valid].astype(float)

    if len(preds) < 10:
        return {"ic": 0.0, "rank_ic": 0.0, "ic_std": 0.0, "icir": 0.0, "sample_size": len(preds)}

    if date_col is not None:
        # 按日分组计算IC
        dates = date_col[valid]
        daily_ics = []
        daily_rank_ics = []
        for d in dates.unique():
            mask = dates == d
            if mask.sum() < 5:
                continue
            p = preds[mask]
            l = labs[mask]
            if p.std() > 0 and l.std() > 0:
                ic = float(p.corr(l, method="pearson"))
                ric = float(p.corr(l, method="spearman"))
                if not np.isnan(ic):
                    daily_ics.append(ic)
                if not np.isnan(ric):
                    daily_rank_ics.append(ric)

        if not daily_ics:
            return {"ic": 0.0, "rank_ic": 0.0, "ic_std": 0.0, "icir": 0.0, "sample_size": len(preds)}

        ic_mean = float(np.mean(daily_ics))
        ic_std = float(np.std(daily_ics)) if len(daily_ics) > 1 else 0.0
        rank_ic_mean = float(np.mean(daily_rank_ics)) if daily_rank_ics else 0.0
        icir = ic_mean / ic_std if ic_std > 0 else 0.0

        return {
            "ic": round(ic_mean, 4),
            "rank_ic": round(rank_ic_mean, 4),
            "ic_std": round(ic_std, 4),
            "icir": round(icir, 4),
            "sample_size": len(preds),
            "daily_count": len(daily_ics),
        }
    else:
        # 不分组，整体计算
        ic = float(preds.corr(labs, method="pearson"))
        rank_ic = float(preds.corr(labs, method="spearman"))
        if np.isnan(ic):
            ic = 0.0
        if np.isnan(rank_ic):
            rank_ic = 0.0

        return {
            "ic": round(ic, 4),
            "rank_ic": round(rank_ic, 4),
            "ic_std": 0.0,
            "icir": 0.0,
            "sample_size": len(preds),
        }


def evaluate_signal_ic(
    df: pd.DataFrame,
    signal_col: str,
    forward_days: int = 5,
    price_col: str = "close",
) -> dict[str, Any]:
    """评估某列信号对未来N日收益的预测力。

    df: 含signal_col和price_col的DataFrame
    signal_col: 信号列名（如'ma_signal', 'rsi', 'macd_hist'）
    forward_days: 预测未来几天收益（默认5天）
    price_col: 价格列（默认close）

    返回 IC评估结果 + 信号质量评级
    """
    if signal_col not in df.columns:
        return {"error": f"列 {signal_col} 不存在"}

    # 计算未来N日收益率
    df = df.copy()
    df["_forward_return"] = df[price_col].pct_change(forward_days).shift(-forward_days)

    # 对齐有效数据
    valid = df[signal_col].notna() & df["_forward_return"].notna()
    if valid.sum() < 20:
        return {"error": f"有效样本不足({valid.sum()}条)"}

    result = calc_ic(df.loc[valid, signal_col], df.loc[valid, "_forward_return"])

    # 信号质量评级
    abs_ic = abs(result["ic"])
    abs_rank_ic = abs(result["rank_ic"])
    if abs_rank_ic > 0.1 or abs_ic > 0.1:
        grade = "强信号"
    elif abs_rank_ic > 0.05 or abs_ic > 0.05:
        grade = "有效信号"
    elif abs_rank_ic > 0.02 or abs_ic > 0.02:
        grade = "弱信号"
    else:
        grade = "无预测力"

    result["signal"] = signal_col
    result["forward_days"] = forward_days
    result["grade"] = grade
    result["interpretation"] = _ic_interpretation(result["ic"], result["rank_ic"], grade)

    return result


def _ic_interpretation(ic: float, rank_ic: float, grade: str) -> str:
    """生成IC解读文本"""
    direction = "正向" if ic > 0 else "反向" if ic < 0 else "中性"
    parts = [f"信号评级: {grade}"]
    parts.append(f"IC={ic}（{direction}预测力）")
    parts.append(f"Rank IC={rank_ic}（排名相关性，更鲁棒）")
    if abs(rank_ic) > 0.1:
        parts.append("信号具有显著预测力，适合用于策略信号生成")
    elif abs(rank_ic) > 0.05:
        parts.append("信号有一定预测力，可结合其他指标使用")
    elif abs(rank_ic) > 0.02:
        parts.append("信号预测力较弱，建议作为辅助参考")
    else:
        parts.append("信号几乎没有预测力，不建议单独使用")
    return "；".join(parts)


def evaluate_strategy_signals(
    df: pd.DataFrame,
    forward_days: int = 5,
) -> dict[str, Any]:
    """批量评估多个技术指标的IC。

    对df中的常见指标列计算IC，返回对比表。
    """
    # 识别可能的信号列
    signal_cols = []
    for col in df.columns:
        if col.startswith("_"):
            continue
        if col in ("date", "open", "close", "high", "low", "volume"):
            continue
        if df[col].dtype in (np.float64, np.float32, np.int64, np.int32, float, int):
            signal_cols.append(col)

    if not signal_cols:
        return {"error": "未找到可评估的信号列"}

    results = []
    for col in signal_cols:
        r = evaluate_signal_ic(df, col, forward_days)
        if "error" not in r:
            results.append(r)

    # 按Rank IC绝对值排序
    results.sort(key=lambda x: abs(x.get("rank_ic", 0)), reverse=True)

    return {
        "forward_days": forward_days,
        "signals_evaluated": len(results),
        "results": results,
        "best_signal": results[0]["signal"] if results else None,
        "best_rank_ic": results[0]["rank_ic"] if results else 0,
    }
