"""特征工程：从原始 OHLCV K 线构造预测信号。

特征族：
  - 收益率族   : 1d/5d/10d/20d 收益率
  - 动量族     : RSI(14)、MACD、价格距 N 日均线偏移%
  - 波动率族   : N 日收益率标准差（年化）、布林带宽、ATR
  - 量能族     : 量比、OBV 一阶差分、成交额动量
  - 结构族     : N 日新高新低距离、价格分位

所有特征在 t 时只能用 t 及之前的信息（无未来函数）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# 默认特征列（模型训练时按此顺序取列）
DEFAULT_FEATURE_COLUMNS = [
    "ret_1d", "ret_5d", "ret_10d", "ret_20d",
    "rsi_14", "macd_hist", "ma_bias_5", "ma_bias_20", "ma_bias_60",
    "vol_5d", "vol_20d", "bb_width",
    "volume_ratio_5", "volume_ratio_20", "obv_diff",
    "dist_high_20", "dist_low_20", "price_quantile_20",
]


def add_features(df: pd.DataFrame, dropna: bool = True) -> pd.DataFrame:
    """为 OHLCV 数据框附加 ML 特征列。

    输入要求：列含 date, open, close, high, low, volume（与 fetcher.get_history 一致）。
    返回：原 df 的副本，新增特征列；不足数据时对应列为 NaN。

    参数：
      dropna : 是否丢弃头部因滚动窗口产生 NaN 的行（默认 True）
    """
    if df is None or len(df) < 30:
        return df.copy() if df is not None else pd.DataFrame()

    out = df.copy().reset_index(drop=True)
    close = out["close"].astype(float)
    high = out["high"].astype(float)
    low = out["low"].astype(float)
    volume = out["volume"].astype(float)

    # ---- 收益率族 ----
    for w in (1, 5, 10, 20):
        out[f"ret_{w}d"] = close.pct_change(w)

    # ---- 动量族 ----
    out["rsi_14"] = _rsi(close, 14)
    macd_line, signal_line, hist = _macd(close)
    out["macd_hist"] = hist

    # 均线偏移 %（price / MA - 1）
    for w in (5, 20, 60):
        ma = close.rolling(w).mean()
        out[f"ma_bias_{w}"] = close / ma - 1.0

    # ---- 波动率族 ----
    ret1 = close.pct_change()
    # 年化波动（√252），用收益率标准差
    out["vol_5d"] = ret1.rolling(5).std() * np.sqrt(252)
    out["vol_20d"] = ret1.rolling(20).std() * np.sqrt(252)
    # 布林带宽：(upper - lower) / mid
    mid = close.rolling(20).mean()
    std20 = close.rolling(20).std()
    out["bb_width"] = (4 * std20) / mid.replace(0, np.nan)

    # ---- 量能族 ----
    vol_ma5 = volume.rolling(5).mean()
    vol_ma20 = volume.rolling(20).mean()
    out["volume_ratio_5"] = volume / vol_ma5.replace(0, np.nan)
    out["volume_ratio_20"] = volume / vol_ma20.replace(0, np.nan)
    # OBV 一阶差分（归一化为相对变化）
    obv = (np.sign(close.diff()) * volume).cumsum()
    out["obv_diff"] = obv.pct_change(5).replace([np.inf, -np.inf], np.nan)

    # ---- 结构族 ----
    high_20 = high.rolling(20).max()
    low_20 = low.rolling(20).min()
    out["dist_high_20"] = close / high_20 - 1.0   # 距 20 日新高，<=0
    out["dist_low_20"] = close / low_20 - 1.0     # 距 20 日新低，>=0
    # 过去 20 日收盘价分位（0=最低，1=最高）
    out["price_quantile_20"] = close.rolling(20).apply(
        _rolling_quantile_rank, raw=True
    )

    # 替换 inf
    out = out.replace([np.inf, -np.inf], np.nan)

    if dropna:
        # 丢弃头部因 60 日窗口产生的 NaN
        first_valid = out[DEFAULT_FEATURE_COLUMNS].apply(
            lambda c: c.first_valid_index()
        ).max()
        if first_valid is not None and not pd.isna(first_valid):
            out = out.iloc[int(first_valid):].reset_index(drop=True)

    return out


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """RSI 指标（Wilder 平滑近似为简单滚动均值，与 backtest.py 一致）。"""
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, 1e-9)
    return 100 - 100 / (1 + rs)


def _macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """MACD 三件套：(DIF, DEA, hist)。"""
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    hist = 2 * (dif - dea)
    return dif, dea, hist


def _rolling_quantile_rank(arr: np.ndarray) -> float:
    """当前值在过去窗口的分位（0~1）。用于 apply(raw=True)。"""
    if len(arr) == 0:
        return np.nan
    last = arr[-1]
    if np.isnan(last):
        return np.nan
    valid = arr[~np.isnan(arr)]
    if len(valid) <= 1:
        return np.nan
    return float(np.sum(valid <= last) / len(valid))
