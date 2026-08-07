"""港股/美股数据获取（新浪美股日K / yfinance分钟级 / 东财分时）。

仅含美股相关底层抓取函数；A股入口在 a_stock.py 中按 sym.startswith("us") 分发到此处。
"""
from __future__ import annotations

import json as _json
from typing import Any, Optional

import pandas as pd
import requests


def _fetch_us_kline(sym: str, days: int) -> Optional[dict[str, Any]]:
    """美股日K线（新浪接口，1984年至今完整历史，国内直连）。

    返回 JSONP：var _=([{"d":"1984-09-07","o":"26.50","h":"26.87","l":"26.25","c":"26.50","v":...}, ...])
    """
    ticker = sym[2:]  # usAAPL -> AAPL
    url = (
        "https://stock.finance.sina.com.cn/usstock/api/jsonp_v2.php/"
        f"var%20_=/US_MinKService.getDailyK?symbol={ticker}"
    )
    try:
        r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"})
        text = r.text
        start, end = text.find("(["), text.rfind("]")
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


def _us_minute_from_em(symbol: str) -> Optional[dict[str, Any]]:
    """美股分时数据（东财trends2接口，curl_cffi绕过TLS封锁）。

    返回与A股分时相同格式: {points, last_close, data_date, is_today}
    """
    sym = symbol.replace("us", "")
    # secid: 105=纳斯达克, 106=纽交所
    # 常见纳斯达克: AAPL/MSFT/GOOGL/AMZN/TSLA/NVDA/META
    nasdaq = {"AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "TSLA", "NVDA", "META", "NFLX",
              "AMD", "INTC", "CSCO", "ADBE", "PEP", "COST", "AVGO", "TXN", "QCOM",
              "TMUS", "CMCSA", "SBUX", "PYPL"}
    market = "105" if sym.upper() in {s.upper() for s in nasdaq} else "106"
    secid = f"{market}.{sym}"

    try:
        from curl_cffi import requests as cffi_req
        url = "https://push2his.eastmoney.com/api/qt/stock/trends2/get"
        params = {
            "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
            "iscr": "0",
            "ndays": "1",
            "secid": secid,
        }
        r = cffi_req.get(url, params=params, impersonate="chrome", timeout=10)
        d = r.json()
        trends = d.get("data", {}).get("trends", [])
        if not trends:
            return None

        # 昨收
        pre_close = d.get("data", {}).get("preClose", 0) or 0

        points = []
        total_vol = 0
        total_amount = 0
        for item in trends:
            parts = item.split(",")
            if len(parts) < 7:
                continue
            dt_str = parts[0]  # "2026-08-05 21:30"
            price = float(parts[2])  # 收盘价
            vol = int(float(parts[5]))  # 成交量
            amount = float(parts[6])  # 成交额
            total_vol += vol
            total_amount += amount
            # 提取时间部分 "2130"
            time_part = dt_str.split(" ")[1].replace(":", "")[:4] if " " in dt_str else "0000"
            avg_price = total_amount / total_vol if total_vol > 0 else price
            points.append({"time": time_part, "price": round(price, 2), "avg": round(avg_price, 2), "vol": vol})

        if not points:
            return None

        # 取最后一个交易日期
        last_date = trends[-1].split(",")[0].split(" ")[0] if trends else ""

        return {
            "points": points,
            "last_close": round(pre_close, 2) if pre_close else None,
            "data_date": last_date,
            "is_today": True,
        }
    except Exception:
        return None
