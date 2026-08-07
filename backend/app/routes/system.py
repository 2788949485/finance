"""路由模块: system"""
from __future__ import annotations
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..auth import get_profile
from ..config import get_config, save_config, PROVIDER_PRESETS
from ..deps import get_current_user, require_admin

router = APIRouter()


def _mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "****"
    return key[:4] + "****" + key[-4:]


def _public_config() -> dict[str, Any]:
    from ..config import get_config
    cfg = get_config()
    out = dict(cfg)
    out["api_key"] = _mask_key(str(cfg.get("api_key", "")))
    return out


from datetime import date
from ..llm_compare import compare_models
from ..models import LLMConfig


@router.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}



@router.get("/api/providers")
def providers() -> dict[str, Any]:
    return PROVIDER_PRESETS



@router.get("/api/config")
def read_config() -> dict[str, Any]:
    return _public_config()



@router.put("/api/config")
def write_config(cfg: LLMConfig) -> dict[str, Any]:
    save_config(cfg.model_dump())
    return _public_config()


# ---------- 认证与用户画像 ----------


@router.post("/api/llm-compare")
async def llm_compare_api(request: Request, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    """多LLM模型对比：同一prompt调用多个模型，对比回答。需要登录。
    body: {prompt, models: [{name, base_url, api_key, model}]}
    """
    body = await request.json()
    prompt = body.get("prompt", "").strip()
    models = body.get("models", [])
    if not prompt or not models:
        raise HTTPException(400, "请提供prompt和models列表")
    results = llm_compare.compare_models(prompt, models)
    return {"results": results}


