"""编排流水线：研究 -> 辩论 -> 共识 -> 风控 -> 交易计划。

数据收集全部容错，单个数据源失败不影响整体流程。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from . import data as datalayer
from .agents.analysts import ALL_ANALYSTS
from .agents.risk import RiskManager
from .agents.trader import Trader
from .config import get_config
from .llm import LLMClient
from .memory import save_analysis
from .models import (
    AnalysisResult,
    AnalystView,
    DebateRound,
    RiskReview,
    TradePlan,
)

DISCLAIMER = (
    "本报告由 AI 智能体自动生成，仅供参考，不构成任何投资建议。"
    "市场有风险，投资需谨慎，盈亏自负。"
)


def collect_context(ticker: str) -> dict[str, Any]:
    """收集标的的全部数据，单项失败不影响整体。"""
    ctx: dict[str, Any] = {"ticker": ticker}
    ctx["brief"] = datalayer.get_stock_brief(ticker) or {}
    history = datalayer.get_history(ticker)
    ctx["tech"] = datalayer.compute_tech_signals(history) if history is not None else {"error": "行情数据不可用"}
    ctx["financials"] = datalayer.get_financials(ticker) or {}
    ctx["lhb"] = datalayer.get_lhb(ticker)
    ctx["news"] = datalayer.get_news(ticker) or []
    return ctx


def run_analysis(ticker: str, topic: str | None = None) -> AnalysisResult:
    """执行完整投研流水线。"""
    ctx = collect_context(ticker)
    llm = LLMClient()

    # 1. 独立分析（分析师团队并行观点）
    views: list[AnalystView] = []
    for agent_cls in ALL_ANALYSTS:
        agent = agent_cls(llm)
        try:
            view = agent.analyze(ctx)
        except Exception as e:
            view = AnalystView(
                role=agent_cls.role, title=agent_cls.title,
                summary=f"分析异常: {e}", score=0,
            )
        views.append(view)

    # 2. 辩论：找出最大分歧点，生成辩论记录
    debate = _run_debate(llm, ctx, views, topic)

    # 3. 共识：等权加权评分 + 裁决
    consensus_score = round(sum(v.score for v in views) / len(views), 2) if views else 0.0
    consensus_verdict = _run_consensus(llm, ctx, views, consensus_score, topic)

    # 4. 风控审查
    risk = RiskManager(llm).review(ctx, views, consensus_score)

    # 5. 交易计划
    trade_plan = Trader(llm).plan(ctx, views, consensus_score, consensus_verdict, risk)

    result = AnalysisResult(
        ticker=ticker,
        name=(ctx.get("brief") or {}).get("name", ""),
        price=(ctx.get("brief") or {}).get("price"),
        created_at=datetime.now().isoformat(timespec="seconds"),
        status="completed",
        consensus_score=consensus_score,
        consensus_verdict=consensus_verdict,
        analyst_views=views,
        debate=debate,
        risk_review=risk,
        trade_plan=trade_plan,
        disclaimer=DISCLAIMER,
        raw={"topic": topic or ""},
    )
    result.id = save_analysis(ticker, result.model_dump())
    return result


def _run_debate(
    llm: LLMClient, ctx: dict[str, Any], views: list[AnalystView], topic: str | None
) -> list[DebateRound]:
    """辩论：找出评分分歧最大的两个角色，让 LLM 生成一轮多空交锋。"""
    if len(views) < 2:
        return []
    sorted_views = sorted(views, key=lambda v: v.score)
    bear, bull = sorted_views[0], sorted_views[-1]
    if bull.score - bear.score < 1:
        return [DebateRound(topic="观点一致性较高，未触发激烈辩论", positions=[])]
    system = (
        "你是辩论主持人。请组织看空方与看多方围绕标的展开一轮辩论，"
        "双方各陈述论据并反驳对方。只输出JSON: "
        "{\"topic\": \"辩论主题\", \"positions\": [\"看空方论点\", \"看多方论点\", \"交锋结论\"]}"
    )
    user = (
        f"标的: {ctx.get('ticker')}  主题: {topic or '常规投研'}\n"
        f"看空方（{bear.title} 评分{bear.score}）: {bear.summary}\n"
        f"看多方（{bull.title} 评分{bull.score}）: {bull.summary}\n"
        f"其他观点: {', '.join(v.title + '(' + str(v.score) + ')' for v in views if v.role not in (bear.role, bull.role))}"
    )
    data = llm.chat_json(system, user)
    return [
        DebateRound(
            topic=str(data.get("topic", "多空辩论")),
            positions=[str(p) for p in data.get("positions", [])][:5],
        )
    ]


def _run_consensus(
    llm: LLMClient, ctx: dict[str, Any], views: list[AnalystView],
    score: float, topic: str | None,
) -> str:
    """共识裁决：综合所有观点生成结论文本。"""
    views_block = "\n".join(
        f"- {v.title} ({v.score}): {v.summary[:100]}" for v in views
    )
    system = (
        "你是投研委员会主席，负责汇总各分析师观点形成最终共识结论。"
        "结论需包含：核心逻辑、主要分歧、风险提示。80-120字，简洁专业。"
    )
    user = (
        f"标的: {ctx.get('ticker')}  主题: {topic or '常规投研'}\n"
        f"综合评分: {score}/10\n观点:\n{views_block}"
    )
    return llm.chat(system, user)
