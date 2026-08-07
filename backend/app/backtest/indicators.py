"""技术指标计算：MACD / KDJ / 布林带 / RSI。

从原 app/backtest.py 拆分而来，函数签名与实现保持不变。
"""
from __future__ import annotations

import pandas as pd


# ==================== 技术指标计算 ====================

def _calc_macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """计算MACD：返回 (dif, dea, hist) 三个 pd.Series。"""
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    hist = (dif - dea) * 2.0  # MACD柱（国内常用2倍）
    return dif, dea, hist


def _calc_kdj(df: pd.DataFrame, k_period: int = 9, d_period: int = 3):
    """计算KDJ：返回 (K, D, J) 三个 pd.Series。"""
    low_min = df["low"].rolling(window=k_period, min_periods=1).min()
    high_max = df["high"].rolling(window=k_period, min_periods=1).max()
    rsv = (df["close"] - low_min) / (high_max - low_min) * 100.0
    rsv = rsv.fillna(50.0)
    k = rsv.ewm(com=d_period - 1, adjust=False).mean()
    d = k.ewm(com=d_period - 1, adjust=False).mean()
    j = 3.0 * k - 2.0 * d
    return k, d, j


def _calc_boll(close: pd.Series, period: int = 20, std: float = 2.0):
    """布林带：返回 (upper, mid, lower)。"""
    mid = close.rolling(window=period, min_periods=period).mean()
    sd = close.rolling(window=period, min_periods=period).std(ddof=0)
    upper = mid + std * sd
    lower = mid - std * sd
    return upper, mid, lower


def _calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """RSI（Wilder平滑）。"""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    # Wilder 平滑
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, pd.NA)
    rsi = 100.0 - 100.0 / (1.0 + rs)
    return rsi.fillna(50.0)
