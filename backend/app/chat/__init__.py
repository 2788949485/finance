"""对话式智能体：LangGraph ReAct agent + 后端状态（checkpointer）+ 长短记忆。

架构（参考 LangGraph 官方 memory 模式）：
- 短期记忆：SqliteSaver checkpointer，thread_id=session_id，
  对话状态（消息+工具调用链）由后端持久化，前端无需重发历史
- 长期记忆：user_memories 表，对话后自动提取用户事实（关注标的/偏好），
  下次对话注入 system prompt（mem0 思路）
- 会话可删除：删消息表 + checkpoint

本包由原 chat.py（单文件 820 行）拆分为多模块，向后兼容：
所有原 `from app.chat import xxx` 与 `from app import chat; chat.xxx()` 用法继续可用。

模块划分：
- prompts:   系统提示词（SYSTEM_PROMPT / MEMORY_EXTRACT_PROMPT）
- db:        数据库 CRUD + agent 构建（_connect/_init_db/build_agent/会话CRUD等）
- memory:    长期记忆管理（extract_memories/get_user_memories 等）
- intent:    意图识别（_detect_analysis_intent）
- peers:     行业同行管理（get_peers/auto_generate_peers 等）
- streaming: SSE 流式 + 非流式对话（stream_chat/chat）
"""
from __future__ import annotations

# prompts
from .prompts import MEMORY_EXTRACT_PROMPT, SYSTEM_PROMPT

# db：连接/初始化/CRUD/agent 构建
from .db import (
    _code_name,
    _connect,
    _init_db,
    _make_prompt,
    _new_checkpointer,
    build_agent,
    create_session,
    delete_session,
    get_messages,
    list_sessions,
    rename_session,
    save_message,
    search_messages,
)

# memory
from .memory import (
    _cleanup_stock_memories,
    _get_existing_stock_codes,
    _memory_exists,
    _parse_json_list,
    extract_memories,
    get_user_memories,
)

# intent
from .intent import _HORIZON_PATTERNS, _INTENT_KEYWORDS, _detect_analysis_intent

# peers
from .peers import (
    auto_generate_peers,
    delete_peers,
    get_peers,
    list_industry_peers,
    save_peers,
)

# streaming / chat
from .streaming import _chunk_text, _sse, chat, stream_chat

__all__ = [
    # prompts
    "SYSTEM_PROMPT",
    "MEMORY_EXTRACT_PROMPT",
    # db / CRUD
    "_connect",
    "_init_db",
    "_new_checkpointer",
    "_code_name",
    "_make_prompt",
    "build_agent",
    "create_session",
    "list_sessions",
    "search_messages",
    "delete_session",
    "get_messages",
    "save_message",
    "rename_session",
    # memory
    "get_user_memories",
    "extract_memories",
    "_cleanup_stock_memories",
    "_get_existing_stock_codes",
    "_memory_exists",
    "_parse_json_list",
    # intent
    "_INTENT_KEYWORDS",
    "_HORIZON_PATTERNS",
    "_detect_analysis_intent",
    # peers
    "get_peers",
    "auto_generate_peers",
    "list_industry_peers",
    "save_peers",
    "delete_peers",
    # streaming / chat
    "stream_chat",
    "_chunk_text",
    "_sse",
    "chat",
]
