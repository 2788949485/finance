"""Polygon.io 美股数据源。

免费套餐支持：
- 日K线: /v2/aggs/ticker/{sym}/range/1/day
- 5分钟K线: /v2/aggs/ticker/{sym}/range/5/minute
- 昨收: /v2/aggs/ticker/{sym}/prev

不支持（403）：
- 1分钟K线、snapshot实时行情、当日open-close

实时价格用腾讯接口兜底。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

_POLYGON_KEY = "5hRpdcu0rHVp42h6iMks1qQwqmnF4kNW"
_BJ_TZ = timezone(timedelta(hours=8))

# 纳斯达克常见股票（secid参考，Polygon不需要但保留映射）
NASDAQ_TICKERS = {
    "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "TSLA", "NVDA", "META", "NFLX",
    "AMD", "INTC", "CSCO", "ADBE", "PEP", "COST", "AVGO", "TXN", "QCOM",
    "TMUS", "CMCSA", "SBUX", "PYPL",
}


def polygon_get_brief(symbol: str) -> Optional[dict[str, Any]]:
    """美股实时行情（Polygon prev + 腾讯实时价格）。

    返回与 get_stock_brief 相同的格式。
    """
    ticker = symbol.replace("us", "").upper()

    # 1. Polygon prev 获取昨收 + 昨日OHLCV
    prev_data = _polygon_prev(ticker)
    if not prev_data:
        return None

    # 2. 腾讯获取实时价格（Polygon snapshot 403）
    rt_price = _tencent_realtime_price(symbol)

    price = rt_price or prev_data.get("close", 0)
    prev_close = prev_data.get("close", 0)
    change = price - prev_close if prev_close else 0
    change_pct = (change / prev_close * 100) if prev_close else 0

    return {
        "name": _ticker_name(ticker),
        "code": symbol,
        "price": round(price, 2),
        "pre_close": round(prev_close, 2),
        "change": round(change, 2),
        "change_pct": round(change_pct, 2),
        "open": round(prev_data.get("open", 0), 2),
        "high": round(prev_data.get("high", 0), 2),
        "low": round(prev_data.get("low", 0), 2),
        "volume": int(prev_data.get("vol", 0)),
        "market": "us",
    }


def polygon_get_history(symbol: str, days: int = 250) -> Optional[list[dict[str, Any]]]:
    """美股日K线（Polygon aggregates）。"""
    ticker = symbol.replace("us", "").upper()
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=days * 2)).strftime("%Y-%m-%d")  # *2排除非交易日
    url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/{start}/{end}"
    params = {"apiKey": _POLYGON_KEY, "limit": days, "sort": "asc"}

    try:
        r = requests.get(url, params=params, timeout=15)
        if r.status_code != 200:
            logger.warning("Polygon daily %s: %s", ticker, r.status_code)
            return None
        results = r.json().get("results", [])
        bars = []
        for item in results:
            dt = datetime.fromtimestamp(item["t"] / 1000, tz=_BJ_TZ)
            bars.append({
                "date": dt.strftime("%Y-%m-%d"),
                "open": round(item["o"], 2),
                "close": round(item["c"], 2),
                "high": round(item["h"], 2),
                "low": round(item["l"], 2),
                "volume": int(item["v"]),
            })
        return bars if bars else None
    except Exception as e:
        logger.warning("Polygon daily %s error: %s", ticker, e)
        return None


def polygon_get_minute(symbol: str) -> Optional[dict[str, Any]]:
    """美股分时数据（Polygon 5分钟K线聚合为分时图）。

    返回与 _us_minute_from_em 相同格式: {points, last_close, data_date, is_today}
    时间已是北京时间（Polygon返回UTC，自动转BJ）。
    """
    ticker = symbol.replace("us", "").upper()

    # 获取最近2天的5分钟数据（确保覆盖完整交易日）
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
    url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/5/minute/{start}/{end}"
    params = {"apiKey": _POLYGON_KEY, "limit": 500, "sort": "asc"}

    try:
        r = requests.get(url, params=params, timeout=15)
        if r.status_code != 200:
            logger.warning("Polygon 5min %s: %s", ticker, r.status_code)
            return None
        results = r.json().get("results", [])
        if not results:
            return None

        # 找最新交易日的数据
        all_dates = set()
        for item in results:
            dt = datetime.fromtimestamp(item["t"] / 1000, tz=_BJ_TZ)
            all_dates.add(dt.strftime("%Y-%m-%d"))

        if not all_dates:
            return None

        # 取最新交易日
        latest_date = max(all_dates)
        day_points = []
        for item in results:
            dt = datetime.fromtimestamp(item["t"] / 1000, tz=_BJ_TZ)
            if dt.strftime("%Y-%m-%d") == latest_date:
                day_points.append({
                    "time": dt.strftime("%H%M"),
                    "price": round(item["c"], 2),
                    "avg": round(item.get("vw", item["c"]), 2),
                    "vol": int(item["v"]),
                })

        if not day_points:
            return None

        # 昨收
        prev_data = _polygon_prev(ticker)
        last_close = prev_data.get("close") if prev_data else None

        return {
            "points": day_points,
            "last_close": round(last_close, 2) if last_close else None,
            "data_date": latest_date,
            "is_today": latest_date == datetime.now(_BJ_TZ).strftime("%Y-%m-%d"),
        }
    except Exception as e:
        logger.warning("Polygon 5min %s error: %s", ticker, e)
        return None


def _polygon_prev(ticker: str) -> Optional[dict[str, Any]]:
    """Polygon prev 接口获取前一交易日数据。"""
    try:
        url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/prev"
        r = requests.get(url, params={"apiKey": _POLYGON_KEY}, timeout=10)
        if r.status_code != 200:
            return None
        results = r.json().get("results", [])
        if not results:
            return None
        item = results[0]
        return {
            "open": item["o"],
            "close": item["c"],
            "high": item["h"],
            "low": item["l"],
            "vol": item["v"],
        }
    except Exception:
        return None


def _tencent_realtime_price(symbol: str) -> Optional[float]:
    """腾讯接口获取实时价格（Polygon snapshot不支持）。"""
    try:
        url = f"https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={symbol}"
        r = requests.get(url, timeout=8)
        d = r.json()
        node = d.get("data", {}).get(symbol, {})
        qt = node.get("qt", {})
        qt_arr = qt.get(symbol, []) if isinstance(qt, dict) else []
        if isinstance(qt_arr, list) and len(qt_arr) > 3:
            return float(qt_arr[3])
    except Exception:
        pass
    return None


_TICKER_NAMES = {
    "AAPL": "苹果", "MSFT": "微软", "GOOGL": "谷歌A", "GOOG": "谷歌C",
    "AMZN": "亚马逊", "TSLA": "特斯拉", "NVDA": "英伟达", "META": "Meta",
    "NFLX": "奈飞", "AMD": "AMD", "INTC": "英特尔", "CSCO": "思科",
    "ADBE": "Adobe", "PEP": "百事", "COST": "好市多", "AVGO": "博通",
    "TXN": "德州仪器", "QCOM": "高通", "SBUX": "星巴克", "PYPL": "PayPal",
    "BABA": "阿里巴巴", "JD": "京东", "PDD": "拼多多", "BIDU": "百度",
    "NIO": "蔚来", "XPEV": "小鹏", "LI": "理想", "BILI": "哔哩哔哩",
}


def _ticker_name(ticker: str) -> str:
    return _TICKER_NAMES.get(ticker.upper(), ticker)
