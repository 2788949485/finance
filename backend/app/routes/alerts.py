"""路由模块: alerts"""
from __future__ import annotations
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..auth import get_profile
from ..config import get_config, save_config, PROVIDER_PRESETS
from ..deps import get_current_user, require_admin

router = APIRouter()

from .. import alert


@router.get("/api/alerts")
def list_alerts_api(user: dict[str, Any] = Depends(get_current_user), status: str = "all") -> list[dict[str, Any]]:
    """列出用户的预警规则。status: active/triggered/all。"""
    return alert.list_alerts(user["id"], status=status)



@router.post("/api/alerts")
async def create_alert_api(request: Request, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    """创建价格预警。

    body: {symbol, symbol_name, alert_type, threshold}
    alert_type: price_above / price_below / change_pct_up / change_pct_down
    """
    body = await request.json()
    symbol = (body.get("symbol") or "").strip()
    if not symbol:
        raise HTTPException(400, "请提供股票代码")
    resolved = _resolve_ticker(symbol)
    if not resolved:
        raise HTTPException(400, f"无法识别 {symbol}")
    alert_type = body.get("alert_type", "")
    if alert_type not in ("price_above", "price_below", "change_pct_up", "change_pct_down"):
        raise HTTPException(400, "alert_type 必须为 price_above/price_below/change_pct_up/change_pct_down")
    threshold = float(body.get("threshold", 0))
    if threshold <= 0:
        raise HTTPException(400, "阈值必须大于0")
    symbol_name = body.get("symbol_name", "")
    if not symbol_name:
        brief = datalayer.get_stock_brief(resolved)
        symbol_name = brief.get("name", resolved) if brief else resolved
    return alert.create_alert(user["id"], resolved, symbol_name, alert_type, threshold)



@router.delete("/api/alerts/{alert_id}")
def delete_alert_api(alert_id: int, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, str]:
    """删除预警规则。"""
    ok = alert.delete_alert(alert_id, user["id"])
    return {"status": "ok" if ok else "not_found"}



@router.post("/api/alerts/{alert_id}/reactivate")
def reactivate_alert_api(alert_id: int, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, str]:
    """重新激活已触发的预警（re-arm），支持重复触发。"""
    ok = alert.reactivate_alert(alert_id, user["id"])
    return {"status": "ok" if ok else "not_found"}



@router.post("/api/alerts/check")
def check_alerts_api() -> dict[str, Any]:
    """扫描所有 active 预警（定时轮询触发），返回新触发的预警列表。

    前端每30秒轮询此端点，收到触发的预警后弹出通知。
    """
    triggered = alert.check_alerts()
    return {"triggered": triggered, "count": len(triggered)}


# ---------- 智能对话 ----------

