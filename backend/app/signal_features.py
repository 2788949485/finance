"""ML信号诊断系统：特征快照提取 + CSV记录 + 标签生成。

每次回测产生买卖信号时，记录完整的特征快照（50+维度），
用于后续训练机器学习模型判断信号质量。

特征分为5大类：
1. 基础行情特征（K线形态、收益率、振幅）
2. 趋势特征（EMA、ADX、DI）
3. 波动率特征（ATR、布林带）
4. 交易环境特征（成交量、时间、周期位置）
5. 原策略状态特征（信号方向、过滤器状态）
"""
from __future__ import annotations

import csv
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import pandas as pd

# CSV输出目录
SIGNAL_LOG_DIR = Path(__file__).resolve().parent.parent / "data" / "ml_signals"
SIGNAL_LOG_DIR.mkdir(parents=True, exist_ok=True)

# 固定CSV表头（按顺序，不允许变动）
SIGNAL_CSV_COLUMNS = [
    # 标识
    "signal_id", "signal_time", "symbol", "timeframe",
    # 基础行情 - K线
    "direction", "open_1", "high_1", "low_1", "close_1",
    "open_2", "high_2", "low_2", "close_2",
    "body_size_1", "upper_shadow_1", "lower_shadow_1",
    # 基础行情 - 收益率
    "ret_1d", "ret_3d", "ret_5d", "ret_10d", "ret_20d",
    # 基础行情 - 振幅
    "amplitude_5d", "amplitude_10d", "amplitude_20d",
    # 基础行情 - 连续形态
    "consecutive_up", "consecutive_down",
    # 趋势特征
    "ema_fast", "ema_slow", "ema_distance", "ema_distance_atr",
    "ema_fast_slope", "ema_slow_slope",
    "price_vs_ema_fast", "price_vs_ema_slow",
    "adx", "adx_slope", "di_plus", "di_minus", "di_diff", "di_diff_adx_ratio",
    # 波动率
    "atr", "atr_price_ratio",
    "bb_width", "bb_width_change", "bb_position",
    "volatility_regime",  # 0=低波动 1=正常 2=高波动
    # 交易环境
    "volume", "volume_ma5_ratio",
    "day_of_week", "month",
    # 原策略状态
    "strategy", "filter_ema_pass", "filter_adx_pass", "filter_bb_pass",
    # 标签（回测结束后填充）
    "future_ret_5d", "future_ret_10d", "future_ret_20d",
    "future_ret_5d_atr", "future_ret_10d_atr", "future_ret_20d_atr",
    "label_cycle_profit", "label_fixed_5d", "label_fixed_10d", "label_fixed_20d",
    "cycle_realized_profit",
]


