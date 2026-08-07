"""数据库 CRUD：会话/消息持久化 + checkpointer + agent 构建。

包含：_connect/_init_db/_new_checkpointer/_code_name/_make_prompt/build_agent
+ 会话 CRUD (create_session/list_sessions/search_messages/delete_session/
get_messages/save_message/rename_session)
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from typing import Any

from langchain_core.messages import HumanMessage  # noqa: F401  (保持导入侧可用)
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.prebuilt import create_react_agent

from ..config import DB_PATH, get_config
from ..llm import LLMClient
from ..tools import COMPANY_ALIASES, FINANCE_TOOLS, HK_ALIASES, US_ALIASES
from .prompts import SYSTEM_PROMPT


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS chat_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                title TEXT DEFAULT '新对话',
                created_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                tool_calls TEXT DEFAULT '[]',
                created_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS user_memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                memory_type TEXT DEFAULT 'fact',
                content TEXT NOT NULL,
                source_session INTEGER,
                created_at TEXT NOT NULL
            )"""
        )
        # 行业同行映射
        conn.execute(
            """CREATE TABLE IF NOT EXISTS industry_peers (
                code TEXT PRIMARY KEY,
                name TEXT,
                peers TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL
            )"""
        )
        # 预填充常用行业映射（首次启动时）
        row = conn.execute("SELECT COUNT(*) FROM industry_peers").fetchone()
        if row[0] == 0:
            import json
            presets = {
                "600519": ("贵州茅台", ["000858", "000568", "002304", "603369", "600809"]),
                "000858": ("五粮液", ["600519", "000568", "002304", "603369", "600809"]),
                "000001": ("平安银行", ["600036", "601398", "601939", "601318", "600000"]),
                "600036": ("招商银行", ["000001", "601398", "601939", "601318", "600000"]),
                "300750": ("宁德时代", ["002594", "300014", "600089", "300274", "002460"]),
                "002594": ("比亚迪", ["300750", "601238", "600104", "601633", "000625"]),
                "601318": ("中国平安", ["000001", "600036", "601398", "601628", "601601"]),
            }
            from datetime import datetime
            now = datetime.now().isoformat()
            for code, (name, peers) in presets.items():
                conn.execute(
                    "INSERT INTO industry_peers (code, name, peers, updated_at) VALUES (?, ?, ?, ?)",
                    (code, name, json.dumps(peers), now),
                )


def _new_checkpointer() -> SqliteSaver:
    """每个调用新建 SQLite 连接构造 saver，避免跨线程共享连接问题。

    from_conn_string 返回上下文管理器（with 退出即关闭连接），
    这里直接传连接对象保持连接存活。"""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    return SqliteSaver(conn)


def _code_name(code: str) -> str:
    """代码 -> 公司名（反向查映射表），用于记忆注入时带名称避免模型乱猜。"""
    code = code.upper()
    for alias_map in (COMPANY_ALIASES, HK_ALIASES, US_ALIASES):
        for name, c in alias_map.items():
            if c.upper() == code:
                return name
    return code


def _make_prompt(profile: dict, memories: list[str]) -> str:
    """动态 system prompt 字符串：注入用户画像 + 长期记忆。

    注意：langgraph 0.2 的 create_react_agent 的 prompt 参数必须传字符串
    （callable 形式不生效，会导致模型行为异常/英文回复/批量乱查工具）。
    """
    parts = [SYSTEM_PROMPT.replace("{current_time}", datetime.now().strftime("%Y年%m月%d日 %H:%M %A"))]
    if memories:
        # 记忆中的代码补充公司名（hk00700 -> hk00700(腾讯控股)），避免模型乱猜
        enhanced = []
        for m in memories[:10]:
            def _add_name(mm: str) -> str:
                for code in re.findall(r"\b(hk\d{5}|us[A-Z]{2,5}|[036]\d{5})\b", mm, re.I):
                    name = _code_name(code)
                    if name != code:
                        mm = mm.replace(code, f"{code}({name})")
                return mm
            enhanced.append(_add_name(m))
        parts.append("关于用户的长久记忆（可参考但不要编造）：\n- " + "\n- ".join(enhanced))
    if profile.get("risk_preference") and profile["risk_preference"] != "balanced":
        label = {"conservative": "保守", "aggressive": "激进"}.get(profile["risk_preference"], "平衡")
        parts.append(f"用户风险偏好：{label}，给出仓位/止损建议时适当贴合该偏好。")
    if profile.get("watchlist"):
        named = ", ".join(f"{w}({_code_name(w)})" for w in profile["watchlist"])
        parts.append(f"用户自选股：{named}，可主动关注。")
    return "\n\n".join(parts)


