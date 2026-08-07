"""行业同行管理：获取/生成/列出/保存/删除同行映射。

get_peers/auto_generate_peers/list_industry_peers/save_peers/delete_peers
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from ..config import get_config
from ..llm import LLMClient
from .db import _connect, _init_db


def get_peers(code: str) -> list[str] | None:
    """从数据库获取同行代码列表。"""
    _init_db()
    with _connect() as conn:
        row = conn.execute("SELECT peers FROM industry_peers WHERE code=?", (code,)).fetchone()
        if row:
            import json
            return json.loads(row[0])
    return None


def auto_generate_peers(code: str, name: str | None = None) -> list[str] | None:
    """用 LLM 自动生成同行映射并写入数据库。支持A股/港股/美股。"""
    # 先获取股票名称
    if not name:
        from ..data.fetcher import get_stock_brief
        brief = get_stock_brief(code)
        if not brief:
            return None
        name = brief.get("name", code)

    # 根据市场选择 prompt 和代码格式
    if code.startswith("hk"):
        system = "你是港股行业分析专家。根据公司名称判断所属行业，列出5只最直接的同行业竞争对手的港股代码。只返回JSON：{\"peers\": [\"hk00700\", \"hk09988\", ...]}"
        user = f"公司：{name}（{code}）。列出5只港股同行的代码，格式hk+5位数字。不要包含{code}本身。"
        clean_fn = lambda p: p.strip() if p.strip().startswith("hk") and len(p.strip()) >= 7 else None
    elif code.startswith("us"):
        system = "你是美股行业分析专家。根据公司名称判断所属行业，列出5只最直接的同行业竞争对手的美股代码。只返回JSON：{\"peers\": [\"AAPL\", \"MSFT\", ...]}"
        user = f"公司：{name}（{code}）。列出5只美股同行的股票代码（英文字母）。不要包含{code[2:]}本身。"
        clean_fn = lambda p: ("us" + p.strip().upper()) if p.strip().isalpha() and 1 <= len(p.strip()) <= 6 else None
    else:
        system = "你是A股行业分析专家。根据股票名称判断所属行业，列出5只最直接的同行业竞争对手的A股代码。只返回JSON：{\"peers\": [\"600519\", \"000858\", ...]}"
        user = f"股票：{name}（{code}）。列出5只同行的A股6位代码。不要包含{code}本身。"
        clean_fn = lambda p: p.strip()[:6] if isinstance(p, str) and len(p.strip()) >= 6 else None

    try:
        llm = LLMClient(get_config())
        result = llm.chat_json(system, user)
        peers = result.get("peers", [])
        if not peers or not isinstance(peers, list):
            return None
        # 按市场格式清理代码
        cleaned = [clean_fn(p) for p in peers if isinstance(p, str)]
        peers = [p for p in cleaned if p][:5]
        if len(peers) < 3:
            return None
        # 校验：过滤掉无法获取行情的假代码
        from ..data.fetcher import get_stock_brief
        valid_peers = []
        for pc in peers:
            if get_stock_brief(pc):
                valid_peers.append(pc)
            if len(valid_peers) >= 5:
                break
        if len(valid_peers) < 3:
            return None
        # 写入数据库
        save_peers(code, name, valid_peers)
        return valid_peers
    except Exception:
        return None


def list_industry_peers() -> list[dict[str, Any]]:
    """列出所有行业映射。"""
    _init_db()
    import json
    with _connect() as conn:
        rows = conn.execute("SELECT code, name, peers, updated_at FROM industry_peers ORDER BY code").fetchall()
        return [{"code": r[0], "name": r[1], "peers": json.loads(r[2]), "updated_at": r[3]} for r in rows]


def save_peers(code: str, name: str, peers: list[str]) -> None:
    """新增或更新行业映射。"""
    _init_db()
    import json
    from datetime import datetime
    with _connect() as conn:
        conn.execute(
            """INSERT INTO industry_peers (code, name, peers, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(code) DO UPDATE SET name=?, peers=?, updated_at=?""",
            (code, name, json.dumps(peers), datetime.now().isoformat(),
             name, json.dumps(peers), datetime.now().isoformat()),
        )
        conn.commit()


def delete_peers(code: str) -> bool:
    """删除行业映射。"""
    _init_db()
    with _connect() as conn:
        cur = conn.execute("DELETE FROM industry_peers WHERE code=?", (code,))
        conn.commit()
        return cur.rowcount > 0
