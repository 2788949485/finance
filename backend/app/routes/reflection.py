"""路由模块: reflection"""
from __future__ import annotations
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..auth import get_profile
from ..config import get_config, save_config, PROVIDER_PRESETS
from ..deps import get_current_user, require_admin

router = APIRouter()

from ..reflection_engine import get_recent_memos, settle_pending
from ..llm import LLMClient


@router.get("/api/reflection/{ticker}")
def reflection_api(ticker: str) -> dict[str, Any]:
    """获取某股票的历史决策反思记录（已结算）。"""
    from .reflection_engine import get_recent_memos
    return {"ticker": ticker, "memos": get_recent_memos(ticker, limit=20)}



@router.post("/api/reflection/settle/{ticker}")
def settle_api(ticker: str, force: bool = False) -> dict[str, Any]:
    """手动触发某股票的 pending 决策结算（N 天后反思）。
    force=true 时立即结算（不等5天，用于测试/演示）。"""
    from .reflection_engine import settle_pending
    from .llm import LLMClient
    settled = settle_pending(ticker, LLMClient(), force=force)
    return {"ticker": ticker, "settled": settled}


# ==================== 定时/自动化分析 ====================


