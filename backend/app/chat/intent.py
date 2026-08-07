"""意图识别（轻量前置层）。

纯关键词规则检测分析意图，无 LLM 调用。
"""
from __future__ import annotations

import re
from typing import Any

from ..tools import COMPANY_ALIASES, HK_ALIASES, US_ALIASES, resolve_symbol

# 触发分析意图的关键词
# 明确的分析意图关键词（模糊词如"怎么样""看看"不触发——避免普通提问启动耗时分析）
_INTENT_KEYWORDS = [
    "分析", "调研", "研究", "诊断", "评估",
    "投研", "报告", "分析一下", "深度分析", "全面分析",
    "短线", "中线", "长线", "波段",
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
