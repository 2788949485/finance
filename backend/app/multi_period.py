"""多周期共振分析：从日K聚合周K/月K，判断多周期趋势是否一致。

共振定义：日线、周线、月线趋势方向一致时为"共振"，
共振信号比单周期信号可靠度高。

趋势判断维度：
- MA5 vs MA20 多空排列
- MACD 金叉死叉
- 价格相对均线位置
"""
from __future__ import annotations

from typing import Any, Optional
import pandas as pd

from .data import fetcher as datalayer


def resample_weekly(df: pd.DataFrame) -> pd.DataFrame:
    """日K聚合为周K。"""
    weekly = df.resample("W", on="date").agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum",
    }).dropna()
    weekly["date"] = weekly.index
    weekly = weekly.reset_index(drop=True)
    if len(weekly) >= 5:
        weekly["ma5"] = weekly["close"].rolling(5).mean()
    if len(weekly) >= 20:
        weekly["ma20"] = weekly["close"].rolling(20).mean()
    return weekly


def resample_monthly(df: pd.DataFrame) -> pd.DataFrame:
    """日K聚合为月K。"""
    monthly = df.resample("ME", on="date").agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum",
    }).dropna()
    monthly["date"] = monthly.index
    monthly = monthly.reset_index(drop=True)
    if len(monthly) >= 5:
        monthly["ma5"] = monthly["close"].rolling(5).mean()
    if len(monthly) >= 20:
        monthly["ma20"] = monthly["close"].rolling(20).mean()
    return monthly


def analyze_period_trend(df: pd.DataFrame, period_name: str) -> dict[str, Any]:
    """分析单个周期的趋势。"""
    if df is None or len(df) < 5:
        return {"period": period_name, "error": "数据不足"}

    last = df.iloc[-1]
    close = float(last["close"])
    ma5 = float(last.get("ma5", 0)) if "ma5" in df.columns else 0
    ma20 = float(last.get("ma20", 0)) if "ma20" in df.columns else 0

    # MA多空排列
    ma_bull = ma5 > 0 and ma20 > 0 and ma5 > ma20

    # MACD
    macd_signal = "neutral"
    macd_dif = macd_dea = 0
    if len(df) >= 26:
        ema12 = df["close"].ewm(span=12, adjust=False).mean()
        ema26 = df["close"].ewm(span=26, adjust=False).mean()
        dif = ema12 - ema26
        dea = dif.ewm(span=9, adjust=False).mean()
        macd_dif = float(dif.iloc[-1])
        macd_dea = float(dea.iloc[-1])
        macd_signal = "bull" if macd_dif > macd_dea else "bear"

    # 价格位置
    above_ma5 = close > ma5 if ma5 > 0 else False
    above_ma20 = close > ma20 if ma20 > 0 else False

    # 综合方向
    bull_score = 0
    if ma_bull:
        bull_score += 1
    if macd_signal == "bull":
        bull_score += 1
    if above_ma5 and above_ma20:
        bull_score += 1

    direction = "多" if bull_score >= 2 else ("空" if bull_score == 0 else "震荡")

    return {
        "period": period_name,
        "close": round(close, 2),
        "ma5": round(ma5, 2) if ma5 > 0 else None,
        "ma20": round(ma20, 2) if ma20 > 0 else None,
        "ma_bull": ma_bull,
        "macd_signal": macd_signal,
        "macd_dif": round(macd_dif, 4),
        "macd_dea": round(macd_dea, 4),
        "above_ma5": above_ma5,
        "above_ma20": above_ma20,
        "direction": direction,
        "bull_score": bull_score,
    }


def get_multi_period_analysis(symbol: str) -> Optional[dict[str, Any]]:
    """多周期共振分析。

    返回 {
        symbol: str,
        daily: {...},     # 日线趋势
        weekly: {...},    # 周线趋势
        monthly: {...},   # 月线趋势
        resonance: "强多" / "弱多" / "强空" / "弱空" / "无共振",
        resonance_score: 0-6,
        summary: str,     # 一句话总结
    }
    """
    sym = datalayer._norm_symbol(symbol)
    hist = datalayer.get_history(sym, days=500)
    if hist is None or len(hist) < 60:
        return None

    # 确保date是datetime
    df = hist.copy()
    if not pd.api.types.is_datetime64_any_dtype(df["date"]):
        df["date"] = pd.to_datetime(df["date"])

    # 日线
    if "ma5" not in df.columns:
        df["ma5"] = df["close"].rolling(5).mean()
    if "ma20" not in df.columns:
        df["ma20"] = df["close"].rolling(20).mean()

    daily = analyze_period_trend(df, "日线")
    weekly = analyze_period_trend(resample_weekly(df), "周线")
    monthly = analyze_period_trend(resample_monthly(df), "月线")

    # 共振分析
    periods = [daily, weekly, monthly]
    bull_count = sum(1 for p in periods if p.get("direction") == "多")
    bear_count = sum(1 for p in periods if p.get("direction") == "空")
    total_score = sum(p.get("bull_score", 0) for p in periods)

    if bull_count == 3:
        resonance = "强多共振"
        summary = "日/周/月三周期全部看多，趋势高度一致"
    elif bull_count == 2 and bear_count == 0:
        resonance = "弱多共振"
        summary = "两个周期看多，大方向偏多但不够坚决"
    elif bear_count == 3:
        resonance = "强空共振"
        summary = "日/周/月三周期全部看空，下跌趋势一致"
    elif bear_count == 2 and bull_count == 0:
        resonance = "弱空共振"
        summary = "两个周期看空，大方向偏空"
    else:
        resonance = "无共振"
        summary = "多周期方向不一致，可能处于转折期或震荡"

    return {
        "symbol": sym,
        "daily": daily,
        "weekly": weekly,
        "monthly": monthly,
        "resonance": resonance,
        "resonance_score": total_score,
        "bull_periods": bull_count,
        "bear_periods": bear_count,
        "summary": summary,
    }