def build_signal_features(
    df: pd.DataFrame,
    i: int,
    symbol: str,
    direction: int,
    strategy: str,
    ema_fast_period: int = 5,
    ema_slow_period: int = 20,
) -> Optional[dict[str, Any]]:
    """在信号产生时构建完整的特征快照。

    参数:
        df: 完整的K线DataFrame（含ma5/ma20列）
        i: 当前K线索引（信号产生位置）
        symbol: 股票代码
        direction: 1=做多, -1=做空
        strategy: 策略名称
        ema_fast_period: 快线周期
        ema_slow_period: 慢线周期

    返回: 包含50+维度的特征字典，或None（数据不足）
    """
    if i < 20 or i >= len(df):
        return None

    row = df.iloc[i]
    prev = df.iloc[i - 1]
    closes = df["close"].tolist()
    highs = df["high"].tolist()
    lows = df["low"].tolist()
    vols = df["volume"].tolist()
    price = float(row["close"])

    # --- 基础行情特征 ---
    c1 = float(row["close"])  # 信号K线（i）
    o1 = float(row["open"])
    h1 = float(row["high"])
    l1 = float(row["low"])
    c2 = float(prev["close"])  # 前一根（i-1）
    o2 = float(prev["open"])
    h2 = float(prev["high"])
    l2 = float(prev["low"])

    body_1 = abs(c1 - o1)
    upper_shadow_1 = h1 - max(c1, o1)
    lower_shadow_1 = min(c1, o1) - l1

    # 收益率
    def _ret(n: int) -> float:
        if i >= n and closes[i - n] > 0:
            return round((closes[i] / closes[i - n] - 1) * 100, 4)
        return 0.0

    # 振幅
    def _amplitude(n: int) -> float:
        if i >= n:
            window_h = max(highs[i - n + 1 : i + 1])
            window_l = min(lows[i - n + 1 : i + 1])
            if window_l > 0:
                return round((window_h / window_l - 1) * 100, 4)
        return 0.0

    # 连续上涨/下跌
    consec_up = 0
    consec_down = 0
    for j in range(i, max(i - 20, 0), -1):
        if closes[j] > closes[j - 1]:
            consec_up += 1
            break_consec = False
        else:
            break
    for j in range(i, max(i - 20, 0), -1):
        if closes[j] < closes[j - 1]:
            consec_down += 1
        else:
            break

    # --- 趋势特征 ---
    ema_fast = _ema(df["close"], ema_fast_period, i)
    ema_slow = _ema(df["close"], ema_slow_period, i)
    ema_fast_prev = _ema(df["close"], ema_fast_period, i - 1) if i > 0 else ema_fast
    ema_slow_prev = _ema(df["close"], ema_slow_period, i - 1) if i > 0 else ema_slow

    ema_distance = (ema_fast - ema_slow) / price * 100 if price > 0 else 0

    # ATR
    atr = _atr(df, i, 14)
    ema_distance_atr = (ema_fast - ema_slow) / atr if atr > 0 else 0

    # EMA斜率
    ema_fast_slope = (ema_fast - ema_fast_prev) / price * 100 if price > 0 else 0
    ema_slow_slope = (ema_slow - ema_slow_prev) / price * 100 if price > 0 else 0

    # ADX/DI（简化版）
    adx_val, di_plus, di_minus = _adx_di(df, i, 14)
    di_diff = di_plus - di_minus
    di_diff_adx = di_diff / adx_val if adx_val > 0 else 0

    # --- 波动率特征 ---
    atr_price_ratio = atr / price * 100 if price > 0 else 0

    # 布林带
    bb_mid = ema_slow  # 用EMA20做中轨
    bb_std = df["close"].iloc[i - 19 : i + 1].std() if i >= 19 else 0
    bb_upper = bb_mid + 2 * bb_std
    bb_lower = bb_mid - 2 * bb_std
    bb_width = (bb_upper - bb_lower) / bb_mid * 100 if bb_mid > 0 else 0
    bb_width_prev = 0.0
    if i >= 20:
        bb_mid_prev = _ema(df["close"], 20, i - 1)
        bb_std_prev = df["close"].iloc[i - 20 : i].std() if i >= 20 else 0
        bb_width_prev = (bb_mid_prev + 2 * bb_std_prev - (bb_mid_prev - 2 * bb_std_prev)) / bb_mid_prev * 100 if bb_mid_prev > 0 else 0
    bb_width_change = bb_width - bb_width_prev
    bb_position = (price - bb_lower) / (bb_upper - bb_lower) if (bb_upper - bb_lower) > 0 else 0.5

    # 波动率分位
    if i >= 60:
        atr_history = [_atr(df, j, 14) for j in range(i - 60, i)]
        atr_pct = sum(1 for a in atr_history if a < atr) / len(atr_history)
    else:
        atr_pct = 0.5
    vol_regime = 0 if atr_pct < 0.33 else (1 if atr_pct < 0.66 else 2)

    # --- 交易环境 ---
    vol = int(vols[i]) if i < len(vols) else 0
    vol_ma5 = sum(vols[max(0, i - 4) : i + 1]) / min(5, i + 1) if i > 0 else vol
    vol_ma5_ratio = vol / vol_ma5 if vol_ma5 > 0 else 1.0

    dt = row["date"]
    day_of_week = dt.weekday() if hasattr(dt, "weekday") else 0
    month = dt.month if hasattr(dt, "month") else 0

    # --- signal_id ---
    dt_str = dt.strftime("%Y%m%d") if hasattr(dt, "strftime") else str(dt)[:10]
    signal_id = f"{symbol}_D_{dt_str}_{i}_{'BUY' if direction > 0 else 'SELL'}"

    return {
        # 标识
        "signal_id": signal_id,
        "signal_time": dt_str,
        "symbol": symbol,
        "timeframe": "D1",
        # K线
        "direction": direction,
        "open_1": round(o1, 4), "high_1": round(h1, 4), "low_1": round(l1, 4), "close_1": round(c1, 4),
        "open_2": round(o2, 4), "high_2": round(h2, 4), "low_2": round(l2, 4), "close_2": round(c2, 4),
        "body_size_1": round(body_1, 4),
        "upper_shadow_1": round(upper_shadow_1, 4),
        "lower_shadow_1": round(lower_shadow_1, 4),
        # 收益率
        "ret_1d": _ret(1), "ret_3d": _ret(3), "ret_5d": _ret(5), "ret_10d": _ret(10), "ret_20d": _ret(20),
        # 振幅
        "amplitude_5d": _amplitude(5), "amplitude_10d": _amplitude(10), "amplitude_20d": _amplitude(20),
        # 连续形态
        "consecutive_up": consec_up, "consecutive_down": consec_down,
        # 趋势
        "ema_fast": round(ema_fast, 4), "ema_slow": round(ema_slow, 4),
        "ema_distance": round(ema_distance, 4), "ema_distance_atr": round(ema_distance_atr, 4),
        "ema_fast_slope": round(ema_fast_slope, 4), "ema_slow_slope": round(ema_slow_slope, 4),
        "price_vs_ema_fast": round((price - ema_fast) / price * 100, 4) if price > 0 else 0,
        "price_vs_ema_slow": round((price - ema_slow) / price * 100, 4) if price > 0 else 0,
        "adx": round(adx_val, 2), "adx_slope": round(_adx_di(df, i - 1, 14)[0] if i > 0 else adx_val, 2),
        "di_plus": round(di_plus, 2), "di_minus": round(di_minus, 2),
        "di_diff": round(di_diff, 2), "di_diff_adx_ratio": round(di_diff_adx, 4),
        # 波动率
        "atr": round(atr, 4), "atr_price_ratio": round(atr_price_ratio, 4),
        "bb_width": round(bb_width, 4), "bb_width_change": round(bb_width_change, 4),
        "bb_position": round(bb_position, 4),
        "volatility_regime": vol_regime,
        # 交易环境
        "volume": vol, "volume_ma5_ratio": round(vol_ma5_ratio, 4),
        "day_of_week": day_of_week, "month": month,
        # 原策略状态
        "strategy": strategy,
        "filter_ema_pass": int(direction > 0 and ema_fast > ema_slow or direction < 0 and ema_fast < ema_slow),
        "filter_adx_pass": int(adx_val > 20),
        "filter_bb_pass": int(bb_position < 0.3 or bb_position > 0.7),
        # 标签占位（回测结束后填充）
        "future_ret_5d": None, "future_ret_10d": None, "future_ret_20d": None,
        "future_ret_5d_atr": None, "future_ret_10d_atr": None, "future_ret_20d_atr": None,
        "label_cycle_profit": None, "label_fixed_5d": None,
        "label_fixed_10d": None, "label_fixed_20d": None,
        "cycle_realized_profit": None,
    }


