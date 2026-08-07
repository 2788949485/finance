"""股票搜索（腾讯智能搜索：代码/名称/拼音）。

从原 a_stock.py 拆分而来；函数签名、行为、返回值均未改变。
"""
from __future__ import annotations

from typing import Any, Optional

import requests

from .utils import cached


def search_stocks(q: str, limit: int = 8) -> Optional[list[dict[str, str]]]:
    """股票搜索（腾讯智能搜索：代码/名称/拼音），缓存 5 分钟。

    返回 [{market, code, name, type}]，只含股票（GP），排除指数/基金。
    美股代码规范化：us~aapl.oq -> usAAPL
    """
    if not q or not q.strip():
        return None

    # 腾讯搜索不支持 us/hk 前缀，去掉后再搜
    search_q = q.strip()
    if search_q.lower().startswith("us"):
        search_q = search_q[2:]
    elif search_q.lower().startswith("hk"):
        search_q = search_q[2:].lstrip("0") or search_q[2:]

    def _fetch() -> Optional[list[dict[str, str]]]:
        import json as _json
        import urllib.parse
        url = f"https://smartbox.gtimg.cn/s3/?v=2&q={urllib.parse.quote(search_q)}&t=all"
        try:
            r = requests.get(url, timeout=8)
            r.encoding = "gbk"
            body = r.text.split('"')[1] if '"' in r.text else ""
            items = []
            for part in body.split("^"):
                fields = part.split("~")
                if len(fields) < 5:
                    continue
                market, code, raw_name, _pn, typ = fields[0], fields[1], fields[2], fields[3], fields[4]
                if not (typ.startswith("GP") or typ == "GP"):
                    continue  # 只留股票
                # 名称是 \uXXXX 转义，解码
                try:
                    name = _json.loads(f'"{raw_name}"')
                except Exception:
                    name = raw_name
                if market == "us":
                    std = "us" + code.split(".")[0].upper()
                elif market in ("hk", "bj"):
                    std = market + code
                elif market in ("sh", "sz"):
                    std = code.zfill(6)
                else:
                    continue
                items.append({"market": market, "code": std, "name": name, "type": typ})
                if len(items) >= limit:
                    break
            return items or None
        except Exception:
            return None

    return cached(f"search:{q.strip()}", 300, _fetch)
