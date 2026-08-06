"""融资融券数据：上交所+深交所合并明细 / 余额TOP。

数据源：
- ak.stock_margin_detail_sse(date)   上交所融资融券明细（可用）
- ak.stock_margin_detail_szse(date)   深交所融资融券明细（可用）
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any, Optional

# 国内数据源直连
_CN_DATA_DOMAINS = (
    "eastmoney.com,push2his.eastmoney.com,sse.com.cn,sseinfo.com,"
    "szse.cn,cninfo.com.cn,sina.com.cn,10jqka.com.cn,ths.cn"
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


def _safe_num(v: Any, ndigits: int = 2) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return round(f, ndigits)


def _normalize_date(date: str | None) -> str:
    """规范化日期为 YYYYMMDD；缺省取昨日。"""
    if not date:
        return (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
    s = str(date).strip().replace("-", "").replace("/", "")
    if len(s) == 8 and s.isdigit():
        return s
    raise ValueError(f"日期格式错误，应为 YYYYMMDD，收到：{date}")


def _fetch_margin_detail(date: str) -> Optional[list[dict]]:
    """拉取某日上交所+深交所融资融券明细，合并标准化。

    返回 [{code, name, margin_balance, margin_buy, margin_repay,
    short_volume, short_sell, short_repay, total_balance}]，单位元。
    """
    records: list[dict] = []

    # 上交所
    try:
        df_sse = ak.stock_margin_detail_sse(date=date)
    except Exception:
        df_sse = None
    if df_sse is not None and not df_sse.empty:
        for _, row in df_sse.iterrows():
            records.append({
                "exchange": "SSE",
                "code": str(row.get("标的证券代码", "")),
                "name": str(row.get("标的证券简称", "")),
                "margin_balance": _safe_num(row.get("融资余额"), 0),
                "margin_buy": _safe_num(row.get("融资买入额"), 0),
                "margin_repay": _safe_num(row.get("融资偿还额"), 0),
                "short_volume": _safe_num(row.get("融券余量"), 0),
                "short_sell": _safe_num(row.get("融券卖出量"), 0),
                "short_repay": _safe_num(row.get("融券偿还量"), 0),
            })

    # 深交所
    try:
        df_szse = ak.stock_margin_detail_szse(date=date)
    except Exception:
        df_szse = None
    if df_szse is not None and not df_szse.empty:
        for _, row in df_szse.iterrows():
            records.append({
                "exchange": "SZSE",
                "code": str(row.get("证券代码", "")),
                "name": str(row.get("证券简称", "")),
                "margin_balance": _safe_num(row.get("融资余额"), 0),
                "margin_buy": _safe_num(row.get("融资买入额"), 0),
                "margin_repay": None,  # 深交所原始数据无此列
                "short_volume": _safe_num(row.get("融券余量"), 0),
                "short_sell": _safe_num(row.get("融券卖出量"), 0),
                "short_repay": None,
            })

    return records if records else None


def get_margin_detail(symbol: str | None = None, date: str | None = None) -> dict:
    """融资融券明细（上交所+深交所合并，可按 code 过滤）。

    Args:
        symbol: 可选，6位股票代码过滤
        date: YYYYMMDD，缺省昨日

    返回 {date, total, stocks: [...]}，或 {"error": "..."}。
    """
    if not AK_AVAILABLE:
        return {"error": "akshare 未安装"}
    try:
        d = _normalize_date(date)
    except ValueError as e:
        return {"error": str(e)}
    sym = str(symbol).strip().lower() if symbol else None

    cache_key = f"margin:detail:{d}:{sym or 'all'}"

    def _fetch() -> Optional[dict[str, Any]]:
        records = _fetch_margin_detail(d)
        if records is None:
            return None
        if sym:
            records = [r for r in records if r["code"] == sym]
        return {
            "date": d,
            "total": len(records),
            "stocks": records,
        }

    try:
        result = cached(cache_key, TTL["financials"], _fetch)
    except Exception as e:
        return {"error": f"融资融券明细获取失败：{e}"}
    if result is None:
        return {"error": f"日期 {d} 融资融券数据为空（非交易日或数据未更新）"}
    return result


def get_margin_top(date: str | None = None, limit: int = 20) -> dict:
    """融资余额 TOP / 融券余量 TOP。

    返回 {date, margin_top: [...], short_top: [...]}，按融资余额/融券余量降序。
    """
    if not AK_AVAILABLE:
        return {"error": "akshare 未安装"}
    try:
        d = _normalize_date(date)
    except ValueError as e:
        return {"error": str(e)}
    limit = max(1, min(int(limit), 100))

    cache_key = f"margin:top:{d}:{limit}"

    def _fetch() -> Optional[dict[str, Any]]:
        records = _fetch_margin_detail(d)
        if records is None:
            return None
        # 按 margin_balance 降序（None 视为 0）
        margin_sorted = sorted(
            records,
            key=lambda r: r.get("margin_balance") or 0,
            reverse=True,
        )[:limit]
        # 按 short_volume 降序
        short_sorted = sorted(
            records,
            key=lambda r: r.get("short_volume") or 0,
            reverse=True,
        )[:limit]
        return {
            "date": d,
            "margin_top": margin_sorted,
            "short_top": short_sorted,
        }

    try:
        result = cached(cache_key, TTL["financials"], _fetch)
    except Exception as e:
        return {"error": f"融资融券 TOP 获取失败：{e}"}
    if result is None:
        return {"error": f"日期 {d} 融资融券数据为空（非交易日或数据未更新）"}
    return result