def save_signals_to_csv(signals: list[dict[str, Any]], filename: str = None) -> str:
    """保存信号快照列表到CSV文件。返回文件路径。"""
    if not filename:
        filename = f"signals_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    filepath = SIGNAL_LOG_DIR / filename

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=SIGNAL_CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for signal in signals:
            writer.writerow(signal)

    return str(filepath)


def fill_labels(
    signals: list[dict[str, Any]],
    df: pd.DataFrame,
    threshold_atr: float = 0.3,
) -> list[dict[str, Any]]:
    """回测结束后，给每个信号填充标签。

    标签A: 固定窗口ATR标准化收益
    标签B: 是否>threshold_atr倍ATR

    参数:
        signals: 信号列表
        df: 完整K线数据
        threshold_atr: 标签阈值（默认0.3倍ATR）
    """
    closes = df["close"].tolist()

    for signal in signals:
        idx = None
        # 找到信号在df中的位置
        for j in range(len(df)):
            if df.iloc[j]["date"].strftime("%Y%m%d") == signal["signal_time"]:
                idx = j
                break
        if idx is None:
            continue

        direction = signal["direction"]
        entry_price = signal["close_1"]
        atr = signal["atr"] if signal["atr"] > 0 else 1.0

        # 固定窗口收益
        for window, key_ret, key_atr, key_label in [
            (5, "future_ret_5d", "future_ret_5d_atr", "label_fixed_5d"),
            (10, "future_ret_10d", "future_ret_10d_atr", "label_fixed_10d"),
            (20, "future_ret_20d", "future_ret_20d_atr", "label_fixed_20d"),
        ]:
            if idx + window < len(closes):
                future_close = closes[idx + window]
                if direction > 0:
                    ret = future_close - entry_price
                else:
                    ret = entry_price - future_close
                ret_atr = ret / atr
                signal[key_ret] = round(ret, 4)
                signal[key_atr] = round(ret_atr, 4)
                signal[key_label] = 1 if ret_atr > threshold_atr else 0
            else:
                signal[key_ret] = None
                signal[key_atr] = None
                signal[key_label] = None

    return signals


