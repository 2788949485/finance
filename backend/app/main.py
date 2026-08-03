"""FinanceCrew 后端 API 入口。

启动: uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import memory
from .config import PROVIDER_PRESETS, apply_preset, get_config, save_config
from .models import AnalysisRequest, LLMConfig
from .pipeline import run_analysis

app = FastAPI(title="FinanceCrew API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


def _mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "****"
    return key[:4] + "****" + key[-4:]


def _public_config() -> dict[str, Any]:
    cfg = get_config()
    out = dict(cfg)
    out["api_key"] = _mask_key(str(cfg.get("api_key", "")))
    return out


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/providers")
def providers() -> dict[str, Any]:
    return PROVIDER_PRESETS


@app.get("/api/config")
def read_config() -> dict[str, Any]:
    return _public_config()


@app.put("/api/config")
def write_config(cfg: LLMConfig) -> dict[str, Any]:
    saved = save_config(cfg.model_dump())
    return _public_config()


@app.post("/api/analysis")
def create_analysis(req: AnalysisRequest) -> dict[str, Any]:
    ticker = req.ticker.strip()
    if not ticker or not ticker.isdigit() or len(ticker) > 6:
        raise HTTPException(status_code=400, detail="请输入有效的A股代码（如 600519）")
    try:
        result = run_analysis(ticker.zfill(6), req.topic)
        return result  # 已是 dict 结构
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"分析失败: {e}")


@app.get("/api/analysis/{analysis_id}")
def get_one(analysis_id: int) -> dict[str, Any]:
    row = memory.get_analysis(analysis_id)
    if row is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    return row


@app.get("/api/history")
def history(limit: int = 20) -> list[dict[str, Any]]:
    return memory.list_analyses(limit=min(limit, 100))


# 前端静态托管（构建后可用）
if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")
