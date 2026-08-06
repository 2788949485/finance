"""板块轮动数据：行业板块 / 概念板块 涨跌排行+主力资金流入。

数据源策略（FiClash 封锁 push2.eastmoney.com）：
1. 优先 push2delay.eastmoney.com（东财延迟行情接口，字段与 push2 完全一致，已验证可用）
2. 降级 ak.stock_board_industry_name_em()（内部走 push2，大概率被封）

字段说明（东财 f-code）：
f2=最新价 f3=涨跌幅% f8=换手率 f12=板块代码 f14=板块名称
f62=今日主力净流入(元) f184=主力净流入占比%
f204=领涨股票 f205=领涨股票代码
"""
from __future__ import annotations

import math
import os
import time
from typing import Any, Optional

from curl_cffi import requests as cq

# 国内数据源绕过代理直连
_CN_DATA_DOMAINS = (
    "eastmoney.com,push2delay.eastmoney.com,push2his.eastmoney.com,"
    "datacenter-web.eastmoney.com,10jqka.com.cn,ths.cn,sina.com.cn,"
    "sse.com.cn,sseinfo.com,cninfo.com.cn,xueqiu.com,gtimg.cn,qq.com"
)
os.environ["NO_PROXY"] = os.environ.get("NO_PROXY", "") + "," + _CN_DATA_DOMAINS
os.environ["no_proxy"] = os.environ["NO_PROXY"]

from ..cache import TTL, cached  # noqa: E402

# 东财 push2 延迟行情（TLS 指纹封锁下的可用镜像）
_PUSH2_DELAY_HOST = "https://push2delay.eastmoney.com/api/qt/clist/get"
_UT = "b2884a393a59ad64002292a3e90d46a5"

# 行业 t:2 / 概念 t:3 / 地域 t:1
_SECTOR_FS = {"行业": "m:90 t:2", "概念": "m:90 t:3", "地域": "m:90 t:1"}

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    "Referer": "https://data.eastmoney.com/",
}


def _safe_num(v: Any, ndigits: int = 2) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return round(f, ndigits)


def _fetch_sector_page(sector_type: str, page: int, size: int) -> Optional[list[dict]]:
    """单页拉取板块资金流（今日指标）。返回原始 dict 列表或 None。"""
    params = {
        "pn": page,
        "pz": size,
        "po": "1",
        "np": "1",
        "ut": _UT,
        "fltt": "2",
        "invt": "2",
        "fid0": "f62",          # 按主力净流入排序
        "fs": _SECTOR_FS[sector_type],
        "stat": "1",            # 今日统计
        "fields": ("f12,f14,f2,f3,f8,f62,f184,f66,f69,f72,f75,f78,f81,"
                   "f84,f87,f204,f205,f124"),
        "rt": "52975239",
        "_": int(time.time() * 1000),
    }
    try:
        r = cq.get(_PUSH2_DELAY_HOST, params=params, impersonate="chrome",
                   timeout=10, headers=_HEADERS)
        data = r.json()
    except Exception:
        return None
    if not data or not data.get("data"):
        return None
    diff = data["data"].get("diff") or []
    return diff


def _fetch_sector_rank(sector_type: str, limit: int = 20) -> Optional[list[dict]]:
    """拉取某类板块的前 N 名（按主力净流入降序）。"""
    rows = _fetch_sector_page(sector_type, page=1, size=limit)
    if rows is None:
        return None
    result = []
    for r in rows:
        result.append({
            "code": str(r.get("f12", "")),
            "name": str(r.get("f14", "")),
            "change_pct": _safe_num(r.get("f3")),
            "turnover": _safe_num(r.get("f8")),
            "main_net_inflow": _safe_num(
                float(r.get("f62", 0)) / 1e8 if r.get("f62") is not None else None),
            "main_net_pct": _safe_num(r.get("f184")),
            "leading_stock": str(r.get("f204", "")),
            "leading_code": str(r.get("f205", "")),
        })
    return result


def get_concept_sectors(limit: int = 20) -> dict:
    """概念板块涨跌排行+主力资金流入（TOP N，按主力净流入降序）。

    push2delay 不通时降级为 {"error": "..."}。
    """
    cache_key = f"sector:concept:{limit}"

    def _fetch() -> Optional[dict[str, Any]]:
        rows = _fetch_sector_rank("概念", limit=limit)
        if rows is None:
            return None
        return {
            "type": "概念板块",
            "count": len(rows),
            "sectors": rows,
        }

    try:
        result = cached(cache_key, TTL["quote"], _fetch)
    except Exception as e:
        return {"error": f"概念板块获取失败：{e}"}
    if result is None:
        return {"error": "概念板块数据暂不可用（push2delay 接口超时）"}
    return result


def get_industry_sectors(limit: int = 20) -> dict:
    """行业板块涨跌排行+主力资金流入（TOP N，按主力净流入降序）。

    push2delay 不通时降级为 {"error": "..."}。
    """
    cache_key = f"sector:industry:{limit}"

    def _fetch() -> Optional[dict[str, Any]]:
        rows = _fetch_sector_rank("行业", limit=limit)
        if rows is None:
            return None
        return {
            "type": "行业板块",
            "count": len(rows),
            "sectors": rows,
        }

    try:
        result = cached(cache_key, TTL["quote"], _fetch)
    except Exception as e:
        return {"error": f"行业板块获取失败：{e}"}
    if result is None:
        return {"error": "行业板块数据暂不可用（push2delay 接口超时）"}
    return result
