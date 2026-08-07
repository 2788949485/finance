"""路由模块: analysis"""
from __future__ import annotations
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..auth import get_profile
from ..config import get_config, save_config, PROVIDER_PRESETS
from ..deps import get_current_user, require_admin

router = APIRouter()

from .. import auth
from ..pipeline import run_analysis
from ..models import AnalysisRequest
from ..data import fetcher as datalayer
from ..config import get_config, save_config
from ..llm import LLMClient
import json, time


@router.post("/api/analysis")
def create_analysis(req: AnalysisRequest, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    ticker = req.ticker.strip()
    if not ticker:
        raise HTTPException(status_code=400, detail="请输入股票代码或名称（如 600519 / hk00700 / usAAPL）")
    resolved = _resolve_ticker(ticker)
    if not resolved:
        raise HTTPException(status_code=400, detail=f"无法识别 {ticker}")
    try:
        return run_analysis(resolved, req.topic, user_id=user["id"], mode=req.mode)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"分析失败: {e}")


# 节点名 -> 中文标签映射（SSE 推送给前端展示）
_NODE_LABELS = {
    "collect_data": "数据收集",
    "run_analyst": "分析师研判",
    "aggregate_views": "汇总观点",
    "debate_node": "多空辩论",
    "consensus_node": "形成共识",
    "risk_node": "风控审查",
    "trader_node": "制定交易计划",
    "abstain": "风险规避",
    "finalize": "报告生成",
}



@router.post("/api/analysis/stream")
def stream_analysis(req: AnalysisRequest, user: dict[str, Any] = Depends(get_current_user)):
    """投研分析 SSE 流式：逐节点推送进展 + 最终结果。"""
    ticker = req.ticker.strip()
    if not ticker:
        raise HTTPException(status_code=400, detail="请输入股票代码或名称")
    resolved = _resolve_ticker(ticker)
    if not resolved:
        raise HTTPException(status_code=400, detail=f"无法识别 {ticker}")
    ticker = resolved

    def _sse(obj: dict) -> str:
        import json
        return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

    def _generate():
        try:
            yield _sse({"type": "step", "node": "collect_data", "label": "数据收集", "status": "running"})
            config: dict[str, Any] = {"configurable": {}}
            state: dict[str, Any] = {"ticker": ticker, "topic": req.topic, "user_id": user["id"]}

            for chunk in _GRAPH.stream(state, config=config, stream_mode="updates"):
                for node_name, node_output in chunk.items():
                    label = _NODE_LABELS.get(node_name, node_name)
                    # 推送步骤进展
                    yield _sse({"type": "step", "node": node_name, "label": label, "status": "done"})

                    # 如果是分析师节点，推送观点摘要
                    if node_name == "run_analyst" and isinstance(node_output, dict):
                        vm = node_output.get("view_map", {})
                        for role, view in vm.items():
                            yield _sse({
                                "type": "analyst",
                                "role": role,
                                "title": getattr(view, "title", role),
                                "summary": getattr(view, "summary", ""),
                                "score": getattr(view, "score", 0),
                            })

                    # 风控审查结果推送（Human-in-the-loop：前端展示确认按钮）
                    if node_name == "risk_node" and isinstance(node_output, dict):
                        review = node_output.get("risk_review")
                        if review:
                            yield _sse({
                                "type": "risk_review",
                                "approved": getattr(review, "approved", True),
                                "verdict": getattr(review, "verdict", ""),
                                "max_position_pct": getattr(review, "max_position_pct", 0),
                                "stop_loss_pct": getattr(review, "stop_loss_pct", 0),
                            })

                    # finalize 节点推送最终结果
                    if node_name == "finalize" and isinstance(node_output, dict):
                        result = node_output.get("result")
                        if result:
                            yield _sse({"type": "result", "data": result})

            yield _sse({"type": "done"})
        except Exception as e:
            yield _sse({"type": "error", "message": str(e)})

    return StreamingResponse(_generate(), media_type="text/event-stream")



@router.get("/api/analysis/{analysis_id}")
def get_one(analysis_id: int, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    row = memory.get_analysis(analysis_id)
    if row is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    return row



@router.get("/api/history")
def history(limit: int = 20, user: dict[str, Any] = Depends(get_current_user)) -> list[dict[str, Any]]:
    return memory.list_analyses(limit=min(limit, 100), user_id=user["id"])



@router.delete("/api/history/{analysis_id}")
def delete_history(analysis_id: int, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, str]:
    ok = memory.delete_analysis(analysis_id, user_id=user["id"])
    return {"status": "ok" if ok else "not_found"}


# ---------- 行情 K 线 ----------


@router.get("/api/analysts")
def list_analysts() -> list[dict[str, str]]:
    """返回所有可用分析师列表。"""
    from .agents.analysts import ALL_ANALYSTS
    return [{"role": cls.role, "title": cls.title, "description": getattr(cls, "__doc__", "")} for cls in ALL_ANALYSTS]


