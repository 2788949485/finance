"""北向资金数据：总览趋势 / 个股持股历史 / 增持排行。

数据源：
- ak.stock_hsgt_hist_em           北向资金总览历史（push2his，可用）
- ak.stock_hsgt_individual_em     个股北向持股历史（push2his，可用）
- ak.stock_hsgt_hold_stock_em     北向持股排行（datacenter-web，部分时段不可用 -> 降级返回 error）
"""
from __future__ import annotations

import os
from typing import Any, Optional

# 与 fetcher 一致：国内数据源直连绕过本机代理
_CN_DATA_DOMAINS = (
    "eastmoney.com,push2his.eastmoney.com,datacenter-web.eastmoney.com,"
    "10jqka.com.cn,ths.cn,sina.com.cn,sse.com.cn,sseinfo.com,cninfo.com.cn,"
    "xueqiu.com,gtimg.cn,qq.com"
)
os.environ["NO_PROXY"] = os.environ.get("NO_PROXY", "") + "," + _CN_DATA_DOMAINS
os.environ["no_proxy"] = os.environ["NO_PROXY"]

try:
    import akshare as ak
    AK_AVAILABLE = True
except Exception:  # pragma: no cover
    ak = None
    AK_AVAILABLE = False

from ..cache import TTL, cached


def _safe_num(v: Any, ndigits: int = 2) -> Optional[float]:
    """转 float 容错；失败/NaN 返回 None。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return round(f, ndigits)


def get_north_flow_overview() -> dict:
    """北向资金总览：近30天净流入趋势。

    返回 {latest_date, latest_net, cumulative, history: [{date, net_buy,
    buy_amount, sell_amount, cumulative}]}
    数据源失败时返回 {"error": "..."}。
    """
    if not AK_AVAILABLE:
        return {"error": "akshare 未安装"}

    def _fetch() -> Optional[dict[str, Any]]:
        df = ak.stock_hsgt_hist_em(symbol="北向资金")
        if df is None or df.empty:
            return None
        # 列：日期 当日成交净买额 买入成交额 卖出成交额 历史累计净买额 ...
        df = df.tail(30).copy()
        history = []
        for _, row in df.iterrows():
            history.append({
                "date": str(row["日期"].strftime("%Y-%m-%d")
                            if hasattr(row["日期"], "strftime") else row["日期"]),
                "net_buy": _safe_num(row.get("当日成交净买额")),
                "buy_amount": _safe_num(row.get("买入成交额")),
                "sell_amount": _safe_num(row.get("卖出成交额")),
                "cumulative": _safe_num(row.get("历史累计净买额")),
            })
        latest = history[-1] if history else {}
        return {
            "latest_date": latest.get("date"),
            "latest_net": latest.get("net_buy"),
            "cumulative": latest.get("cumulative"),
            "history": history,
        }

    try:
        result = cached("north_flow:overview", TTL["kline"], _fetch)
    except Exception as e:
        return {"error": f"北向资金总览获取失败：{e}"}
    if result is None:
        return {"error": "北向资金总览数据为空（接口可能不可用）"}
    return result


def get_north_flow_stock(symbol: str) -> dict:
    """个股北向持股历史：最近60天。

    返回 {symbol, history: [{date, close, change_pct, hold_shares, hold_value,
    hold_pct, change_shares}]}
    """
    if not AK_AVAILABLE:
        return {"error": "akshare 未安装"}
    sym = str(symbol).strip().lower()

    def _fetch() -> Optional[dict[str, Any]]:
        df = ak.stock_hsgt_individual_em(symbol=sym)
        if df is None or df.empty:
            return None
        df = df.tail(60).copy()
        history = []
        for _, row in df.iterrows():
            history.append({
                "date": str(row["持股日期"].strftime("%Y-%m-%d")
                            if hasattr(row["持股日期"], "strftime") else row["持股日期"]),
                "close": _safe_num(row.get("当日收盘价")),
                "change_pct": _safe_num(row.get("当日涨跌幅")),
                "hold_shares": _safe_num(row.get("持股数量"), 0),
                "hold_value": _safe_num(row.get("持股市值"), 2),
                "hold_pct": _safe_num(row.get("持股数量占A股百分比"), 4),
                "change_shares": _safe_num(row.get("今日增持股数"), 0),
            })
        return {
            "symbol": sym,
            "history": history,
            "latest": history[-1] if history else {},
        }

    try:
        result = cached(f"north_flow:stock:{sym}", TTL["kline"], _fetch)
    except Exception as e:
        return {"error": f"个股北向持股获取失败：{e}"}
    if result is None:
        return {"error": f"个股 {sym} 北向持股数据为空（非沪深股通标的或接口超时）"}
    return result


def get_north_flow_top_stocks(market: str = "沪股通", period: str = "5日排行") -> dict:
    """北向持股增持排行 TOP20（push2delay接口，实时可用）。

    2024年8月港交所停止实时披露后，用push2delay拉沪深股通成份股资金排行。

    Args:
        market: 沪股通 / 深股通
        period: 5日排行 / 10日排行 / 月排行

    返回 {market, period, date, top: [{code, name, price, change_pct, net_inflow, hold_pct}]}
    """
    from curl_cffi import requests as cq

    # 沪股通=m:1 t:23  深股通=m:0 t:80
    fs_map = {"沪股通": "m:1 t:23", "深股通": "m:0 t:80"}
    fs = fs_map.get(market, "m:1 t:23")

    # period转排序字段fid
    period_map = {"5日排行": "f62", "10日排行": "f164", "月排行": "f184"}
    fid = period_map.get(period, "f62")

    cache_key = f"north_flow:top:{market}:{period}"

    def _fetch() -> Optional[dict[str, Any]]:
        url = "https://push2delay.eastmoney.com/api/qt/clist/get"
        params = {
            "pn": 1, "pz": 20, "po": 1, "np": 1,
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": 2, "invt": 2,
            "fid": fid,
            "fs": fs,
            "fields": "f12,f14,f2,f3,f62,f184,f66,f72",
        }
        try:
            r = cq.get(url, params=params, impersonate="chrome", timeout=10)
            d = r.json()
        except Exception:
            return None
        items = d.get("data", {}).get("diff", [])
        if not items:
            return None
        top = []
        for it in items:
            top.append({
                "code": str(it.get("f12", "")),
                "name": str(it.get("f14", "")),
                "price": it.get("f2"),
                "change_pct": it.get("f3"),
                "net_inflow": it.get("f62"),       # 主力净流入
                "hold_pct": it.get("f184"),         # 持股比例
            })
        return {
            "market": market,
            "period": period,
            "date": "",
            "top": top,
        }

    try:
        result = cached(cache_key, TTL["quote"], _fetch)
    except Exception as e:
        return {"error": f"北向持股排行获取失败：{e}"}
    if result is None:
        return {"error": "北向持股排行暂不可用"}
    return result
