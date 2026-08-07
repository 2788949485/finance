"""路由模块: market"""
from __future__ import annotations
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..auth import get_profile
from ..config import get_config, save_config, PROVIDER_PRESETS
from ..deps import get_current_user, require_admin

router = APIRouter()

from ..data import fetcher as datalayer
from ..cache import cached, TTL
from .. import valuation
from ..config import get_config
import json


def _num(v) -> float:
    """安全转float，None/NaN→0。"""
    try:
        f = float(v)
        import math
        return 0 if math.isnan(f) else f
    except (ValueError, TypeError):
        return 0


@router.get("/api/quote/{symbol}")
def quote(symbol: str, days: int = 120, mode: str = "day", fresh: int = 0, all: int = 0) -> dict[str, Any]:
    """行情接口：brief(实时概览) + kline(日K/分时/全量) + tech(技术指标)。

    - mode=day 日K线；mode=minute 当日分时
    - all=1 全量历史K线（至上市以来，A股/港股 akshare 新浪/腾讯源，美股新浪）
    - fresh=1 绕过行情缓存（实时刷新最新价，配合前端轮询）
    """
    sym = datalayer._norm_symbol(symbol)
    brief = datalayer.get_stock_brief(sym, fresh=bool(fresh))
    if not brief:
        raise HTTPException(404, f"未查询到 {symbol} 行情")

    out: dict[str, Any] = {"brief": brief, "kline": [], "tech": {}}
    if mode == "minute":
        m = datalayer.get_minute_kline(sym)
        if m:
            out["kline"] = m["points"]
            out["last_close"] = m["last_close"]
            out["data_date"] = m.get("data_date", "")
            out["is_today"] = m.get("is_today", True)
    elif all:
        hist = datalayer.get_history_all(sym)
        bars: list[dict[str, Any]] = []
        if hist is not None and not hist.empty:
            for _, row in hist.iterrows():
                bars.append({
                    "date": str(row["date"].date()),
                    "open": _num(row["open"]),
                    "close": _num(row["close"]),
                    "high": _num(row["high"]),
                    "low": _num(row["low"]),
                    "volume": _num(row["volume"]),
                })
            out["tech"] = datalayer.compute_tech_signals(hist) or {}
        out["kline"] = bars
    else:
        days = min(max(days, 30), 500)
        hist = datalayer.get_history(sym, days=days)
        bars = []
        if hist is not None and not hist.empty:
            for _, row in hist.tail(days).iterrows():
                bars.append({
                    "date": str(row["date"].date()),
                    "open": _num(row["open"]),
                    "close": _num(row["close"]),
                    "high": _num(row["high"]),
                    "low": _num(row["low"]),
                    "volume": _num(row["volume"]),
                })
            out["tech"] = datalayer.compute_tech_signals(hist) or {}
        out["kline"] = bars
    return out



@router.get("/api/search/{q}")
def search(q: str) -> dict[str, Any]:
    """股票搜索（代码/名称/拼音，A股/港股/美股）。"""
    cache_key = f"search:{q}"
    result = cached(cache_key, 3600, lambda: {"query": q, "results": datalayer.search_stocks(q, limit=8) or []})
    return result



@router.get("/api/news/{symbol}")
def news(symbol: str) -> dict[str, Any]:
    """个股新闻（实时快讯过滤 + 东财兜底）。"""
    sym = datalayer._norm_symbol(symbol)
    cache_key = f"news:{sym}"
    result = cached(cache_key, TTL["news"], lambda: {"symbol": sym, "news": datalayer.get_news(sym) or []})
    return result



@router.get("/api/hot")
def hot_stocks() -> list[dict[str, Any]]:
    """每日热门股票（涨幅排序，动态变化）。"""
    cache_key = "hot_stocks"
    result = cached(cache_key, 300, lambda: datalayer.get_hot_stocks())
    return result or []



@router.get("/api/industry/{symbol}")
def industry_compare(symbol: str) -> dict[str, Any]:
    """行业对比：同行 PE/PB 均值。"""
    sym = datalayer._norm_symbol(symbol)
    cache_key = f"industry:{sym}"
    result = cached(cache_key, TTL["financials"], lambda: datalayer.get_industry_compare(sym) or {"peers": [], "avg_pe": None, "avg_pb": None})
    return result



