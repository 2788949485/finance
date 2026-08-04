"""LangGraph 节点：投研流水线的每一步。

- collect_data: 数据收集（容错，单项失败不阻塞）
- run_analyst: 单个分析师执行（由 Send API 并行扇出）
- aggregate_views: 汇总分析师观点
- debate / consensus / risk / trader / abstain / finalize
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.types import Send

from .. import data as datalayer
from ..agents.analysts import ALL_ANALYSTS
from ..agents.risk import RiskManager
from ..agents.trader import Trader
from ..llm import LLMClient
from ..models import AnalystView, DebateRound, RiskReview, TradePlan
from .state import AgentState

DISCLAIMER = (
    "本报告由 AI 智能体自动生成，仅供参考，不构成任何投资建议。"
    "市场有风险，投资需谨慎，盈亏自负。"
)

# 分析师角色注册表：role -> 类
ROLE_REGISTRY = {cls.role: cls for cls in ALL_ANALYSTS}
ANALYST_ORDER = [cls.role for cls in ALL_ANALYSTS]


def _get_llm(config: RunnableConfig) -> LLMClient:
    """从 graph config 取 LLM（测试可注入 mock），缺省用真实配置。"""
    return config.get("configurable", {}).get("llm") or LLMClient()


# ---------- 1. 数据收集 ----------

def collect_data(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    ctx: dict[str, Any] = {"ticker": state.get("ticker", "")}
    ctx["brief"] = datalayer.get_stock_brief(ctx["ticker"]) or {}
    history = datalayer.get_history(ctx["ticker"])
    ctx["tech"] = datalayer.compute_tech_signals(history) if history is not None else {"error": "行情数据不可用"}
    ctx["financials"] = datalayer.get_financials(ctx["ticker"]) or {}
    ctx["lhb"] = datalayer.get_lhb(ctx["ticker"])
    ctx["news"] = datalayer.get_news(ctx["ticker"]) or []
    return {"context": ctx}


# ---------- 2. 分析师并行（Send fan-out）----------

def fan_out_analysts(state: AgentState) -> list[Send]:
    """Map 阶段：为每个分析师角色分发一个 Send 任务（LangGraph 并行执行）。"""
    return [
        Send("run_analyst", {"context": state["context"], "role": role})
        for role in ANALYST_ORDER
    ]


def run_analyst(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    """单个分析师执行，产出 {role: AnalystView} 写入 view_map。"""
    role = state["role"]
    agent_cls = ROLE_REGISTRY[role]
    agent = agent_cls(_get_llm(config))
    try:
        view = agent.analyze(state["context"])
    except Exception as e:
        view = AnalystView(role=role, title=agent_cls.title, summary=f"分析异常: {e}", score=0)
    return {"view_map": {role: view}}


def aggregate_views(state: AgentState) -> dict[str, Any]:
    """Reduce 阶段：按固定顺序汇总 view_map 为 views 列表。"""
    view_map: dict[str, AnalystView] = state.get("view_map", {})
    views = [view_map[r] for r in ANALYST_ORDER if r in view_map]
    return {"views": views}


# ---------- 3. 辩论 ----------

def run_debate(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    views = state["views"]
    llm = _get_llm(config)
    if len(views) < 2:
        return {"debate": []}
    sorted_views = sorted(views, key=lambda v: v.score)
    bear, bull = sorted_views[0], sorted_views[-1]
    if bull.score - bear.score < 1:
        return {"debate": [DebateRound(topic="观点一致性较高，未触发激烈辩论", positions=[])]}
    system = (
        "你是辩论主持人。请组织看空方与看多方围绕标的展开一轮辩论，"
        "双方各陈述论据并反驳对方。只输出JSON: "
        '{"topic": "辩论主题", "positions": ["看空方论点", "看多方论点", "交锋结论"]}'
    )
    ctx = state.get("context", {})
    user = (
        f"标的: {state.get('ticker')}  主题: {state.get('topic') or '常规投研'}\n"
        f"看空方（{bear.title} 评分{bear.score}）: {bear.summary}\n"
        f"看多方（{bull.title} 评分{bull.score}）: {bull.summary}\n"
        f"其他观点: {', '.join(v.title + '(' + str(v.score) + ')' for v in views if v.role not in (bear.role, bull.role))}"
    )
    data = llm.chat_json(system, user)
    return {
        "debate": [
            DebateRound(
                topic=str(data.get("topic", "多空辩论")),
                positions=[str(p) for p in data.get("positions", [])][:5],
            )
        ]
    }


# ---------- 4. 共识 ----------

def run_consensus(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    views = state["views"]
    score = round(sum(v.score for v in views) / len(views), 2) if views else 0.0
    views_block = "\n".join(f"- {v.title} ({v.score}): {v.summary[:100]}" for v in views)
    system = (
        "你是投研委员会主席，负责汇总各分析师观点形成最终共识结论。"
        "结论需包含：核心逻辑、主要分歧、风险提示。80-120字，简洁专业。"
    )
    user = (
        f"标的: {state.get('ticker')}  主题: {state.get('topic') or '常规投研'}\n"
        f"综合评分: {score}/10\n观点:\n{views_block}"
    )
    verdict = _get_llm(config).chat(system, user)
    return {"consensus_score": score, "consensus_verdict": verdict}


# ---------- 5. 风控 ----------

def run_risk(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    review = RiskManager(_get_llm(config)).review(
        state.get("context", {}), state["views"], state["consensus_score"]
    )
    return {"risk_review": review}


# ---------- 6. 交易计划 / 避险（条件分支）----------

def route_after_risk(state: AgentState) -> str:
    """条件边：风控批准走正常交易计划，否决走避险节点。"""
    return "trader_node" if state.get("risk_review", RiskReview(approved=True, verdict="")).approved else "abstain"


def run_trader(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    plan = Trader(_get_llm(config)).plan(
        state.get("context", {}),
        state["views"],
        state["consensus_score"],
        state["consensus_verdict"],
        state["risk_review"],
    )
    return {"trade_plan": plan}


def run_abstain(state: AgentState) -> dict[str, Any]:
    """风控否决时的避险计划：不调 LLM，直接生成回避动作。"""
    plan = TradePlan(
        action="回避",
        target_price=None,
        stop_loss=None,
        position_pct=0.0,
        reasoning="风控经理否决了本次交易建议，强制规避以控制风险。",
        risk_warnings=["风控否决", "禁止开仓"],
    )
    return {"trade_plan": plan}


# ---------- 7. 组装与入库 ----------

def finalize(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    from ..memory import save_analysis

    brief = state.get("context", {}).get("brief") or {}
    result = {
        "ticker": state.get("ticker", ""),
        "name": brief.get("name", ""),
        "price": brief.get("price"),
        "change_pct": brief.get("change_pct"),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "status": "completed",
        "consensus_score": state.get("consensus_score", 0.0),
        "consensus_verdict": state.get("consensus_verdict", ""),
        "analyst_views": [v.model_dump() for v in state.get("views", [])],
        "debate": [d.model_dump() for d in state.get("debate", [])],
        "risk_review": state.get("risk_review").model_dump() if state.get("risk_review") else None,
        "trade_plan": state.get("trade_plan").model_dump() if state.get("trade_plan") else None,
        "disclaimer": DISCLAIMER,
        "raw": {"topic": state.get("topic") or ""},
    }
    result["id"] = save_analysis(result["ticker"], result, user_id=state.get("user_id"))
    return {"result": result}
