"""新闻和实时快讯获取（新浪 7x24 全球财经直播 + 东方财富个股新闻）。

- get_flash_news：实时财经快讯（新浪直播流），支持关键词过滤
- get_news：个股新闻聚合（新浪快讯 + 东方财富搜索API兜底）
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

import requests

from .utils import TTL, _norm_symbol, cached


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


def get_news(symbol: str) -> Optional[list[dict[str, str]]]:
    """个股新闻：新浪快讯按名称/代码过滤 + 东方财富个股新闻兜底，缓存 15 分钟。"""
    sym = _norm_symbol(symbol)
    # 延迟导入以避免循环依赖（a_stock 不依赖 news，但 news 依赖 get_stock_brief）
    from .a_stock import get_stock_brief
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
