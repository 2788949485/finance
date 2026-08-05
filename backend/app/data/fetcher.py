"""A股数据获取层。

- 实时行情：腾讯 qt.gtimg.cn（绕开东方财富 push2 对 Python 客户端的 TLS 指纹封锁）
- 历史行情/财务/龙虎榜/新闻：akshare（东方财富 push2his / 同花顺 / 新浪）

所有函数容错：网络异常或接口变动时返回 None/空值，保证流水线降级运行。
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta
from typing import Any, Optional

import pandas as pd
import requests

# A股数据源域名绕过代理直连（本机代理对国内数据源转发不稳定，
# 而直连东方财富/腾讯/新浪/同花顺均可达）。
_CN_DATA_DOMAINS = (
    "eastmoney.com,push2his.eastmoney.com,10jqka.com.cn,ths.cn,"
    "sina.com.cn,sse.com.cn,sseinfo.com,cninfo.com.cn,xueqiu.com,"
    "gtimg.cn,qq.com"
)
os.environ["NO_PROXY"] = os.environ.get("NO_PROXY", "") + "," + _CN_DATA_DOMAINS
os.environ["no_proxy"] = os.environ["NO_PROXY"]

try:
    import akshare as ak
    AK_AVAILABLE = True
except Exception:  # pragma: no cover
    ak = None
    AK_AVAILABLE = False

from ..cache import TTL, cached  # noqa: E402


def _safe(fn, *args, **kwargs):
    """统一容错包装。"""
    try:
        return fn(*args, **kwargs)
    except Exception:
        return None


def data_available() -> bool:
    return AK_AVAILABLE


def _norm_symbol(symbol: str) -> str:
    """统一股票代码格式：A股6位 / 港股 hk+5位 / 美股 us+代码。

    规则：hk/us 前缀原样保留；6位数字=A股；5位数字（如 00700）=港股；
    更短数字=港股补零；其他按原样返回（公司名交给 resolve_symbol 处理）。
    """
    s = symbol.strip().lower()
    if s.startswith(("hk", "us")):
        code = s[2:]
        if code.isdigit():
            return s[:2] + code.zfill(5) if len(code) < 5 else s[:2] + code
        return s[:2] + code.upper()  # 美股代码大写（usAAPL）
    if s.isdigit():
        if len(s) == 5:
            return "hk" + s
        if len(s) <= 4:
            return "hk" + s.zfill(5)
        return s.zfill(6)
    return s


def _market_prefix(symbol: str) -> str:
    """腾讯接口的市场前缀：A股 sh/sz/bj，港股/美股无需前缀（代码自带 hk/us）。"""
    if symbol.startswith(("hk", "us")):
        return ""
    if symbol[0] in "69":
        return "sh"
    if symbol[0] in "48":
        return "bj"
    return "sz"


def get_stock_brief(symbol: str, fresh: bool = False) -> Optional[dict[str, Any]]:
    """个股概览（腾讯实时行情），默认缓存 60 秒；fresh=True 时强制实时请求。

    腾讯接口返回 GBK 编码的 ~ 分隔字段，实测索引：
    p[1]=名称 p[3]=现价 p[32]=涨跌% p[38]=换手% p[39]=PE p[45]=总市值(亿) p[46]=PB
    """
    sym = _norm_symbol(symbol)

    def _fetch() -> Optional[dict[str, Any]]:
        url = f"https://qt.gtimg.cn/q={_market_prefix(sym)}{sym}"
        try:
            r = requests.get(url, timeout=10)
            r.encoding = "gbk"
            body = r.text.split('"')[1] if '"' in r.text else ""
            p = body.split("~")
            if len(p) < 47 or not p[1]:
                return None
            return {
                "symbol": sym,
                "name": p[1],
                "price": _to_float(p[3]),
                "change_pct": _to_float(p[32]),
                "market_cap": _to_float(p[45]),  # 单位：亿元
                "pe": _to_float(p[39]),
                "pb": _to_float(p[46]),
                "turnover": _to_float(p[38]),
                "industry": "",
            }
        except Exception:
            return None

    if fresh:
        return _fetch()  # fresh=True：直连腾讯，不读缓存
    return cached(f"quote:{sym}", TTL["quote"], _fetch)


def _fetch_us_kline(sym: str, days: int) -> Optional[dict[str, Any]]:
    """美股日K线（新浪接口，1984年至今完整历史，国内直连）。

    返回 JSONP：var _=([{"d":"1984-09-07","o":"26.50","h":"26.87","l":"26.25","c":"26.50","v":...}, ...])
    """
    import json as _json

    ticker = sym[2:]  # usAAPL -> AAPL
    url = (
        "https://stock.finance.sina.com.cn/usstock/api/jsonp_v2.php/"
        f"var%20_=/US_MinKService.getDailyK?symbol={ticker}"
    )
    try:
        r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"})
        text = r.text
        start, end = text.find("(["), text.rfind("])")
        if start == -1 or end == -1:
            return None
        data = _json.loads(text[start + 1 : end + 1])
        bars = []
        for row in data[-days:]:
            try:
                bars.append({
                    "date": str(row["d"]),
                    "open": float(row["o"]),
                    "close": float(row["c"]),
                    "high": float(row["h"]),
                    "low": float(row["l"]),
                    "volume": float(row.get("v", 0)),
                })
            except (KeyError, ValueError, TypeError):
                continue
        return {"bars": bars} if bars else None
    except Exception:
        return None


def _fetch_us_kline_aggregated(sym: str, period: str, count: int) -> Optional[dict[str, Any]]:
    """美股周K/月K：从新浪日K数据聚合。

    period: 'week' 或 'month'
    """
    # 多取数据确保聚合后有足够的周/月
    need_days = count * 7 if period == "week" else count * 31
    raw = _fetch_us_kline(sym, min(need_days, 5000))
    if raw is None or not raw.get("bars"):
        return None

    df = pd.DataFrame(raw["bars"])
    df["date"] = pd.to_datetime(df["date"])

    if period == "week":
        # 按周聚合：取每周第一天的日期，OHLC聚合
        df["period_key"] = df["date"].dt.to_period("W")
    else:
        df["period_key"] = df["date"].dt.to_period("M")

    agg = df.groupby("period_key").agg(
        date=("date", "first"),
        open=("open", "first"),
        close=("close", "last"),
        high=("high", "max"),
        low=("low", "min"),
        volume=("volume", "sum"),
    ).reset_index(drop=True)

    agg = agg.sort_values("date").tail(count)

    bars = []
    for _, row in agg.iterrows():
        bars.append({
            "date": row["date"].strftime("%Y-%m-%d"),
            "open": float(row["open"]),
            "close": float(row["close"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "volume": float(row["volume"]),
        })
    return {"bars": bars} if bars else None


def _fetch_a_share_minute_akshare(sym: str, m_param: str, count: int) -> Optional[dict[str, Any]]:
    """A股分钟级K线（AKShare腾讯源，国内直连）。

    m_param: m5/m15/m30/m60
    sym: 6位A股代码(无前缀)
    """
    try:
        import akshare as ak
        # AKShare需要带市场前缀的代码: sh600519 / sz000001
        prefix = "sh" if sym.startswith(("6", "9")) else "sz"
        ak_code = f"{prefix}{sym}"
        period_map = {"m5": "5", "m15": "15", "m30": "30", "m60": "60"}
        ak_period = period_map.get(m_param, "5")
        df = ak.stock_zh_a_minute(symbol=ak_code, period=ak_period, adjust="qfq")
        if df is None or len(df) == 0:
            return None
        # AKShare列: day, open, high, low, close, volume, amount
        df = df.tail(count)
        bars = []
        for _, row in df.iterrows():
            dt_str = str(row["day"])[:16]  # YYYY-MM-DD HH:MM
            try:
                bars.append({
                    "date": dt_str,
                    "open": round(float(row["open"]), 4),
                    "close": round(float(row["close"]), 4),
                    "high": round(float(row["high"]), 4),
                    "low": round(float(row["low"]), 4),
                    "volume": int(float(row["volume"])),
                })
            except (ValueError, TypeError):
                continue
        return {"bars": bars} if bars else None
    except Exception:
        return None


def _fetch_us_minute_kline(ticker: str, m_param: str, count: int) -> Optional[dict[str, Any]]:
    """美股分钟级K线（yfinance，国内直连）。

    m_param: m5/m15/m30/m60
    """
    try:
        import yfinance as yf
        interval_map = {"m5": "5m", "m15": "15m", "m30": "30m", "m60": "60m"}
        interval = interval_map.get(m_param, "5m")
        period = "5d" if count <= 390 else "60d"
        df = yf.download(ticker, period=period, interval=interval, progress=False)
        if df is None or len(df) == 0:
            return None
        # yfinance返回MultiIndex列名，扁平化
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        bars = []
        for idx, row in df.tail(count).iterrows():
            dt_str = str(idx)[:16]
            try:
                bars.append({
                    "date": dt_str,
                    "open": round(float(row["Open"]), 4),
                    "close": round(float(row["Close"]), 4),
                    "high": round(float(row["High"]), 4),
                    "low": round(float(row["Low"]), 4),
                    "volume": int(float(row["Volume"])),
                })
            except (ValueError, TypeError):
                continue
        return {"bars": bars} if bars else None
    except Exception:
        return None


def get_history(symbol: str, days: int = 250) -> Optional[pd.DataFrame]:
    """前复权日线行情（腾讯 K 线接口），缓存 1 小时。"""
    sym = _norm_symbol(symbol)

    def _fetch() -> Optional[dict[str, Any]]:
        # 美股：腾讯接口日K只返回最近2条，改用新浪美股日K（1984年至今完整历史）
        if sym.startswith("us"):
            return _fetch_us_kline(sym, days)
        code = f"{_market_prefix(sym)}{sym}"
        url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,,,{days},qfq"
        try:
            r = requests.get(url, timeout=15)
            data = r.json()
            node = data["data"][code]
            key = "qfqday" if "qfqday" in node else "day"
            rows = node[key]
            # 部分行可能多出第7个字段（成交额等），只取前6列
            bars = []
            for row in rows:
                try:
                    bars.append({
                        "date": str(row[0]),
                        "open": float(row[1]),
                        "close": float(row[2]),
                        "high": float(row[3]),
                        "low": float(row[4]),
                        "volume": float(row[5]),
                    })
                except (ValueError, IndexError):
                    continue
            return {"bars": bars}
        except Exception:
            return None

    data = cached(f"kline:{sym}:{days}", TTL["kline"], _fetch)
    if data is None or not data.get("bars"):
        return None
    df = pd.DataFrame(data["bars"])
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    df["ma5"] = df["close"].rolling(5).mean()
    df["ma20"] = df["close"].rolling(20).mean()
    df["ma60"] = df["close"].rolling(60).mean()
    return df


# ==================== 多周期K线 ====================

# 支持的周期: 日/周/月 用fqkline接口, 分钟级用mkline接口
PERIOD_MAP = {
    "day": {"type": "fqkline", "param": "day"},
    "week": {"type": "fqkline", "param": "week"},
    "month": {"type": "fqkline", "param": "month"},
    "5min": {"type": "mkline", "param": "m5"},
    "15min": {"type": "mkline", "param": "m15"},
    "30min": {"type": "mkline", "param": "m30"},
    "60min": {"type": "mkline", "param": "m60"},
}


def get_history_multi(symbol: str, period: str = "day", count: int = 250) -> Optional[pd.DataFrame]:
    """多周期K线数据。

    period: day/week/month/5min/15min/30min/60min
    count: 返回的K线数量

    数据源:
    - A股日K/周K/月K: 腾讯fqkline
    - A股分钟级: AKShare腾讯源(stock_zh_a_minute)
    - 美股日K: AKShare新浪源(stock_us_daily)
    - 美股周K/月K: 新浪日K聚合
    - 美股分钟级: yfinance
    """
    sym = _norm_symbol(symbol)
    period_info = PERIOD_MAP.get(period)
    if period_info is None:
        period_info = PERIOD_MAP["day"]

    cache_key = f"kline:{sym}:{period}:{count}"

    def _fetch() -> Optional[dict[str, Any]]:
        code = f"{_market_prefix(sym)}{sym}"
        try:
            # ===== 美股 =====
            if sym.startswith("us"):
                ticker = sym[2:]
                if period_info["type"] == "fqkline" and period_info["param"] != "day":
                    return _fetch_us_kline_aggregated(sym, period_info["param"], count)
                if period_info["type"] == "fqkline" and period_info["param"] == "day":
                    return _fetch_us_kline(sym, count)
                if period_info["type"] == "mkline":
                    return _fetch_us_minute_kline(ticker, period_info["param"], count)

            # ===== A股分钟级: AKShare腾讯源 =====
            if period_info["type"] == "mkline":
                return _fetch_a_share_minute_akshare(sym, period_info["param"], count)

            # ===== A股日K/周K/月K: 腾讯fqkline =====
            if period_info["type"] == "fqkline":
                # 日/周/月K线
                url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},{period_info['param']},,,{count},qfq"
                r = requests.get(url, timeout=15)
                data = r.json()
                node = data["data"][code]
                key = f"qfq{period_info['param']}"
                rows = node.get(key, node.get(period_info["param"], []))
                if isinstance(rows, dict):
                    rows = rows.get("data", [])
            else:
                # 分钟级K线
                url = f"https://ifzq.gtimg.cn/appstock/app/kline/mkline?param={code},{period_info['param']},,{count}"
                r = requests.get(url, timeout=15)
                data = r.json()
                node = data["data"][code]
                rows = node.get(period_info["param"], {})
                if isinstance(rows, dict):
                    rows = rows.get("data", [])

            bars = []
            for row in rows:
                try:
                    if period_info["type"] == "fqkline":
                        # 格式: ['2026-03-11', open, close, high, low, volume]
                        bars.append({
                            "date": str(row[0]),
                            "open": float(row[1]),
                            "close": float(row[2]),
                            "high": float(row[3]),
                            "low": float(row[4]),
                            "volume": float(row[5]),
                        })
                    else:
                        # 分钟格式: ['202607271445', open, close, high, low, volume, ...]
                        raw_date = str(row[0])
                        if len(raw_date) == 12:
                            # YYYYMMDDHHMM -> 格式化
                            dt_str = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]} {raw_date[8:10]}:{raw_date[10:12]}"
                        else:
                            dt_str = raw_date
                        bars.append({
                            "date": dt_str,
                            "open": float(row[1]),
                            "close": float(row[2]),
                            "high": float(row[3]),
                            "low": float(row[4]),
                            "volume": float(row[5]),
                        })
                except (ValueError, IndexError):
                    continue
            return {"bars": bars}
        except Exception:
            return None

    data = cached(cache_key, TTL["kline"], _fetch)
    if data is None or not data.get("bars"):
        return None
    df = pd.DataFrame(data["bars"])
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    df["ma5"] = df["close"].rolling(5).mean()
    df["ma20"] = df["close"].rolling(20).mean()
    df["ma60"] = df["close"].rolling(60).mean()
    return df


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


def get_financials(symbol: str) -> Optional[dict[str, Any]]:
    """财务摘要：A股用同花顺接口，港股/美股用 yfinance。缓存 24 小时。"""
    sym = _norm_symbol(symbol)

    # 港股/美股：用 yfinance
    if sym.startswith("hk") or sym.startswith("us"):
        def _fetch_yf() -> Optional[dict[str, Any]]:
            try:
                import yfinance as yf
                # 转换代码：hk00700 -> 0700.HK，usAAPL -> AAPL
                if sym.startswith("hk"):
                    yf_sym = sym[2:] + ".HK"
                else:
                    yf_sym = sym[2:]
                info = yf.Ticker(yf_sym).info
                if not info:
                    return None
                return {
                    "period": "最新报告期",
                    "revenue": info.get("totalRevenue"),
                    "revenue_yoy": None,
                    "net_profit": info.get("netIncomeToCommon"),
                    "net_profit_yoy": None,
                    "roe": round(info.get("returnOnEquity", 0) * 100, 2) if info.get("returnOnEquity") else None,
                    "gross_margin": round(info.get("grossMargins", 0) * 100, 2) if info.get("grossMargins") else None,
                    "debt_ratio": info.get("debtToEquity"),
                }
            except Exception:
                return None
        return cached(f"financials:{sym}", TTL["financials"], _fetch_yf)

    # A股：同花顺接口
    if not AK_AVAILABLE:
        return None

    def _fetch() -> Optional[dict[str, Any]]:
        df = _safe(ak.stock_financial_abstract_ths, symbol=sym, indicator="按报告期")
        if df is None or df.empty:
            return None
        recent = df.iloc[-1]
        out: dict[str, Any] = {"period": str(recent.get("报告期", ""))}
        for key, col in [
            ("revenue", "营业总收入"),
            ("revenue_yoy", "营业总收入同比增长率"),
            ("net_profit", "净利润"),
            ("net_profit_yoy", "净利润同比增长率"),
            ("roe", "净资产收益率"),
            ("gross_margin", "销售毛利率"),
            ("debt_ratio", "资产负债率"),
        ]:
            out[key] = _parse_num(recent.get(col))
        return out

    return cached(f"financials:{sym}", TTL["financials"], _fetch)


def get_lhb(symbol: str, days: int = 30) -> Optional[dict[str, Any]]:
    """最近龙虎榜记录，缓存 6 小时。"""
    if not AK_AVAILABLE:
        return None
    sym = _norm_symbol(symbol)

    def _fetch() -> Optional[dict[str, Any]]:
        end = datetime.now()
        start = end - timedelta(days=days)
        df = _safe(
            ak.stock_lhb_detail_em,
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
        )
        if df is None or df.empty:
            return None
        rows = df[df["代码"].astype(str).str.zfill(6) == sym]
        if rows.empty:
            return None
        recent = rows.sort_values("上榜日", ascending=False).iloc[0]
        return {
            "date": str(recent.get("上榜日", "")),
            "reason": str(recent.get("上榜原因", "")),
            "net_buy": _to_float(recent.get("龙虎榜净买额")),
            "buy_total": _to_float(recent.get("龙虎榜买入额")),
            "sell_total": _to_float(recent.get("龙虎榜卖出额")),
        }

    return cached(f"lhb:{sym}", TTL["lhb"], _fetch)


def get_flash_news(keyword: str = "", limit: int = 10) -> Optional[list[dict[str, str]]]:
    """实时财经快讯（新浪 7x24 全球财经直播），缓存 60 秒。

    keyword 非空时按关键词过滤（如个股名称/代码）；否则返回最新快讯。
    """
    def _fetch() -> Optional[list[dict[str, str]]]:
        url = (
            "https://zhibo.sina.com.cn/api/zhibo/feed?"
            "page=1&page_size=30&zhibo_id=152&tag_id=0&dire=f&dpc=1"
        )
        try:
            r = requests.get(url, timeout=12, headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://finance.sina.com.cn",
            })
            d = r.json()
            items = d.get("result", {}).get("data", {}).get("feed", {}).get("list", [])
            out = []
            for it in items:
                text = (it.get("rich_text") or "").strip()
                if not text:
                    continue
                out.append({
                    "title": text,
                    "time": (it.get("create_time") or "")[:16],
                })
            return out or None
        except Exception:
            return None

    data = cached(f"flash:{keyword or 'all'}", 60, _fetch)
    if data is None:
        return None
    if keyword:
        kw = keyword.lower()
        hits = [n for n in data if kw in n["title"].lower() or kw in n["time"]]
        return hits[:limit] or None
    return data[:limit]


# 行业同行映射（A股核心股票）
INDUSTRY_PEERS: dict[str, list[str]] = {
    "600519": ["000858", "000568", "002304", "603369", "600809"],  # 白酒：五粮液 泸州老窖 洋河 今世缘 山西汾酒
    "000858": ["600519", "000568", "002304", "603369", "600809"],
    "000001": ["600036", "601398", "601939", "601318", "600000"],  # 银行：招行 工行 建行 平安 浦发
    "600036": ["000001", "601398", "601939", "601318", "600000"],
    "300750": ["002594", "300014", "600089", "300274", "002460"],  # 新能源：比亚迪 亿纬锂能 特变电工 阳光电源 京东方
    "002594": ["300750", "601238", "600104", "601633", "000625"],  # 汽车：长安 上汽 长城 长安
    "600036": ["000001", "601398", "601939", "601318", "600000"],
    "601318": ["000001", "600036", "601398", "601628", "601601"],  # 保险：人寿 太保
}


def get_industry_compare(symbol: str) -> Optional[dict[str, Any]]:
    """行业对比：从数据库读取同行列表，拉取实时 PE/PB + 行业均值。"""
    sym = _norm_symbol(symbol)

    # 从数据库获取同行列表（没有则用 LLM 自动生成）
    try:
        from ..chat import get_peers, auto_generate_peers
        peers = get_peers(sym)
        if not peers:
            # 数据库没有，自动生成并缓存
            peers = auto_generate_peers(sym)
    except Exception:
        peers = None
    if not peers:
        return None

    def _fetch() -> Optional[dict[str, Any]]:
        items = []
        all_codes = [sym] + peers
        for code in all_codes:
            brief = get_stock_brief(code)
            if brief and brief.get("pe"):
                items.append({
                    "code": code,
                    "name": brief.get("name", code),
                    "pe": brief.get("pe"),
                    "pb": brief.get("pb"),
                    "change_pct": brief.get("change_pct"),
                    "market_cap": brief.get("market_cap"),
                    "is_target": code == sym,
                })
        if len(items) < 2:
            return None
        pes = [i["pe"] for i in items if i["pe"] and i["pe"] > 0]
        pbs = [i["pb"] for i in items if i["pb"] and i["pb"] > 0]
        return {
            "peers": items,
            "avg_pe": round(sum(pes) / len(pes), 2) if pes else None,
            "avg_pb": round(sum(pbs) / len(pbs), 2) if pbs else None,
        }

    return cached(f"industry:{sym}", TTL["quote"], _fetch)


def get_hot_stocks() -> list[dict[str, Any]]:
    """每日热门股票：A股+港股+美股候选池拉通按涨幅排序取前6。"""
    all_pool = [
        # A股
        "600519", "601398", "300750", "600036", "000858",
        "601318", "000001", "600276", "601012", "002594",
        "600900", "000333", "601899", "600030", "002475",
        # 港股
        "hk00700", "hk09988", "hk01810", "hk03690",
        # 美股
        "usAAPL", "usTSLA", "usNVDA", "usMSFT",
    ]

    def _fetch() -> list[dict[str, Any]]:
        quotes = []
        for code in all_pool:
            brief = get_stock_brief(code)
            if brief and brief.get("price"):
                quotes.append({
                    "code": code,
                    "name": brief["name"],
                    "change_pct": brief.get("change_pct", 0),
                })
        # 全市场拉通按涨幅排序取前6
        quotes.sort(key=lambda x: x.get("change_pct", 0), reverse=True)
        return quotes[:6]

    return cached("hot_stocks", 3600, _fetch)  # 缓存1小时


def get_news(symbol: str) -> Optional[list[dict[str, str]]]:
    """个股新闻：新浪快讯按名称/代码过滤 + 东方财富个股新闻兜底，缓存 15 分钟。"""
    sym = _norm_symbol(symbol)
    brief = get_stock_brief(sym)
    name = brief.get("name", "") if brief else ""
    items: list[dict[str, str]] = []

    # 1) 实时快讯过滤（三市场通用）
    if name:
        # 名称可能带后缀（控股/集团/股份），截断核心名提高命中（腾讯控股 -> 腾讯）
        short = name
        for suffix in ("控股", "集团", "股份有限公司", "有限公司", "股份", "科技"):
            if short.endswith(suffix) and len(short) - len(suffix) >= 2:
                short = short[: -len(suffix)]
                break
        for kw in dict.fromkeys([name, short]):
            flash = get_flash_news(keyword=kw, limit=4)
            if flash:
                items.extend(flash)
        # 代码过滤（如 600519）
        code_hits = get_flash_news(keyword=sym.replace("hk", "").replace("us", ""), limit=3)
        if code_hits:
            items.extend(code_hits)

    # 2) 东方财富搜索API：按股票名称搜索，返回真正的个股新闻（比akshare按代码搜索质量高）
    if name and len(items) < 8:
        # 相关性匹配关键词：股票全名 + 核心简称（至少2字）
        name_keywords = {name.lower()}
        for suffix in ("控股", "集团", "股份有限公司", "有限公司", "股份", "科技"):
            if name.endswith(suffix) and len(name) - len(suffix) >= 2:
                name_keywords.add(name[:-len(suffix)].lower())
                break
        # 额外常见简称
        if len(name) >= 4:
            name_keywords.add(name[:2].lower())

        def _fetch() -> Optional[list[dict[str, str]]]:
            try:
                import urllib.parse
                param = json.dumps({
                    "uid": "", "keyword": name, "type": ["cmsArticleWebOld"],
                    "client": "web", "clientType": "web", "clientVersion": "curr",
                    "param": {"cmsArticleWebOld": {"searchScope": "default", "sort": "default",
                               "pageIndex": 1, "pageSize": 15, "preTag": "", "postTag": ""}}
                }, ensure_ascii=False)
                url = f"https://search-api-web.eastmoney.com/search/jsonp?cb=jQuery&param={urllib.parse.quote(param)}"
                r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
                m = re.search(r"jQuery\((.+)\)", r.text, re.S)
                if not m:
                    return None
                d = json.loads(m.group(1))
                arts = d.get("result", {}).get("cmsArticleWebOld", [])
                out = []
                for a in arts:
                    title = a.get("title", "").replace("<em>", "").replace("</em>", "")
                    # 必须标题前30字符内包含股票名称核心词（排除正文碰巧提到的不相关新闻）
                    title_head = title[:30].lower()
                    if not any(kw in title_head for kw in name_keywords):
                        continue
                    out.append({"title": title, "time": (a.get("date", "") or "")[:16]})
                    if len(out) >= 8:
                        break
                return out or None
            except Exception:
                return None

        extra = cached(f"news:{sym}", TTL["news"], _fetch)
        if extra:
            items.extend(extra)

    # 去重（按标题）
    seen, uniq = set(), []
    for n in items:
        if n["title"] not in seen:
            seen.add(n["title"])
            uniq.append(n)
    return uniq[:8] or None


def get_minute_kline(symbol: str) -> Optional[dict[str, Any]]:
    """当日分时数据（腾讯分钟接口），缓存 30 秒。

    返回 {points: [[时间, 价, 均价, 量], ...], last_close: 昨收}
    """
    sym = _norm_symbol(symbol)
    code = f"{_market_prefix(sym)}{sym}"

    def _fetch() -> Optional[dict[str, Any]]:
        try:
            url = f"https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={code}"
            r = requests.get(url, timeout=12)
            d = r.json()
            node = d.get("data", {}).get(code, {})
            points = node.get("data", {}).get("data", [])
            qt = node.get("qt", {})
            last_close = None
            # 昨收在 qt[code] 数组 index 4（腾讯行情字段：0未知 1名字 2代码 3现价 4昨收）
            qt_arr = qt.get(code, []) if isinstance(qt, dict) else []
            if isinstance(qt_arr, list) and len(qt_arr) > 4:
                try:
                    last_close = float(qt_arr[4])
                except (ValueError, TypeError):
                    pass
            out = []
            for p in points[:500]:
                try:
                    # 腾讯分时格式: "0930 1350.06 235 31726410.00"（空格分隔字符串）
                    # 或旧格式: ["0930", "1358.00", "1358.50", "12345"]
                    parts = p.split() if isinstance(p, str) else p
                    t = str(parts[0])
                    if not (t.isdigit() and len(t) == 4):
                        continue
                    price = float(parts[1])
                    if price <= 0:
                        continue
                    vol = float(parts[2]) if len(parts) > 2 and parts[2] else 0    # 成交量(手)
                    amt = float(parts[3]) if len(parts) > 3 and parts[3] else 0     # 成交额
                    out.append({
                        "time": t,
                        "price": price,
                        "volume": vol if vol else None,
                        "amount": amt if amt else None,
                    })
                except (ValueError, IndexError, TypeError, AttributeError):
                    continue
            # 点数太少视为无效（盘前/异常），但美股/港股盘后可能只有1-2条
            if len(out) < 1:
                return None
            # 判断市场：A股成交量单位=手(需*100转股)，港股/美股=股数(直接用)
            is_a_share = not (sym.startswith("hk") or sym.startswith("us"))
            vol_factor = 100 if is_a_share else 1
            # 计算分时均价（累计成交额 / (累计成交量 * vol_factor)）
            cum_amt = 0.0
            cum_vol = 0.0
            for pt in out:
                amt = pt.pop("amount", 0) or 0
                vol = pt.get("volume") or 0
                cum_amt += amt
                cum_vol += vol
                pt["avg"] = round(cum_amt / (cum_vol * vol_factor), 2) if cum_vol > 0 else None
            # 从 qt 数组提取数据日期
            data_date = ""
            is_today = True
            try:
                from datetime import datetime as _dt
                today_str = _dt.now().strftime("%Y%m%d")
                if isinstance(qt_arr, list) and len(qt_arr) > 30 and qt_arr[30]:
                    raw_date = str(qt_arr[30])[:8]
                    if raw_date.isdigit() and len(raw_date) == 8:
                        data_date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
                        is_today = (raw_date == today_str)
            except Exception:
                pass
            return {"points": out, "last_close": last_close, "data_date": data_date, "is_today": is_today}
        except Exception:
            return None

    return cached(f"minute:{sym}", 30, _fetch)


def search_stocks(q: str, limit: int = 8) -> Optional[list[dict[str, str]]]:
    """股票搜索（腾讯智能搜索：代码/名称/拼音），缓存 5 分钟。

    返回 [{market, code, name, type}]，只含股票（GP），排除指数/基金。
    美股代码规范化：us~aapl.oq -> usAAPL
    """
    if not q or not q.strip():
        return None

    # 腾讯搜索不支持 us/hk 前缀，去掉后再搜
    search_q = q.strip()
    if search_q.lower().startswith("us"):
        search_q = search_q[2:]
    elif search_q.lower().startswith("hk"):
        search_q = search_q[2:].lstrip("0") or search_q[2:]

    def _fetch() -> Optional[list[dict[str, str]]]:
        import json as _json
        import urllib.parse
        url = f"https://smartbox.gtimg.cn/s3/?v=2&q={urllib.parse.quote(search_q)}&t=all"
        try:
            r = requests.get(url, timeout=8)
            r.encoding = "gbk"
            body = r.text.split('"')[1] if '"' in r.text else ""
            items = []
            for part in body.split("^"):
                fields = part.split("~")
                if len(fields) < 5:
                    continue
                market, code, raw_name, _pn, typ = fields[0], fields[1], fields[2], fields[3], fields[4]
                if not (typ.startswith("GP") or typ == "GP"):
                    continue  # 只留股票
                # 名称是 \uXXXX 转义，解码
                try:
                    name = _json.loads(f'"{raw_name}"')
                except Exception:
                    name = raw_name
                if market == "us":
                    std = "us" + code.split(".")[0].upper()
                elif market in ("hk", "bj"):
                    std = market + code
                elif market in ("sh", "sz"):
                    std = code.zfill(6)
                else:
                    continue
                items.append({"market": market, "code": std, "name": name, "type": typ})
                if len(items) >= limit:
                    break
            return items or None
        except Exception:
            return None

    return cached(f"search:{q.strip()}", 300, _fetch)


def get_history_all(symbol: str) -> Optional[pd.DataFrame]:
    """全量历史日K（至上市以来），缓存 6 小时。

    - A股：akshare stock_zh_a_daily（新浪源，全量，2001年至今）
    - 港股：akshare stock_hk_daily（腾讯源，全量）
    - 美股：新浪日K（1984年至今，复用 get_history 的 us 分支）
    统一返回 [date, open, close, high, low, volume] + ma5/ma20/ma60 的 DataFrame。
    """
    sym = _norm_symbol(symbol)
    if sym.startswith("us"):
        return get_history(sym, days=5000)

    def _fetch() -> Optional[dict[str, Any]]:
        try:
            if sym.startswith("hk"):
                df = _safe(ak.stock_hk_daily, symbol=sym[2:], adjust="qfq")
            else:
                df = _safe(ak.stock_zh_a_daily, symbol=f"{_market_prefix(sym)}{sym}", adjust="qfq")
            if df is None or df.empty:
                return None
            bars = []
            for _, row in df.iterrows():
                try:
                    bars.append({
                        "date": str(row["date"])[:10],
                        "open": float(row["open"]),
                        "close": float(row["close"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "volume": float(row.get("volume", 0) or 0),
                    })
                except (ValueError, TypeError, KeyError):
                    continue
            return {"bars": bars} if bars else None
        except Exception:
            return None

    data = cached(f"kline_all:{sym}", 6 * 3600, _fetch)
    if data is None or not data.get("bars"):
        return None
    df = pd.DataFrame(data["bars"])
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    df["ma5"] = df["close"].rolling(5).mean()
    df["ma20"] = df["close"].rolling(20).mean()
    df["ma60"] = df["close"].rolling(60).mean()
    return df


def _parse_num(v: Any) -> Optional[float]:
    """解析带单位数值：'54.27%'->54.27, '1.47亿'->147000000, False/None->None。"""
    if v is None or v is False:
        return None
    s = str(v).strip().replace("%", "")
    if s in ("", "--", "nan", "None", "False"):
        return None
    mult = 1.0
    if s.endswith("亿"):
        mult = 1e8
        s = s[:-1]
    elif s.endswith("万"):
        mult = 1e4
        s = s[:-1]
    try:
        f = float(s) * mult
        return f if f == f else None
    except ValueError:
        return None


def _to_float(v: Any) -> Optional[float]:
    try:
        f = float(v)
        return f if f == f else None  # NaN 过滤
    except (TypeError, ValueError):
        return None


# ==================== 情绪面数据（社交热度+资金流向） ====================


def get_social_sentiment(symbol: str) -> Optional[dict[str, Any]]:
    """社交情绪数据：东财人气排名 + 雪球关注度 + 今日主力资金净流入 + 排名变化趋势。

    仅支持A股（港股美股东财人气榜无数据）。
    返回 {
        hot_rank: int,          # 东财人气榜当前排名(越小越热)
        hot_rank_trend: list,   # 最近20个时间点的排名
        xq_followers: int,      # 雪球关注人数
        main_net_inflow: float, # 今日主力净流入(元)
        super_large_net: float, # 超大单净流入(元)
        large_net: float,       # 大单净流入
        medium_net: float,      # 中单净流入
        small_net: float,       # 小单净流入
        sentiment_score: float, # 综合情绪评分(-100到100)
    }
    """
    sym = _norm_symbol(symbol)
    if sym.startswith(("hk", "us")):
        return None  # 港股美股无东财人气榜

    result: dict[str, Any] = {}

    # 1) 东财人气榜排名趋势（最近20个10分钟采样点，取最新排名）
    def _fetch_rank_trend() -> Optional[list[dict]]:
        try:
            import os as _os
            saved = {}
            for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
                if k in _os.environ:
                    saved[k] = _os.environ.pop(k)
            try:
                code = f"{'SH' if sym[0] in '69' else 'SZ'}{sym}"
                df = ak.stock_hot_rank_detail_realtime_em(symbol=code)
            finally:
                _os.environ.update(saved)
            if df is None or df.empty:
                return None
            recent = df.tail(20)
            return [
                {"time": str(r["时间"]), "rank": int(r["排名"])}
                for _, r in recent.iterrows()
            ]
        except Exception:
            return None

    result["hot_rank_trend"] = cached(f"ranktrend:{sym}", 300, _fetch_rank_trend)

    # 2) 雪球关注度
    def _fetch_xq() -> Optional[int]:
        try:
            import os as _os
            saved = {}
            for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
                if k in _os.environ:
                    saved[k] = _os.environ.pop(k)
            try:
                df = ak.stock_hot_follow_xq(symbol="最热门")
            finally:
                _os.environ.update(saved)
            if df is None or df.empty:
                return None
            code = f"{'SH' if sym[0] in '69' else 'SZ'}{sym}"
            row = df[df["股票代码"] == code]
            if not row.empty:
                return int(float(row.iloc[0].get("关注", 0)))
            return None
        except Exception:
            return None

    result["xq_followers"] = cached(f"xqfollow:{sym}", 600, _fetch_xq)

    # 3) 资金动能（从K线量价计算，不依赖被封的push2 API）
    #    放量上涨=资金流入，缩量下跌=资金流出
    def _fetch_fund_momentum() -> Optional[dict]:
        try:
            hist = get_history(sym, days=10)
            if hist is None or len(hist) < 2:
                return None
            recent = hist.tail(5)
            prev = hist.tail(20).head(10) if len(hist) >= 20 else hist.head(len(hist)//2)
            # 最近5日平均量 vs 前期平均量
            recent_avg_vol = recent["volume"].mean()
            prev_avg_vol = prev["volume"].mean() if len(prev) > 0 else recent_avg_vol
            vol_ratio = recent_avg_vol / prev_avg_vol if prev_avg_vol > 0 else 1.0
            # 涨跌幅
            price_change = (recent.iloc[-1]["close"] - recent.iloc[0]["open"]) / recent.iloc[0]["open"] * 100
            # 量价方向：放量上涨=主力流入，缩量下跌=主力流出
            # 动能值 = 量比 * 涨跌方向
            momentum = (vol_ratio - 1) * (1 if price_change > 0 else -1) * 100
            return {
                "vol_ratio": round(vol_ratio, 2),
                "price_5d_chg": round(price_change, 2),
                "momentum": round(momentum, 1),
            }
        except Exception:
            return None

    fund = cached(f"fundmomentum:{sym}", 300, _fetch_fund_momentum)
    if fund:
        result["vol_ratio"] = fund["vol_ratio"]
        result["price_5d_chg"] = fund["price_5d_chg"]
        result["momentum"] = fund["momentum"]
    else:
        result["vol_ratio"] = None
        result["price_5d_chg"] = None
        result["momentum"] = None

    # 4) 综合情绪评分（-100到100）
    score = 0.0
    # 资金动能（量价关系）：momentum正值=放量上涨，负值=缩量下跌
    momentum = result.get("momentum")
    if momentum is not None:
        score += max(-40, min(40, momentum))
    # 人气排名：前50名加分（用趋势中最新排名）
    trend = result.get("hot_rank_trend")
    latest_rank = trend[-1]["rank"] if trend else result.get("hot_rank")
    if latest_rank and latest_rank > 0:
        if latest_rank <= 10:
            score += 30
        elif latest_rank <= 50:
            score += 15
        elif latest_rank <= 200:
            score += 5
        else:
            score -= 5
    # 雪球关注度：高关注+5
    if result.get("xq_followers") and result["xq_followers"] > 500000:
        score += 5

    result["sentiment_score"] = round(max(-100, min(100, score)), 1)
    return result if any(v is not None for v in result.values()) else None
