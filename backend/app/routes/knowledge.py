"""路由模块: knowledge"""
from __future__ import annotations
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..auth import get_profile
from ..config import get_config, save_config, PROVIDER_PRESETS
from ..deps import get_current_user, require_admin

router = APIRouter()

from ..knowledge_base import search_knowledge, get_stock_history, list_all_knowledge, get_knowledge_stats


@router.get("/api/knowledge/search")
def knowledge_search_api(
    q: str,
    limit: int = 20,
    user: dict[str, Any] = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """搜索用户的历史投研分析。"""
    from .knowledge_base import search_knowledge
    return search_knowledge(user["id"], q, limit)



@router.get("/api/knowledge/stock/{ticker}")
def knowledge_stock_api(
    ticker: str,
    limit: int = 20,
    user: dict[str, Any] = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """获取用户对某只股票的所有历史分析。"""
    from .knowledge_base import get_stock_history
    return get_stock_history(user["id"], ticker, limit)



@router.get("/api/knowledge/list")
def knowledge_list_api(
    limit: int = 50,
    offset: int = 0,
    user: dict[str, Any] = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """列出用户所有投研分析（分页）。"""
    from .knowledge_base import list_all_knowledge
    return list_all_knowledge(user["id"], limit, offset)



@router.get("/api/knowledge/stats")
def knowledge_stats_api(
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """知识库统计信息。"""
    from .knowledge_base import get_knowledge_stats
    return get_knowledge_stats(user["id"])
