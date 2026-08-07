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
import time
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

当前时间：{current_time}

支持的标的格式：
- A股：6位代码（600519）或公司名（贵州茅台）
- 港股：hk+5位代码（hk00700）或公司名（腾讯）
- 美股：us+代码（usAAPL）或公司名（苹果）、或直接输入代码（AAPL）

工作方式：
1. 用户提到股票时，先调用工具获取真实数据（行情/K线/财务/龙虎榜/新闻），基于数据回答，不要凭空编造数字
2. 财务/龙虎榜/情绪数据仅支持A股（数据源限制）；港股美股可查询行情/K线/深度投研；若用户需要的数据暂不可用请明确说明
3. 需要深度研判时，调用 run_research 运行多智能体投研分析
4. 回答用简体中文，专业、简洁；涉及价格/指标时注明数据来源和日期
5. 排版紧凑：段落之间不要空行，用单换行分隔；不要用多个空行制造间距
6. 数学公式、伪代码用LaTeX格式：行内公式用$...$包裹（如$E=mc^2$），独立公式用$$...$$包裹（如$$回撤_t = \\frac{总资产_t}{峰值_t} - 1$$）
7. 涉及投资建议时，末尾提醒"仅供参考，不构成投资建议"
8. 用户问题与股票无关时正常回答
9. 查询纪律：一次只查询用户明确询问的标的，不要批量查询多个股票；用户说公司名（如"腾讯"）时直接把它作为工具参数（get_quote("腾讯") 会自动解析为 hk00700），不要猜测或尝试其他代码
10. 回复中首次提到股票时用标准代码格式：A股6位数字（600519）、港股 hk+5位（hk00700）、美股 us+代码（usAAPL），方便前端展示行情卡片
11. 遇到不认识的股票名称（如新上市公司），先调用 search_stock 搜索代码，或 web_search 联网查询最新信息，不要凭训练数据断言"不存在"或"未上市"
12. 联网搜索结果中的日期可能不是最新的，请以上方"当前时间"为准判断
13. 用户问"我之前分析过XX"或需要引用历史研究时，调用 search_my_research 搜索用户在本平台的历史投研记录"""

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


# ---------- 意图识别（轻量前置层） ----------

# 触发分析意图的关键词
_INTENT_KEYWORDS = [
    "分析", "调研", "研究", "诊断", "评估", "看看", "帮我看", "怎么样",
    "投研", "报告", "分析一下", "看一下", "深度分析", "全面分析",
    "短线", "中线", "长线", "波段", "趋势",
]
# 投资周期关键词
_HORIZON_PATTERNS = [
    (r"短线|短期|几天|超短", "短线"),
    (r"中线|中期|几周|波段", "中线"),
    (r"长线|长期|价值投资|持有", "长线"),
]


def _detect_analysis_intent(message: str) -> dict[str, Any] | None:
    """轻量意图检测（无LLM调用，纯关键词规则）。

    返回 None 表示不是分析意图；否则返回:
      {"symbol": str|None, "horizon": str|None, "mode": "standard"|"agentic"}
    """
    msg = message.strip()

    # 是否包含分析意图关键词
    has_intent = any(kw in msg for kw in _INTENT_KEYWORDS)
    if not has_intent:
        return None

    # 提取标的：优先匹配已知代码/公司名
    symbol = None

    # A股代码（不依赖\b，因为中文后面没有词边界）
    a_match = re.search(r"([036]\d{5})", msg)
    if a_match:
        symbol = a_match.group(1)
    if not symbol:
        # 港股
        hk_match = re.search(r"(hk\d{5})", msg, re.I)
        if hk_match:
            symbol = hk_match.group(1).lower()
    if not symbol:
        # 美股
        us_match = re.search(r"(us[A-Z]{2,5})", msg, re.I)
        if us_match:
            symbol = us_match.group(1)
    if not symbol:
        # 公司名匹配
        for name, code in {**COMPANY_ALIASES, **HK_ALIASES, **US_ALIASES}.items():
            if name in msg:
                symbol = code
                break
    if not symbol:
        # 提取分析关键词之前的文本作为候选公司名
        for kw in _INTENT_KEYWORDS:
            if kw in msg:
                idx = msg.index(kw)
                candidate = msg[:idx].strip()
                # 去掉"帮我把帮我们"等前缀词
                candidate = re.sub(r"^(帮|请|麻烦|来|去|给我|给我来|我想|我要|能不能|可以|帮我分析|帮我看)\s*", "", candidate)
                if candidate and len(candidate) <= 10:
                    resolved = resolve_symbol(candidate)
                    if resolved and resolved != candidate:
                        symbol = resolved
                        break

    if not symbol:
        return None  # 有意图但没有标的

    # 投资周期
    horizon = None
    for pattern, label in _HORIZON_PATTERNS:
        if re.search(pattern, msg):
            horizon = label
            break

    # agentic 模式关键词
    mode = "standard"
    if any(kw in msg for kw in ["agent", "深度", "全面", "详细", "agentic", "自主"]):
        mode = "agentic"

    return {"symbol": symbol, "horizon": horizon, "mode": mode}


# ---------- 流式对话（SSE） ----------

def stream_chat(session_id: int, user_id: int, message: str):
    """流式对话：agent 执行过程实时推送工具调用事件（SSE）。

    事件格式（data: JSON）：
      {"type":"tool_start","name":"get_quote","args":{...}}
      {"type":"tool_end","name":"get_quote","preview":"..."}
      {"type":"msg","content":"..."}        最终回复
      {"type":"error","message":"..."}
      {"type":"done","session_id":N}
    """
    # 注册 session→user_id 映射，供工具（如 search_my_research）跨线程使用
    from .tools import _set_session_user
    _set_session_user(session_id, user_id)

    _init_db()
    save_message(session_id, "user", message)

    # 意图识别：检测是否是分析请求（如"调研茅台短线"）
    intent = _detect_analysis_intent(message)

    profile = get_profile(user_id)
    memories = get_user_memories(user_id)
    agent = build_agent(profile=profile, memories=memories)

    if agent is None:
        reply = "还没有配置大模型 API Key。请先到「模型配置」页填写（支持 DeepSeek/OpenAI/通义/Ollama 等任意 OpenAI 兼容服务）。"
        save_message(session_id, "assistant", reply)
        yield _sse({"type": "msg", "content": reply})
        yield _sse({"type": "done", "session_id": session_id})
        return

    # 如果识别到分析意图，优先自动触发投研分析（不等LLM自行决定是否调用run_research）
    if intent:
        symbol = intent["symbol"]
        horizon = intent.get("horizon")
        mode = intent.get("mode", "standard")
        topic = f"{horizon}分析" if horizon else ""

        yield _sse({
            "type": "tool_start",
            "name": "run_research",
            "args": {"symbol": symbol, "topic": topic, "mode": mode},
            "intent": True,
        })

        try:
            from .pipeline import run_analysis
            result = run_analysis(symbol, topic or None, mode=mode, user_id=user_id)

            # 生成摘要推送给前端
            name = result.get("name", symbol)
            score = result.get("consensus_score", 0)
            verdict = result.get("consensus_verdict", "")
            tp = result.get("trade_plan")
            action = tp.get("action", "") if tp else ""
            price = result.get("price")
            change = result.get("change_pct")

            summary_parts = [f"已完成 {name}({symbol}) 的投研分析"]
            if horizon:
                summary_parts.append(f"周期: {horizon}")
            if price:
                summary_parts.append(f"当前价 {price}")
            if change is not None:
                summary_parts.append(f"涨跌 {change}%")
            summary_parts.append(f"共识评分 {score:.1f}")
            summary_parts.append(f"结论: {verdict}")
            if action:
                summary_parts.append(f"建议: {action}")
            analysis_summary = "，".join(summary_parts)

            yield _sse({
                "type": "tool_end",
                "name": "run_research",
                "preview": analysis_summary[:120],
                "analysis": result,
            })
            tool_calls = [{"name": "run_research", "args": {"symbol": symbol, "topic": topic, "mode": mode}}]
        except Exception as e:
            yield _sse({"type": "tool_end", "name": "run_research", "preview": f"分析失败: {e}"})
            analysis_summary = f"分析失败: {e}"
            tool_calls = []

        # 分析完成后，让LLM基于分析结果做进一步解读
        # 把分析摘要拼入消息，让LLM做自然语言解读
        enhanced_msg = (
            f"{message}\n\n"
            f"[系统已完成投研分析，结果如下]\n"
            f"{analysis_summary}\n"
            f"请基于以上分析结果，给用户做简洁的自然语言解读。"
        )
        message_for_agent = enhanced_msg
    else:
        message_for_agent = message
        tool_calls = []

    reply = ""
    try:
        for event in agent.stream(
            {"messages": [HumanMessage(content=message_for_agent)]},
            config={"configurable": {"thread_id": str(session_id)}},
        ):
            for _node, value in event.items():
                msgs = value.get("messages", [])
                if not msgs:
                    continue
                # 并行工具调用时 tools 节点可能含多个消息，逐个处理
                for m in msgs:
                    tcs = getattr(m, "tool_calls", None)
                    if tcs:
                        for tc in tcs:
                            name = tc.get("name", "")
                            args = tc.get("args", {})
                            tool_calls.append({"name": name, "args": args})
                            yield _sse({"type": "tool_start", "name": name, "args": args})
                    elif getattr(m, "type", "") == "tool":
                        # 工具执行完成（ToolMessage）：标记对应步骤 done
                        yield _sse({"type": "tool_end", "name": "", "preview": str(m.content)[:120]})
                    elif getattr(m, "type", "") == "ai" and m.content:
                        # 最终回复（无工具调用的 AIMessage）：先存，循环结束后分块输出
                        reply = str(m.content)
    except Exception as e:
        reply = f"对话处理失败: {e}"
        yield _sse({"type": "error", "message": str(e)})

    # 回复分块流式输出（打字机效果）：chunk 逐块，msg 为完整回复
    if reply:
        for chunk in _chunk_text(reply):
            yield _sse({"type": "chunk", "content": chunk})
            time.sleep(0.03)  # 30ms/块，打字机节奏
        yield _sse({"type": "msg", "content": reply})

    save_message(session_id, "assistant", reply, tool_calls)
    try:
        extract_memories(user_id, message, reply, session_id)
    except Exception:
        pass
    if len(get_messages(session_id, user_id)) <= 2:
        rename_session(session_id, message[:12] + ("..." if len(message) > 12 else ""))
    yield _sse({"type": "done", "session_id": session_id})


def _chunk_text(text: str, size: int = 12) -> list[str]:
    """把文本切成小块用于流式输出（打字机效果）。"""
    if not text:
        return []
    chunks = []
    i = 0
    n = len(text)
    while i < n:
        # 优先在标点后断块，让流式输出更自然
        end = min(i + size, n)
        if end < n:
            for punct in ("。", "！", "？", "\n", ".", "!", "?", "，", ","):
                idx = text.rfind(punct, i + 8, end)
                if idx != -1:
                    end = idx + 1
                    break
        chunks.append(text[i:end])
        i = end
    return chunks


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


# ---------- 对话 ----------

def chat(session_id: int, user_id: int, message: str) -> dict[str, Any]:
    """处理一轮对话：后端状态（checkpointer）管理上下文 + 长期记忆注入。

    返回 {reply, tool_calls, session_id}。
    """
    # 注册 session→user_id 映射
    from .tools import _set_session_user
    _set_session_user(session_id, user_id)

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
        from .data.fetcher import get_stock_brief
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
        from .data.fetcher import get_stock_brief
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
