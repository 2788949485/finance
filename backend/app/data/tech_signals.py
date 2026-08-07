"""技术指标计算（MA/MACD/KDJ/RSI/BOLL/OBV/CCI/WR/DMI/ADX/SAR/量比）。

从原 a_stock.py 拆分而来；函数签名、行为、返回值均未改变。
"""
from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from .utils import _to_float


def compute_tech_signals(df: Optional[pd.DataFrame]) -> dict[str, Any]:
    """从日线数据计算技术面指标。

    指标清单：
    - MA5/MA20/MA60 均线
    - RSI14 相对强弱
    - MACD 指数平滑异同
    - KDJ 随机指标
    - BOLL 布林带
    - OBV 能量潮
    - CCI 顺势指标
    - WR 威廉指标
    - DMI 趋向指标(ADX/DI+/DI-)
    - SAR 抛物线转向
    - 量比
    """
    if df is None or df.empty or len(df) < 20:
        return {"error": "历史数据不足"}
    last = df.iloc[-1]
    close = float(last["close"])
    high = float(last["high"])
    low = float(last["low"])
    vol = float(last["volume"])
    sig = {
        "price": close,
        "ma5": _to_float(last.get("ma5")),
        "ma20": _to_float(last.get("ma20")),
        "ma60": _to_float(last.get("ma60")),
        "ret_5d": _to_float((close / df["close"].iloc[-6] - 1) * 100) if len(df) >= 6 else None,
        "ret_20d": _to_float((close / df["close"].iloc[-21] - 1) * 100) if len(df) >= 21 else None,
        "ret_60d": _to_float((close / df["close"].iloc[-61] - 1) * 100) if len(df) >= 61 else None,
        "high_60d": _to_float(df["high"].tail(60).max()),
        "low_60d": _to_float(df["low"].tail(60).min()),
    }

    # RSI14
    delta = df["close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, 1e-9)
    sig["rsi14"] = _to_float((100 - 100 / (1 + rs)).iloc[-1])

    # MACD (12,26,9)
    ema12 = df["close"].ewm(span=12, adjust=False).mean()
    ema26 = df["close"].ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    sig["macd_dif"] = round(_to_float(dif.iloc[-1]), 4)
    sig["macd_dea"] = round(_to_float(dea.iloc[-1]), 4)
    sig["macd_hist"] = round(_to_float((dif - dea).iloc[-1] * 2), 4)

    # KDJ (9,3,3)
    low_9 = df["low"].rolling(9).min()
    high_9 = df["high"].rolling(9).max()
    rsv = (df["close"] - low_9) / (high_9 - low_9).replace(0, 1e-9) * 100
    k = rsv.ewm(com=2, adjust=False).mean()
    d = k.ewm(com=2, adjust=False).mean()
    sig["kdj_k"] = round(_to_float(k.iloc[-1]), 2)
    sig["kdj_d"] = round(_to_float(d.iloc[-1]), 2)
    sig["kdj_j"] = round(_to_float(3 * k.iloc[-1] - 2 * d.iloc[-1]), 2)

    # BOLL (20,2)
    boll_mid = df["close"].rolling(20).mean()
    boll_std = df["close"].rolling(20).std()
    sig["boll_mid"] = round(_to_float(boll_mid.iloc[-1]), 2)
    sig["boll_upper"] = round(_to_float(boll_mid.iloc[-1] + 2 * boll_std.iloc[-1]), 2)
    sig["boll_lower"] = round(_to_float(boll_mid.iloc[-1] - 2 * boll_std.iloc[-1]), 2)
    sig["boll_width"] = round(_to_float((sig["boll_upper"] - sig["boll_lower"]) / max(sig["boll_mid"], 1) * 100), 2)

    # OBV 能量潮
    obv = (df["volume"] * (df["close"].diff() > 0).astype(int) * 2 - 1).cumsum()
    sig["obv"] = _to_float(obv.iloc[-1])
    sig["obv_ma5"] = _to_float(obv.tail(5).mean()) if len(obv) >= 5 else None

    # CCI 顺势指标 (14)
    tp = (df["high"] + df["low"] + df["close"]) / 3
    tp_sma = tp.rolling(14).mean()
    tp_md = tp.rolling(14).apply(lambda x: abs(x - x.mean()).mean(), raw=True)
    cci = (tp - tp_sma) / (0.015 * tp_md.replace(0, 1e-9))
    sig["cci14"] = round(_to_float(cci.iloc[-1]), 2)

    # WR 威廉指标 (14)
    wr_high = df["high"].rolling(14).max()
    wr_low = df["low"].rolling(14).min()
    wr = (wr_high - df["close"]) / (wr_high - wr_low).replace(0, 1e-9) * -100
    sig["wr14"] = round(_to_float(wr.iloc[-1]), 2)

    # DMI 趋向指标 (14)
    if len(df) >= 28:
        up_move = df["high"].diff()
        down_move = -df["low"].diff()
        plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0)
        minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0)
        tr = pd.concat([df["high"] - df["low"], (df["high"] - df["close"].shift()).abs(), (df["low"] - df["close"].shift()).abs()], axis=1).max(axis=1)
        atr_dmi = tr.rolling(14).mean()
        plus_di = 100 * plus_dm.rolling(14).mean() / atr_dmi.replace(0, 1e-9)
        minus_di = 100 * minus_dm.rolling(14).mean() / atr_dmi.replace(0, 1e-9)
        dx = (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1e-9) * 100
        adx = dx.rolling(14).mean()
        sig["adx"] = round(_to_float(adx.iloc[-1]), 2)
        sig["di_plus"] = round(_to_float(plus_di.iloc[-1]), 2)
        sig["di_minus"] = round(_to_float(minus_di.iloc[-1]), 2)
    else:
        sig["adx"] = sig["di_plus"] = sig["di_minus"] = None

    # SAR 抛物线转向 (简化版)
    if len(df) >= 10:
        af = 0.02
        max_af = 0.2
        sar_list = []
        is_up = True
        ep = float(df["high"].iloc[0])
        sar = float(df["low"].iloc[0])
        for i in range(1, len(df)):
            h = float(df["high"].iloc[i])
            l = float(df["low"].iloc[i])
            sar = sar + af * (ep - sar)
            if is_up:
                if l < sar:
                    is_up = False
                    sar = ep
                    ep = l
                    af = 0.02
                else:
                    if h > ep:
                        ep = h
                        af = min(af + 0.02, max_af)
            else:
                if h > sar:
                    is_up = True
                    sar = ep
                    ep = h
                    af = 0.02
                else:
                    if l < ep:
                        ep = l
                        af = min(af + 0.02, max_af)
            sar_list.append(sar)
        sig["sar"] = round(sar, 2)
        sig["sar_signal"] = "看多" if is_up else "看空"

    sig["volume_ratio"] = _to_float(df["volume"].iloc[-1] / df["volume"].tail(5).mean()) if len(df) >= 5 else None
    return sig
