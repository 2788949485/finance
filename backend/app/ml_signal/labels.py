"""标签生成：三重壁垒（Triple-Barrier）方法。

思想（López de Prado, AFML）：
  在 t 时做多，设置三重壁垒：
    上轨：+take_profit_pct   （触及 → 标签 +1，看对）
    下轨：-stop_loss_pct      （触及 → 标签 -1，看错）
    时间：max_holding_days    （超时未触及 → 按到期收益方向判定）

  标签 y ∈ {+1, 0, -1}，分别代表「涨/平/跌」预期。
  评估时通常只取 ±1 做二分类，或保留三分类。

支持「方向元标签」：若已有一组原始信号（如均线交叉），
可对信号方向建模，把入场时机做成 ±1 元分类。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def triple_barrier_labels(
    df: pd.DataFrame,
    take_profit_pct: float = 0.05,
    stop_loss_pct: float = 0.05,
    max_holding_days: int = 10,
    side: pd.Series | None = None,
) -> pd.Series:
    """生成三重壁垒标签。

    参数：
      df               : OHLCV 数据框（列含 close/high/low）
      take_profit_pct  : 止盈阈值（如 0.05 = 5%）
      stop_loss_pct    : 止损阈值
      max_holding_days : 最大持有天数（时间壁垒）
      side             : 可选方向元标签（+1/-1），长度=len(df)。
                         若提供，则按 side 方向设置壁垒，
                         否则默认全部按做多方向。

    返回：
      pd.Series 索引与 df 对齐，值 ∈ {-1, 0, +1, NaN}。
      尾部 max_holding_days 行无法判定 → NaN。
    """
    if df is None or len(df) < max_holding_days + 5:
        return pd.Series(dtype=float)

    n = len(df)
    close = df["close"].to_numpy(dtype=float)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)

    if side is None:
        side_arr = np.ones(n)  # 默认做多
    else:
        side_arr = np.where(side.reindex(df.index).fillna(1).to_numpy() >= 0, 1, -1)

    labels = np.full(n, np.nan)
    for i in range(n - max_holding_days):
        entry = close[i]
        s = side_arr[i]
        tp = entry * (1 + s * take_profit_pct)  # 止盈线
        sl = entry * (1 - s * stop_loss_pct)     # 止损线

        hit = np.nan  # 触碰结果
        for j in range(i + 1, min(i + 1 + max_holding_days, n)):
            # 用 high/low 判断是否触碰（更贴近真实）
            if s == 1:  # 做多：先看止损还是止盈（保守起见假设先触及不利线）
                if low[j] <= sl:
                    hit = -1
                    break
                if high[j] >= tp:
                    hit = 1
                    break
            else:  # 做空
                if high[j] >= sl:
                    hit = -1
                    break
                if low[j] <= tp:
                    hit = 1
                    break

        if hit is np.nan or np.isnan(hit):
            # 时间壁垒：按到期收益方向
            exit_price = close[min(i + max_holding_days, n - 1)]
            ret = (exit_price / entry - 1) * s
            hit = 1 if ret > 0 else (-1 if ret < 0 else 0)

        labels[i] = hit

    return pd.Series(labels, index=df.index, name="label")


def binary_labels(
    df: pd.DataFrame,
    forward_days: int = 5,
    threshold_pct: float = 0.02,
) -> pd.Series:
    """简易二分类标签：未来 N 日收益率超过阈值 → 1，否则 0。

    适合快速基线；三重壁垒更贴近实战。
    threshold_pct=0.02 表示 ±2% 之外才算有效涨/跌，中间归为 0。
    """
    if df is None or len(df) < forward_days + 5:
        return pd.Series(dtype=float)
    close = df["close"].astype(float)
    fwd_ret = close.shift(-forward_days) / close - 1.0
    labels = np.where(
        fwd_ret > threshold_pct, 1,
        np.where(fwd_ret < -threshold_pct, -1, 0),
    )
    return pd.Series(labels, index=df.index, name="label").astype(float)
