"""条件选股器：全市场快照+多条件过滤+排序。

数据源：新浪财经 quotes_service（东财 push2 被封锁，新浪可用）。
全市场约 5500 只 A 股，分页拉取（每页 100，约 56 页）。

新浪字段：
symbol sh600000 / code 600000 / name / trade 现价 / pricechange /
changepercent 涨跌% / per PE / pb PB / mktcap 总市值(万元) /
nmc 流通市值(万元) / turnoverratio 换手%
"""
from __future__ import annotations

import os
import time
from typing import Any, Optional

import pandas as pd
from curl_cffi import requests as cq

# 国内数据源直连
_CN_DATA_DOMAINS = (
    "sina.com.cn,finance.sina.com.cn,eastmoney.com,10jqka.com.cn,ths.cn,"
    "sse.com.cn,sseinfo.com,cninfo.com.cn,gtimg.cn,qq.com"
)
os.environ["NO_PROXY"] = os.environ.get("NO_PROXY", "") + "," + _CN_DATA_DOMAINS
os.environ["no_proxy"] = os.environ["NO_PROXY"]

from ..cache import TTL, cached  # noqa: E402

_SINA_URL = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/"
             "json_v2.php/Market_Center.getHQNodeData")

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    "Referer": "https://vip.stock.finance.sina.com.cn/",
}

# 选股排序字段名 → DataFrame 列名映射
_SORT_MAP = {
    "change_pct": "change_pct",
    "pe": "pe",
    "pb": "pb",
    "turnover": "turnover",
    "market_cap": "market_cap",
    "price": "price",
}


def _fetch_all_stocks() -> Optional[pd.DataFrame]:
    """拉取全市场 A 股快照（新浪 API 分页）。

    返回标准化 DataFrame：code, name, price, change_pct, pe, pb,
    turnover, market_cap(亿元), negotiable_cap(亿元)。
    """
    all_rows = []
    page = 1
    max_pages = 60  # 安全上限：5500/100≈56
    while page <= max_pages:
        params = {
            "page": page,
            "num": 100,
            "sort": "symbol",
            "asc": 1,
            "node": "hs_a",
            "symbol": "",
            "_s_r_a": "sort",
        }
        try:
            r = cq.get(_SINA_URL, params=params, impersonate="chrome",
                       timeout=12, headers=_HEADERS)
            data = r.json()
        except Exception:
            break
        if not isinstance(data, list) or not data:
            break
        for row in data:
            try:
                all_rows.append({
                    "code": str(row.get("code", "")),
                    "symbol": str(row.get("symbol", "")),
                    "name": str(row.get("name", "")),
                    "price": _to_float(row.get("trade")),
                    "change_pct": _to_float(row.get("changepercent")),
                    "pe": _to_float(row.get("per")),
                    "pb": _to_float(row.get("pb")),
                    "turnover": _to_float(row.get("turnoverratio")),
                    "market_cap": _to_float(row.get("mktcap")),
                    "negotiable_cap": _to_float(row.get("nmc")),
                })
            except (ValueError, TypeError):
                continue
        if len(data) < 100:
            break  # 最后一页
        page += 1
    if not all_rows:
        return None
    df = pd.DataFrame(all_rows)
    # 市值单位换算：新浪 mktcap/nmc 单位为「万元」-> 转为「亿元」
    df["market_cap"] = df["market_cap"] / 1e4
    df["negotiable_cap"] = df["negotiable_cap"] / 1e4
    return df


def _to_float(v: Any) -> Optional[float]:
    """容错转 float；空/异常返回 None。"""
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return round(f, 4)


def screen_stocks(
    pe_min: Optional[float] = None,
    pe_max: Optional[float] = None,
    pb_min: Optional[float] = None,
    pb_max: Optional[float] = None,
    change_pct_min: Optional[float] = None,
    change_pct_max: Optional[float] = None,
    turnover_min: Optional[float] = None,
    turnover_max: Optional[float] = None,
    market_cap_min: Optional[float] = None,
    market_cap_max: Optional[float] = None,
    sort_by: str = "change_pct",
    sort_desc: bool = True,
    limit: int = 50,
) -> dict:
    """条件选股：全市场快照+多条件过滤+排序。

    所有金额/市值单位均为「亿元」；换手率/涨跌幅为百分比。
    sort_by: change_pct / pe / pb / turnover / market_cap / price
    返回 {total_market, matched, stocks: [...]}
    """
    limit = max(1, min(int(limit), 200))

    def _fetch() -> Optional[dict[str, Any]]:
        df = _fetch_all_stocks()
        if df is None or df.empty:
            return None
        total_market = len(df)

        # 过滤条件（忽略 None 条件）
        def _filter(col: str, lo, hi):
            nonlocal df
            if lo is not None:
                df = df[(df[col].isna()) | (df[col] >= lo)]
            if hi is not None:
                df = df[(df[col].isna()) | (df[col] <= hi)]

        _filter("pe", pe_min, pe_max)
        _filter("pb", pb_min, pb_max)
        _filter("change_pct", change_pct_min, change_pct_max)
        _filter("turnover", turnover_min, turnover_max)
        _filter("market_cap", market_cap_min, market_cap_max)

        matched = len(df)

        # 排序
        sort_col = _SORT_MAP.get(sort_by, "change_pct")
        if sort_col in df.columns:
            df = df.sort_values(by=sort_col, ascending=not sort_desc,
                                na_position="last")
        df = df.head(limit)

        stocks = []
        for _, row in df.iterrows():
            stocks.append({
                "code": row["code"],
                "name": row["name"],
                "price": row["price"],
                "change_pct": row["change_pct"],
                "pe": row["pe"],
                "pb": row["pb"],
                "turnover": row["turnover"],
                "market_cap": row["market_cap"],
                "negotiable_cap": row["negotiable_cap"],
            })
        return {
            "total_market": total_market,
            "matched": matched,
            "returned": len(stocks),
            "stocks": stocks,
        }

    # 全市场快照缓存 60 秒（行情级 TTL），过滤在缓存外做
    # 但为了简单+缓存命中，把整个 screen 结果按参数 key 缓存
    ck = (f"screener:{pe_min}:{pe_max}:{pb_min}:{pb_max}:"
          f"{change_pct_min}:{change_pct_max}:{turnover_min}:{turnover_max}:"
          f"{market_cap_min}:{market_cap_max}:{sort_by}:{sort_desc}:{limit}")
    try:
        result = cached(ck, TTL["quote"], _fetch)
    except Exception as e:
        return {"error": f"选股失败：{e}"}
    if result is None:
        return {"error": "全市场快照拉取失败（新浪 API 超时或被限制）"}
    return result
