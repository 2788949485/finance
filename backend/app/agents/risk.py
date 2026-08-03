"""风控经理：审查共识结论，输出风控意见（可一票否决）。"""
from __future__ import annotations

from typing import Any

from ..llm import LLMClient
from ..models import RiskReview


class RiskManager:
    role = "risk"
    title = "风控经理"

    def __init__(self, llm: LLMClient | None = None):
        self.llm = llm or LLMClient()

    def review(self, context: dict[str, Any], views: list, consensus_score: float) -> RiskReview:
        """输入：全部角色观点 + 共识评分。输出：风控意见。"""
        brief = context.get("brief") or {}
        tech = context.get("tech") or {}
        views_block = "\n".join(
            f"- {v.title}: 评分 {v.score} | {v.summary[:80]}" for v in views
        )
        user_prompt = (
            f"标的: {context.get('ticker')} ({brief.get('name', '')})  现价: {brief.get('price', 'N/A')}\n"
            f"共识评分: {consensus_score}（-10看空 ~ +10看多）\n"
            f"各角色观点:\n{views_block}\n"
            f"60日低点: {tech.get('low_60d', 'N/A')}  60日高点: {tech.get('high_60d', 'N/A')}\n\n"
            "请输出JSON: {\"approved\": bool, \"verdict\": \"风控结论\", "
            "\"max_position_pct\": 建议最大仓位百分比, \"stop_loss_pct\": 建议止损百分比}\n"
            "规则：评分极端(<-6或>6)时仓位不超过10%；中性评分仓位不超过5%；"
            "存在重大风险时 approved=false。"
        )
        system = (
            "你是风控经理，负责审查交易建议。你有最终否决权。"
            "只输出JSON，不要输出其他文字。"
        )
        data = self.llm.chat_json(system, user_prompt)
        try:
            approved = bool(data.get("approved", True))
        except Exception:
            approved = True
        return RiskReview(
            approved=approved,
            verdict=str(data.get("verdict", "")),
            max_position_pct=float(data.get("max_position_pct", 0) or 0),
            stop_loss_pct=float(data.get("stop_loss_pct", 0) or 0),
        )
