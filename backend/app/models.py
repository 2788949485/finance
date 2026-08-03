"""Pydantic 数据模型：配置、分析请求、分析结果。"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class LLMConfig(BaseModel):
    provider: str = "deepseek"
    base_url: str = "https://api.deepseek.com/v1"
    api_key: str = ""
    model: str = "deepseek-chat"
    temperature: float = 0.3
    max_tokens: int = 4096


class AnalysisRequest(BaseModel):
    ticker: str = Field(..., description="A股代码，如 600519 或 000001")
    topic: Optional[str] = Field(None, description="可选：分析主题/事件")


class AnalystView(BaseModel):
    """单个角色的分析结论。"""
    role: str
    title: str
    summary: str
    score: float = Field(..., description="看多评分 -10~+10")
    evidence: list[str] = Field(default_factory=list)
    risk_points: list[str] = Field(default_factory=list)


class DebateRound(BaseModel):
    """辩论记录。"""
    topic: str
    positions: list[str] = Field(default_factory=list)


class RiskReview(BaseModel):
    approved: bool
    verdict: str
    max_position_pct: float = 0.0
    stop_loss_pct: float = 0.0


class TradePlan(BaseModel):
    action: str = Field(..., description="买入/卖出/观望/回避")
    target_price: Optional[float] = None
    stop_loss: Optional[float] = None
    position_pct: float = 0.0
    reasoning: str = ""
    risk_warnings: list[str] = Field(default_factory=list)


class AnalysisResult(BaseModel):
    id: Optional[int] = None
    ticker: str
    name: str = ""
    price: Optional[float] = None
    created_at: str = ""
    status: str = "completed"
    consensus_score: float = 0.0
    consensus_verdict: str = ""
    analyst_views: list[AnalystView] = Field(default_factory=list)
    debate: list[DebateRound] = Field(default_factory=list)
    risk_review: Optional[RiskReview] = None
    trade_plan: Optional[TradePlan] = None
    disclaimer: str = ""
    raw: dict[str, Any] = Field(default_factory=dict)
