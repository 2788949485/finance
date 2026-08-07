"""长期记忆管理：用户事实/关注标的的提取与维护。

get_user_memories/extract_memories/_cleanup_stock_memories/
_get_existing_stock_codes/_memory_exists/_parse_json_list
"""
from __future__ import annotations

import json
import re
from datetime import datetime

from ..config import get_config
from ..llm import LLMClient
from .db import _connect, _init_db
from .prompts import MEMORY_EXTRACT_PROMPT


def get_user_memories(user_id: int, limit: int = 20) -> list[str]:
    _init_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT content FROM user_memories WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [r["content"] for r in rows]


def _memory_exists(user_id: int, content: str) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM user_memories WHERE user_id=? AND content=?", (user_id, content)
        ).fetchone()
    return row is not None


def extract_memories(user_id: int, user_msg: str, assistant_msg: str, session_id: int) -> None:
    """对话后从内容中提取长期记忆：规则提取股票代码 + LLM 提取用户事实。

    注意：只从用户消息提取股票代码（AI回复里提到的股票不代表用户关注）。
    """
    _init_db()
    # 先清理旧的冗余记忆（合并重复标的）
    _cleanup_stock_memories(user_id)

    # 1) 规则提取：只从用户消息提取（AI回复里的股票不代表用户关注）
    codes = set(re.findall(r"(hk\d{5}|us[A-Z]{2,5}|[036]\d{5})", user_msg))
    existing = _get_existing_stock_codes(user_id)
    for code in codes:
        if code in existing:
            continue  # 已有该标的的记忆，跳过
        fact = f"用户关注标的：{code}"
        if not _memory_exists(user_id, fact):
            with _connect() as conn:
                conn.execute(
                    "INSERT INTO user_memories (user_id, memory_type, content, source_session, created_at) VALUES (?, 'stock', ?, ?, ?)",
                    (user_id, fact, session_id, datetime.now().isoformat(timespec="seconds")),
                )
    # 2) LLM 提取用户事实（仅用户消息）
    cfg = get_config()
    if not (cfg.get("api_key") or "").strip():
        return
    try:
        llm = LLMClient(cfg)
        raw = llm.chat(MEMORY_EXTRACT_PROMPT, f"用户消息：{user_msg}")
        facts = _parse_json_list(raw)
        for fact in facts[:2]:  # 每次最多存2条
            fact = str(fact).strip()
            if len(fact) < 8 or len(fact) > 80:
                continue
            if not _memory_exists(user_id, fact):
                with _connect() as conn:
                    conn.execute(
                        "INSERT INTO user_memories (user_id, memory_type, content, source_session, created_at) VALUES (?, 'fact', ?, ?, ?)",
                        (user_id, fact, session_id, datetime.now().isoformat(timespec="seconds")),
                    )
    except Exception:
        pass


def _get_existing_stock_codes(user_id: int) -> set[str]:
    """获取已记录的股票代码集合（用于去重）。"""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT content FROM user_memories WHERE user_id=? AND memory_type='stock'",
            (user_id,),
        ).fetchall()
    codes: set[str] = set()
    for r in rows:
        # 从"用户关注标的：600519"中提取代码
        m = re.search(r"(hk\d{5}|us[A-Z]{2,5}|[036]\d{5})", r["content"])
        if m:
            codes.add(m.group(1))
    return codes


def _cleanup_stock_memories(user_id: int) -> None:
    """清理冗余的股票记忆：同一代码只保留最新一条，fact类只保留最近10条。"""
    with _connect() as conn:
        # stock类：同代码去重，只留最新
        rows = conn.execute(
            """SELECT id, content FROM user_memories
               WHERE user_id=? AND memory_type='stock'
               ORDER BY id DESC""",
            (user_id,),
        ).fetchall()
        seen_codes: set[str] = set()
        to_delete: list[int] = []
        for r in rows:
            m = re.search(r"(hk\d{5}|us[A-Z]{2,5}|[036]\d{5})", r["content"])
            code = m.group(1) if m else r["content"]
            if code in seen_codes:
                to_delete.append(r["id"])
            else:
                seen_codes.add(code)
        if to_delete:
            conn.executemany(
                "DELETE FROM user_memories WHERE id=?",
                [(tid,) for tid in to_delete],
            )

        # fact类：只保留最近10条
        fact_rows = conn.execute(
            """SELECT id FROM user_memories
               WHERE user_id=? AND memory_type='fact'
               ORDER BY id DESC""",
            (user_id,),
        ).fetchall()
        if len(fact_rows) > 10:
            old_ids = [r["id"] for r in fact_rows[10:]]
            conn.executemany(
                "DELETE FROM user_memories WHERE id=?",
                [(tid,) for tid in old_ids],
            )


def _parse_json_list(text: str) -> list[str]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
    try:
        data = json.loads(cleaned)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        start, end = cleaned.find("["), cleaned.rfind("]")
        if start != -1 and end != -1:
            try:
                data = json.loads(cleaned[start : end + 1])
                return data if isinstance(data, list) else []
            except json.JSONDecodeError:
                pass
        return []