@router.get("/api/sentiment/{symbol}")
def sentiment_data(symbol: str) -> dict[str, Any]:
    """社交情绪面数据：东财人气榜+雪球关注+主力资金流+情绪评分。"""
    sym = datalayer._norm_symbol(symbol)
    cache_key = f"sentiment:{sym}"
    result = cached(cache_key, 900, lambda: datalayer.get_social_sentiment(sym) or {"error": "暂无情绪数据"})
    return result



@router.get("/api/dcf/{symbol}")
def dcf_valuation(symbol: str) -> dict[str, Any]:
    """DCF现金流折现估值。"""
    sym = datalayer._norm_symbol(symbol)
    cache_key = f"dcf:{sym}"
    result = cached(cache_key, TTL["financials"], lambda: valuation.compute_dcf(sym) or {"error": "无法计算估值（财务数据不足）"})
    return result


# ---------- 投资组合 ----------


@router.get("/api/multi-period/{symbol}")
def multi_period_api(symbol: str) -> dict[str, Any]:
    """多周期共振分析：日线/周线/月线趋势是否一致。"""
    from .multi_period import get_multi_period_analysis
    result = get_multi_period_analysis(symbol)
    return result or {"error": "数据不足（需要至少60个交易日）"}



@router.get("/api/kline/{symbol}")
def kline_multi_period_api(symbol: str, period: str = "day", count: int = 250) -> dict[str, Any]:
    """多周期K线数据。period: day/week/month/5min/15min/30min/60min
    分钟级默认显示最近5个交易日，日K/周K/月K显示全部。
    """
    sym = datalayer._norm_symbol(symbol)
    # 分钟级默认只取最近5个交易日的数据
    if period.endswith("min"):
        bars_per_day = {"5min": 48, "15min": 16, "30min": 8, "60min": 4}
        count = min(count, bars_per_day.get(period, 48) * 5)  # 最近5个交易日
    df = datalayer.get_history_multi(sym, period=period, count=count)
    if df is None or len(df) == 0:
        return {"error": "数据不足"}
    bars = []
    for _, row in df.iterrows():
        bars.append({
            "date": row["date"].strftime("%Y-%m-%d %H:%M") if period.endswith("min") else row["date"].strftime("%Y-%m-%d"),
            "open": round(float(row["open"]), 4),
            "close": round(float(row["close"]), 4),
            "high": round(float(row["high"]), 4),
            "low": round(float(row["low"]), 4),
            "volume": int(row["volume"]),
        })
    # 技术指标
    tech = datalayer.compute_tech_signals(df)
    return {"symbol": sym, "period": period, "bars": bars, "tech": tech}



@router.get("/api/fund-flow/{symbol}")
def fund_flow_api(symbol: str, days: int = 10) -> dict[str, Any]:
    """个股资金流向：主力/超大单/大单净流入。"""
    from .fund_flow import get_fund_flow
    sym = datalayer._norm_symbol(symbol)
    result = get_fund_flow(sym, days=days)
    return result or {"error": "资金流向数据获取失败（可能为港股美股或东财接口超时）"}



@router.get("/api/patterns/{symbol}")
def patterns_api(symbol: str) -> dict[str, Any]:
    """K线形态自动识别。"""
    from .patterns import get_pattern_summary
    sym = datalayer._norm_symbol(symbol)
    df = datalayer.get_history(sym, days=30)
    if df is None or len(df) < 3:
        return {"error": "数据不足"}
    result = get_pattern_summary(df)
    return result or {"pattern": None, "description": "近期无明显K线形态"}



@router.get("/api/em-proxy")
def em_proxy(url: str) -> Any:
    """东财接口代理（解决前端CORS跨域问题）"""
    from curl_cffi import requests as cffi_req
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://quote.eastmoney.com/",
    }
    try:
        r = cffi_req.get(url, impersonate="chrome", timeout=10, headers=headers)
        return r.json()
    except Exception:
        return {"error": "proxy failed"}


