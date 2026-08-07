"""A股行情数据（实时概览 / 日K线 / 多周期K线 / 分时 / 全量历史）。

从原 a_stock.py 拆分而来；同时承担 hk/us 符号的分发角色。
函数签名、行为、返回值均未改变。
"""
from __future__ import annotations

from typing import Any, Optional

import pandas as pd
import requests

from .utils import (
    TTL,
    _market_prefix,
    _norm_symbol,
    _safe,
    _to_float,
    ak,
    cached,
)
from .hk_us_stock import (
    _fetch_us_kline,
    _fetch_us_kline_aggregated,
    _fetch_us_minute_kline,
    _us_minute_from_em,
)


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
                # 盘口数据
                "pre_close": _to_float(p[4]),       # 昨收
                "open": _to_float(p[5]),            # 今开
                "high": _to_float(p[33]),           # 最高
                "low": _to_float(p[34]),            # 最低
                "volume": _to_float(p[36]),         # 成交量(手)
                "amount": _to_float(p[37]),         # 成交额(万元)
                "limit_up": _to_float(p[47]),       # 涨停价
                "limit_down": _to_float(p[48]),     # 跌停价
                "volume_ratio": _to_float(p[49]),   # 量比
            }
        except Exception:
            return None

    if fresh:
        return _fetch()  # fresh=True：直连腾讯，不读缓存
    return cached(f"quote:{sym}", TTL["quote"], _fetch)


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


def get_minute_kline(symbol: str) -> Optional[dict[str, Any]]:
    """当日分时数据。A股/港股用腾讯接口，美股用东财分时(curl_cffi)。

    返回 {points: [[时间, 价, 均价, 量], ...], last_close: 昨收}
    """
    sym = _norm_symbol(symbol)

    # 美股：用东财分时接口(curl_cffi绕过TLS封锁)
    if sym.startswith("us"):
        return _us_minute_from_em(sym)

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