def build_agent(profile: dict | None = None, memories: list[str] | None = None):
    """构建 ReAct 智能体（带 checkpointer 后端状态）；无 API Key 返回 None。"""
    cfg = get_config()
    if not (cfg.get("api_key") or "").strip():
        return None
    model = LLMClient(cfg)._build_model()
    if model is None:
        return None
    prompt = _make_prompt(profile or {}, memories or [])
    return create_react_agent(model, FINANCE_TOOLS, prompt=prompt, checkpointer=_new_checkpointer())


# ---------- 会话 CRUD ----------

def create_session(user_id: int, title: str = "新对话") -> int:
    _init_db()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO chat_sessions (user_id, title, created_at) VALUES (?, ?, ?)",
            (user_id, title, datetime.now().isoformat(timespec="seconds")),
        )
        return int(cur.lastrowid)


def list_sessions(user_id: int, limit: int = 30) -> list[dict[str, Any]]:
    _init_db()
    with _connect() as conn:
        rows = conn.execute(
            """SELECT s.id, s.title, s.created_at,
                      (SELECT COUNT(*) FROM chat_messages m WHERE m.session_id = s.id) as msg_count
               FROM chat_sessions s WHERE s.user_id=? ORDER BY s.id DESC LIMIT ?""",
            (user_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def search_messages(user_id: int, keyword: str, limit: int = 20) -> list[dict[str, Any]]:
    """搜索用户所有对话中的消息（按关键词模糊匹配 content）。"""
    _init_db()
    kw = f"%{keyword}%"
    with _connect() as conn:
        rows = conn.execute(
            """SELECT m.id, m.session_id, m.role, m.content, m.created_at, s.title as session_title
               FROM chat_messages m
               JOIN chat_sessions s ON m.session_id = s.id
               WHERE s.user_id=? AND m.content LIKE ?
               ORDER BY m.id DESC LIMIT ?""",
            (user_id, kw, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def delete_session(session_id: int, user_id: int) -> bool:
    """删除会话：校验归属 -> 删消息/会话 -> 删 checkpoint 状态。"""
    _init_db()
    with _connect() as conn:
        row = conn.execute("SELECT user_id FROM chat_sessions WHERE id=?", (session_id,)).fetchone()
        if row is None or row["user_id"] != user_id:
            return False
        conn.execute("DELETE FROM chat_messages WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM chat_sessions WHERE id=?", (session_id,))
    try:
        _new_checkpointer().delete_thread(str(session_id))
    except Exception:
        pass  # checkpoint 不存在时忽略
    return True


def get_messages(session_id: int, user_id: int) -> list[dict[str, Any]]:
    _init_db()
    with _connect() as conn:
        row = conn.execute("SELECT user_id FROM chat_sessions WHERE id=?", (session_id,)).fetchone()
    if row is None or row["user_id"] != user_id:
        return []
    with _connect() as conn:
        rows = conn.execute(
            "SELECT role, content, tool_calls, created_at FROM chat_messages WHERE session_id=? ORDER BY id",
            (session_id,),
        ).fetchall()
    out = []
    for r in rows:
        item = {"role": r["role"], "content": r["content"], "created_at": r["created_at"]}
        try:
            item["tool_calls"] = json.loads(r["tool_calls"])
        except json.JSONDecodeError:
            item["tool_calls"] = []
        out.append(item)
    return out


def save_message(session_id: int, role: str, content: str, tool_calls: list[dict] | None = None) -> None:
    _init_db()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO chat_messages (session_id, role, content, tool_calls, created_at) VALUES (?, ?, ?, ?, ?)",
            (session_id, role, content, json.dumps(tool_calls or [], ensure_ascii=False),
             datetime.now().isoformat(timespec="seconds")),
        )


def rename_session(session_id: int, title: str) -> None:
    _init_db()
    with _connect() as conn:
        conn.execute("UPDATE chat_sessions SET title=? WHERE id=?", (title[:30], session_id))
