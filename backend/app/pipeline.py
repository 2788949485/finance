"""编排流水线：基于 LangGraph 状态图的薄封装。

完整流程由 app.graph 定义：
  collect_data -> [5×run_analyst 并行] -> aggregate_views -> debate
  -> consensus -> risk -> (批准: trader | 否决: abstain) -> finalize

对外保持 run_analysis(ticker, topic) 签名，API 层无需改动。
"""
from __future__ import annotations

from typing import Any

from .graph.builder import build_graph
from .llm import LLMClient

# 编译一次，全局复用（LangGraph 图可被多次 invoke）
_GRAPH = build_graph()


def run_analysis(ticker: str, topic: str | None = None, llm: LLMClient | None = None) -> dict[str, Any]:
    """执行完整投研流水线，返回 AnalysisResult 结构字典。

    llm 参数用于测试注入（如 mock 无 key 的客户端）；生产环境省略。
    """
    config: dict[str, Any] = {"configurable": {"llm": llm}} if llm else {}
    state = _GRAPH.invoke({"ticker": ticker, "topic": topic}, config=config)
    return state["result"]
