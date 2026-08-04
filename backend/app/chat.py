"""对话式智能体：LangGraph ReAct agent + 后端状态（checkpointer）+ 长短记忆。

架构（参考 LangGraph 官方 memory 模式）：
- 短期记忆：SqliteSaver checkpointer，thread_id=session_id，
  对话状态（消息+工具调用链）由后端持久化，前端无需重发历史
- 长期记忆：user_memories 表，对话后自动提取用户事实（关注标的/偏好），
  下次对话注入 system prompt（mem0 思路）
- 会话可删除：删消息表 + checkpoint
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from typing import Any, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.prebuilt import create_react_agent

from .auth import get_profile
from .config import DB_PATH, get_config
from .llm import LLMClient
from .tools import COMPANY_ALIASES, FINANCE_TOOLS, HK_ALIASES, US_ALIASES, resolve_symbol

SYSTEM_PROMPT = """你是 FinanceCrew 的投研助理，一位专业的投资研究智能体，覆盖 A 股、港股、美股三大市场。

支持的标的格式：
- A股：6位代码（600519）或公司名（贵州茅台）
- 港股：hk+5位代码（hk00700）或公司名（腾讯）
- 美股：us+代码（usAAPL）或公司名（苹果）、或直接输入代码（AAPL）

工作方式：
1. 用户提到股票时，先调用工具获取真实数据（行情/K线/财务/龙虎榜/新闻），基于数据回答，不要凭空编造数字
2. 财务/龙虎榜/新闻/深度投研仅支持A股；港股美股可查询行情和K线，若用户需要深度分析请明确说明"该市场暂不支持深度投研"
3. 需要深度研判时，调用 run_research 运行多智能体投研分析
4. 回答用简体中文，专业、简洁；涉及价格/指标时注明数据来源和日期
5. 涉及投资建议时，末尾提醒"仅供参考，不构成投资建议"
6. 用户问题与股票无关时正常回答
7. 查询纪律：一次只查询用户明确询问的标的，不要批量查询多个股票；用户说公司名（如"腾讯"）时直接把它作为工具参数（get_quote("腾讯") 会自动解析为 hk00700），不要猜测或尝试其他代码
8. 回复中首次提到股票时用标准代码格式：A股6位数字（600519）、港股 hk+5位（hk00700）、美股 us+代码（usAAPL），方便前端展示行情卡片"""

MEMORY_EXTRACT_PROMPT = """从下面的对话中提取用户的持久性事实，用于长期记忆。
只提取值得长期记住的信息，例如：关注的股票/行业、风险偏好、投资风格、持仓、投资目标。
输出 JSON 数组，如 ["用户关注贵州茅台和腾讯控股", "用户风险偏好偏保守"]，没有可提取的返回 []。
只输出 JSON，不要其他文字。"""


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
    parts = [SYSTEM_PROMPT]
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


# ---------- 长期记忆 ----------

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
    """对话后从内容中提取长期记忆：规则提取股票代码 + LLM 提取用户事实。"""
    _init_db()
    # 1) 规则提取：对话中提到的股票
    codes = set(re.findall(r"\b(hk\d{5}|us[A-Z]{2,5}|[036]\d{5})\b", user_msg + assistant_msg))
    for code in codes:
        fact = f"用户关注标的：{code}"
        if not _memory_exists(user_id, fact):
            with _connect() as conn:
                conn.execute(
                    "INSERT INTO user_memories (user_id, memory_type, content, source_session, created_at) VALUES (?, 'stock', ?, ?, ?)",
                    (user_id, fact, session_id, datetime.now().isoformat(timespec="seconds")),
                )
    # 2) LLM 提取用户事实（仅用户消息，控制成本）
    cfg = get_config()
    if not (cfg.get("api_key") or "").strip():
        return
    try:
        llm = LLMClient(cfg)
        raw = llm.chat(MEMORY_EXTRACT_PROMPT, f"用户消息：{user_msg}\n助手回复：{assistant_msg[:300]}")
        facts = _parse_json_list(raw)
        for fact in facts[:3]:
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


# ---------- 对话 ----------

def chat(session_id: int, user_id: int, message: str) -> dict[str, Any]:
    """处理一轮对话：后端状态（checkpointer）管理上下文 + 长期记忆注入。

    返回 {reply, tool_calls, session_id}。
    """
    _init_db()
    save_message(session_id, "user", message)

    profile = get_profile(user_id)
    memories = get_user_memories(user_id)

    agent = build_agent(profile=profile, memories=memories)
    if agent is None:
        reply = "还没有配置大模型 API Key。请先到「模型配置」页填写（支持 DeepSeek/OpenAI/通义/Ollama 等任意 OpenAI 兼容服务）。"
        save_message(session_id, "assistant", reply)
        return {"reply": reply, "tool_calls": [], "session_id": session_id}

    try:
        # thread_id = session_id：对话状态由后端 checkpoint 持久化
        result = agent.invoke(
            {"messages": [HumanMessage(content=message)]},
            config={"configurable": {"thread_id": str(session_id)}},
        )
        reply = result["messages"][-1].content or ""
        tool_calls = []
        for m in result["messages"]:
            if getattr(m, "tool_calls", None):
                for tc in m.tool_calls:
                    tool_calls.append({"name": tc.get("name", ""), "args": tc.get("args", {})})
    except Exception as e:
        reply = f"对话处理失败: {e}"
        tool_calls = []

    save_message(session_id, "assistant", reply, tool_calls)

    # 长期记忆提取（异步场景可后台执行，这里同步但不阻塞主流程）
    try:
        extract_memories(user_id, message, reply, session_id)
    except Exception:
        pass

    # 首轮对话用用户消息前 12 字做标题
    if len(get_messages(session_id, user_id)) <= 2:
        rename_session(session_id, message[:12] + ("..." if len(message) > 12 else ""))

    return {"reply": reply, "tool_calls": tool_calls[:6], "session_id": session_id}
