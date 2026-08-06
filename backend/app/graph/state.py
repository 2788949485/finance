"""LangGraph 状态定义：投研流水线的全部状态字段。"""
from __future__ import annotations

from typing import Annotated, Any, Optional, TypedDict

from ..models import AnalystView, DebateRound, RiskReview, TradePlan


def _merge_view_maps(a: dict[str, AnalystView], b: dict[str, AnalystView]) -> dict[str, AnalystView]:
    """并行分析师节点的 view_map 合并器（map 阶段并发写入时使用）。"""
    return {**a, **b}


class AgentState(TypedDict, total=False):
    """智能体团队协作状态。total=False 允许节点按需写入部分字段。"""

    # 输入
    ticker: str
    topic: Optional[str]
    user_id: Optional[int]
    mode: str  # "standard" | "agentic" 分析师执行模式

    # 数据层收集结果
    context: dict[str, Any]

    # 分析师并行产出（map 阶段写入，reduce 阶段汇总）
    # Annotated reducer 允许 5 个并行 run_analyst 节点同时写 view_map
    view_map: Annotated[dict[str, AnalystView], _merge_view_maps]
    views: list[AnalystView]  # 汇总后的有序列表

    # 辩论与共识
    debate: list[DebateRound]
    consensus_score: float
    consensus_verdict: str

    # 风控与执行
    risk_review: RiskReview
    trade_plan: TradePlan

    # 最终产出
    created_at: str
    result: dict[str, Any]
