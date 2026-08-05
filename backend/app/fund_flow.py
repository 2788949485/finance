"""资金流向分析：主力净流入/大单/超大单/北向资金。

数据源：东方财富push2接口（需代理，加重试）
"""
from __future__ import annotations

from typing import Any, Optional
import requests

from .cache import cached, TTL


def _em_secid(symbol: str) -> str:
    """东财secid：沪市1.xxx 深市0.xxx"""
    sym = symbol.replace("sh", "").replace("sz", "").replace("us", "").replace("hk", "")
    if symbol.startswith("sh") or sym.startswith("6") or sym.startswith("9"):
        return f"1.{sym}"
    return f"0.{sym}"


def _fetch_with_retry(url: str, retries: int = 2) -> Optional[dict]:
    """带重试的请求（curl_cffi模拟Chrome TLS指纹绕过反爬）"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Referer": "https://quote.eastmoney.com/",
    }
    for i in range(retries):
        try:
            from curl_cffi import requests as cffi_req
            r = cffi_req.get(url, impersonate="chrome", timeout=10, headers=headers)
            if r.status_code == 200 and r.text:
                return r.json()
        except Exception:
            continue
    return None


def get_fund_flow(symbol: str, days: int = 10) -> Optional[dict[str, Any]]:
    """个股资金流向（东财接口）。

    返回最近N天的主力/超大单/大单/中单/小单净流入。
    """
    sym = symbol.replace("sh", "").replace("sz", "")
    secid = _em_secid(sym)

    cache_key = f"fundflow:{sym}:{days}"

    def _fetch() -> Optional[dict[str, Any]]:
        url = (
            f"https://push2his.eastmoney.com/api/qt/stock/getFFlowDaykline/get?"
            f"secid={secid}&lmt={days}"
            f"&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56,f57,f58"
        )
        data = _fetch_with_retry(url)
        if data is None:
            return None
        klines = data.get("data", {}).get("klines", [])
        if not klines:
            return None

        records = []
        for line in klines:
            parts = line.split(",")
            if len(parts) < 6:
                continue
            try:
                records.append({
                    "date": parts[0],
                    "main_net": round(float(parts[1]) / 1e8, 2),      # 主力净流入(亿)
                    "small_net": round(float(parts[2]) / 1e8, 2),      # 小单净流入(亿)
                    "medium_net": round(float(parts[3]) / 1e8, 2),     # 中单+小单净流入(亿)
                    "large_net": round(float(parts[4]) / 1e8, 2),      # 大单净流入(亿)
                    "super_net": round(float(parts[5]) / 1e8, 2),      # 超大单净流入(亿)
                    "main_pct": round(float(parts[6]), 2) if len(parts) > 6 else 0,  # 主力净占比%
                })
            except (ValueError, IndexError):
                continue

        if not records:
            return None

        latest = records[-1]
        return {
            "symbol": sym,
            "latest_date": latest["date"],
            "latest_main_net": latest["main_net"],
            "latest_super_net": latest["super_net"],
            "latest_large_net": latest["large_net"],
            "latest_main_pct": latest["main_pct"],
            "summary": _flow_summary(latest["main_net"], latest["super_net"]),
            "history": records[-min(days, len(records)):],
        }

    return cached(cache_key, 300, _fetch)  # 缓存5分钟


def _flow_summary(main_net: float, super_net: float) -> str:
    """生成资金流向摘要"""
    parts = []
    if main_net > 0:
        parts.append(f"主力净流入{main_net:.2f}亿元")
    elif main_net < 0:
        parts.append(f"主力净流出{abs(main_net):.2f}亿元")
    else:
        parts.append("主力资金持平")

    if super_net > 0:
        parts.append(f"超大单净流入{super_net:.2f}亿元")
    elif super_net < 0:
        parts.append(f"超大单净流出{abs(super_net):.2f}亿元")

    return "，".join(parts)
