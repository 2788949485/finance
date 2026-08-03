"""交易员：基于共识与风控意见生成具体交易计划。"""
from __future__ import annotations

from typing import Any

from ..llm import LLMClient
from ..models import TradePlan


class Trader:
    role = "trader"
    title = "交易员"

    def __init__(self, llm: LLMClient | None = None):
        self.llm = llm or LLMClient()

    def plan(
        self,
        context: dict[str, Any],
        views: list,
        consensus_score: float,
        consensus_verdict: str,
        risk: Any,
    ) -> TradePlan:
        brief = context.get("brief") or {}
        tech = context.get("tech") or {}
        views_block = "\n".join(
            f"- {v.title}: 评分 {v.score} | {v.summary[:80]}" for v in views
        )
        user_prompt = (
            f"标的: {context.get('ticker')} ({brief.get('name', '')})  现价: {brief.get('price', 'N/A')}\n"
            f"共识评分: {consensus_score}  共识结论: {consensus_verdict}\n"
            f"风控意见: {risk.verdict}  批准: {'是' if risk.approved else '否'}  "
            f"最大仓位: {risk.max_position_pct}%  止损: {risk.stop_loss_pct}%\n"
            f"60日区间: {tech.get('low_60d', 'N/A')} ~ {tech.get('high_60d', 'N/A')}\n"
            f"各角色观点:\n{views_block}\n\n"
            "请输出JSON: {\"action\": \"买入/卖出/观望/回避\", \"target_price\": 目标价或null, "
            "\"stop_loss\": 止损价或null, \"position_pct\": 建议仓位百分比, "
            "\"reasoning\": \"执行逻辑(80字内)\", \"risk_warnings\": [\"风险提示\"]}\n"
            "注意A股规则：T+1、涨跌停约束；风控未批准时 action 必须为观望或回避。"
        )
        system = (
            "你是交易员，负责把投研结论转化为可执行的交易计划。"
            "必须服从风控经理的审批结果。只输出JSON，不要输出其他文字。"
        )
        data = self.llm.chat_json(system, user_prompt)
        return TradePlan(
            action=str(data.get("action", "观望")),
            target_price=_num(data.get("target_price")),
            stop_loss=_num(data.get("stop_loss")),
            position_pct=float(data.get("position_pct", 0) or 0),
            reasoning=str(data.get("reasoning", "")),
            risk_warnings=[str(w) for w in data.get("risk_warnings", [])][:4],
        )


def _num(v: Any):
    try:
        if v is None:
            return None
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None
