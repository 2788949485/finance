"""SSE 流式对话 + 非流式对话。

stream_chat/_chunk_text/_sse/chat
"""
from __future__ import annotations

import json
import time
from typing import Any

from langchain_core.messages import HumanMessage

from ..auth import get_profile
from .db import (
    _init_db,
    build_agent,
    get_messages,
    rename_session,
    save_message,
)
from .intent import _detect_analysis_intent
from .memory import extract_memories, get_user_memories


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
    from ..tools import _set_session_user
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
            from ..pipeline import run_analysis
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
    from ..tools import _set_session_user
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
