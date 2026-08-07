"""路由模块: thesis"""
from __future__ import annotations
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..auth import get_profile
from ..config import get_config, save_config, PROVIDER_PRESETS
from ..deps import get_current_user, require_admin

router = APIRouter()

from ..thesis_tracker import list_theses, create_thesis, update_thesis, delete_thesis, check_thesis, check_all_active_theses, list_thesis_checks, detect_thesis_drift
from ..llm import LLMClient


@router.get("/api/theses")
def list_theses_api(
    status: str = "all",
    ticker: str = "",
    user: dict[str, Any] = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """列出用户的投资论文。status=active/invalidated/all。"""
    from .thesis_tracker import list_theses
    return list_theses(user["id"], status, ticker or None)



@router.post("/api/theses")
def create_thesis_api(
    body: dict[str, Any],
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """创建投资论文。body: {ticker, name, thesis_text, key_assumptions[], invalidation_conditions[], score, horizon}"""
    from .thesis_tracker import create_thesis
    return create_thesis(
        user["id"],
        ticker=body.get("ticker", ""),
        name=body.get("name", ""),
        thesis_text=body.get("thesis_text", ""),
        key_assumptions=body.get("key_assumptions"),
        invalidation_conditions=body.get("invalidation_conditions"),
        score=body.get("score", 0),
        horizon=body.get("horizon", ""),
    )



@router.put("/api/theses/{thesis_id}")
def update_thesis_api(
    thesis_id: int,
    body: dict[str, Any],
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """更新投资论文。"""
    from .thesis_tracker import update_thesis
    return update_thesis(
        thesis_id, user["id"],
        thesis_text=body.get("thesis_text"),
        key_assumptions=body.get("key_assumptions"),
        invalidation_conditions=body.get("invalidation_conditions"),
        score=body.get("score"),
        status=body.get("status"),
        invalidation_reason=body.get("invalidation_reason"),
    )



@router.delete("/api/theses/{thesis_id}")
def delete_thesis_api(
    thesis_id: int,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, str]:
    """删除投资论文。"""
    from .thesis_tracker import delete_thesis
    ok = delete_thesis(thesis_id, user["id"])
    return {"ok": "deleted" if ok else "not_found"}



@router.post("/api/theses/{thesis_id}/check")
def check_thesis_api(
    thesis_id: int,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """手动触发证伪检查。"""
    from .thesis_tracker import check_thesis
    from .llm import LLMClient
    return check_thesis(thesis_id, LLMClient())



@router.post("/api/theses/check-all")
def check_all_theses_api(
    user: dict[str, Any] = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """批量检查所有active论文。"""
    from .thesis_tracker import check_all_active_theses
    from .llm import LLMClient
    return check_all_active_theses(user["id"], LLMClient())



@router.get("/api/theses/{thesis_id}/checks")
def list_thesis_checks_api(
    thesis_id: int,
    limit: int = 10,
    user: dict[str, Any] = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """查看论文的检查历史。"""
    from .thesis_tracker import list_thesis_checks
    return list_thesis_checks(thesis_id, limit)



@router.get("/api/thesis-drift/{ticker}")
def thesis_drift_api(
    ticker: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """论文漂移检测：对比同一标的最近的两次分析。"""
    from .thesis_tracker import detect_thesis_drift
    from .llm import LLMClient
    result = detect_thesis_drift(ticker, LLMClient())
    if result is None:
        raise HTTPException(404, f"需要至少2次{ticker}的分析记录才能做漂移检测")
    return result


# ==================== 投研知识库 ====================