# ---------- 技术指标辅助函数 ----------

def _ema(series: pd.Series, period: int, end_idx: int) -> float:
    """计算指定位置的EMA值。"""
    if end_idx < period - 1:
        return float(series.iloc[end_idx]) if end_idx >= 0 else 0.0
    alpha = 2 / (period + 1)
    window = series.iloc[max(0, end_idx - period * 3) : end_idx + 1].tolist()
    if not window:
        return 0.0
    ema_val = window[0]
    for v in window[1:]:
        ema_val = alpha * v + (1 - alpha) * ema_val
    return ema_val


def _atr(df: pd.DataFrame, idx: int, period: int = 14) -> float:
    """计算ATR。"""
    if idx < period:
        return 0.0
    trs = []
    for j in range(max(period, idx - period + 1), idx + 1):
        high = float(df.iloc[j]["high"])
        low = float(df.iloc[j]["low"])
        prev_close = float(df.iloc[j - 1]["close"])
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    return sum(trs) / len(trs) if trs else 0.0


def _adx_di(df: pd.DataFrame, idx: int, period: int = 14) -> tuple[float, float, float]:
    """计算ADX和DI+/DI-。返回(adx, di_plus, di_minus)。"""
    if idx < period * 2:
        return (25.0, 20.0, 20.0)  # 默认值

    plus_dms = []
    minus_dms = []
    trs = []
    for j in range(idx - period + 1, idx + 1):
        high = float(df.iloc[j]["high"])
        low = float(df.iloc[j]["low"])
        prev_high = float(df.iloc[j - 1]["high"])
        prev_low = float(df.iloc[j - 1]["low"])
        prev_close = float(df.iloc[j - 1]["close"])

        up_move = high - prev_high
        down_move = prev_low - low
        plus_dm = up_move if up_move > down_move and up_move > 0 else 0
        minus_dm = down_move if down_move > up_move and down_move > 0 else 0
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))

        plus_dms.append(plus_dm)
        minus_dms.append(minus_dm)
        trs.append(tr)

    avg_tr = sum(trs) / len(trs) if trs else 1
    avg_plus = sum(plus_dms) / len(plus_dms) if plus_dms else 0
    avg_minus = sum(minus_dms) / len(minus_dms) if minus_dms else 0

    di_plus = avg_plus / avg_tr * 100 if avg_tr > 0 else 0
    di_minus = avg_minus / avg_tr * 100 if avg_tr > 0 else 0
    dx = abs(di_plus - di_minus) / (di_plus + di_minus) * 100 if (di_plus + di_minus) > 0 else 0
    # 简化ADX = DX
    return (dx, di_plus, di_minus)
