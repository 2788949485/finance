"""LangGraph 图构建：投研智能体团队协作状态机。

图结构：
  collect_data ──(Send 并行)──> run_analyst × 5 ──> aggregate_views
      ──> debate ──> consensus ──> risk ──(条件)──> trader / abstain ──> finalize

- Send API 让 5 位分析师并行执行（map-reduce）
- 条件边：风控否决时走 abstain（避险），批准时走 trader（正常交易计划）
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from .nodes import (
    aggregate_views,
    collect_data,
    fan_out_analysts,
    finalize,
    route_after_risk,
    run_abstain,
    run_analyst,
    run_consensus,
    run_debate,
    run_risk,
    run_trader,
)
from .state import AgentState


def build_graph():
    """构建并编译投研状态图。"""
    g = StateGraph(AgentState)

    g.add_node("collect_data", collect_data)
    g.add_node("run_analyst", run_analyst)
    g.add_node("aggregate_views", aggregate_views)
    g.add_node("debate", run_debate)
    g.add_node("consensus", run_consensus)
    g.add_node("risk", run_risk)
    g.add_node("trader", run_trader)
    g.add_node("abstain", run_abstain)
    g.add_node("finalize", finalize)

    g.add_edge(START, "collect_data")
    # 分析师并行：collect_data 后按角色 fan-out 为多个 run_analyst 任务
    g.add_conditional_edges("collect_data", fan_out_analysts, ["run_analyst"])
    g.add_edge("run_analyst", "aggregate_views")
    g.add_edge("aggregate_views", "debate")
    g.add_edge("debate", "consensus")
    g.add_edge("consensus", "risk")
    # 条件边：风控批准 -> trader；否决 -> abstain
    g.add_conditional_edges("risk", route_after_risk, {"trader": "trader", "abstain": "abstain"})
    g.add_edge("trader", "finalize")
    g.add_edge("abstain", "finalize")
    g.add_edge("finalize", END)

    return g.compile()
