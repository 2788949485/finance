"""LangGraph 状态定义：投研流水线的全部状态字段。"""
from __future__ import annotations

from typing import Any, Optional, TypedDict

from ..models import AnalystView, DebateRound, RiskReview, TradePlan


class AgentState(TypedDict, total=False):
    """智能体团队协作状态。total=False 允许节点按需写入部分字段。"""

    # 输入
    ticker: str
    topic: Optional[str]

    # 数据层收集结果
    context: dict[str, Any]

    # 分析师并行产出（map 阶段写入，reduce 阶段汇总）
    view_map: dict[str, AnalystView]  # role -> view
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
