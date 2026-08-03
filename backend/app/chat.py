"""对话式智能体：LangGraph ReAct agent + 会话存储。

- create_react_agent：标准 ReAct 循环，智能体自主决定工具调用
- 会话与消息存 SQLite（chat_sessions / chat_messages），关联用户
- 无 API Key 时返回引导提示（agent 无法构建）
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.prebuilt import create_react_agent

from .config import DB_PATH, get_config
from .llm import LLMClient
from .tools import FINANCE_TOOLS

SYSTEM_PROMPT = """你是 FinanceCrew 的投研助理，一位专业的A股投资研究智能体。

工作方式：
1. 用户提到股票时，先调用工具获取真实数据（行情/K线/财务/龙虎榜/新闻），基于数据回答，不要凭空编造数字
2. 需要深度研判时，调用 run_research 运行多智能体投研分析
3. 回答用简体中文，专业、简洁；涉及价格/指标时注明数据来源和日期
4. 涉及投资建议时，末尾提醒"仅供参考，不构成投资建议"
5. 用户问题与股票无关时正常回答"""


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


def build_agent():
    """构建 ReAct 智能体；无 API Key 返回 None。"""
    cfg = get_config()
    if not (cfg.get("api_key") or "").strip():
        return None
    model = LLMClient(cfg)._build_model()
    if model is None:
        return None
    return create_react_agent(model, FINANCE_TOOLS, prompt=SYSTEM_PROMPT)


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


# ---------- 对话 ----------

def chat(session_id: int, user_id: int, message: str) -> dict[str, Any]:
    """处理一轮对话：保存用户消息 -> agent 推理 -> 保存回复。
    返回 {reply, tool_calls, session_id}。"""
    _init_db()
    save_message(session_id, "user", message)

    agent = build_agent()
    if agent is None:
        reply = "还没有配置大模型 API Key。请先到「模型配置」页填写（支持 DeepSeek/OpenAI/通义/Ollama 等任意 OpenAI 兼容服务）。"
        save_message(session_id, "assistant", reply)
        return {"reply": reply, "tool_calls": [], "session_id": session_id}

    # 组装历史（最近 20 条，控制上下文长度）
    history_msgs: list[Any] = []
    msgs = get_messages(session_id, user_id)[-20:]
    for m in msgs:
        if m["role"] == "user":
            history_msgs.append(HumanMessage(content=m["content"]))
        elif m["role"] == "assistant":
            history_msgs.append(AIMessage(content=m["content"]))

    try:
        result = agent.invoke({"messages": history_msgs})
        reply = result["messages"][-1].content or ""
        # 提取工具调用摘要
        tool_calls = []
        for m in result["messages"]:
            if getattr(m, "tool_calls", None):
                for tc in m.tool_calls:
                    tool_calls.append({
                        "name": tc.get("name", ""),
                        "args": tc.get("args", {}),
                    })
    except Exception as e:
        reply = f"对话处理失败: {e}"
        tool_calls = []

    save_message(session_id, "assistant", reply, tool_calls)

    # 首轮对话用用户消息前 12 字做标题
    if len(msgs) <= 1:
        rename_session(session_id, message[:12] + ("..." if len(message) > 12 else ""))

    return {"reply": reply, "tool_calls": tool_calls[:6], "session_id": session_id}
