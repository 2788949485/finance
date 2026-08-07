"""路由模块: scheduler"""
from __future__ import annotations
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..auth import get_profile
from ..config import get_config, save_config, PROVIDER_PRESETS
from ..deps import get_current_user, require_admin

router = APIRouter()

from datetime import date, datetime
from ..scheduler import list_tasks, create_task, update_task, delete_task, list_results, run_task_now, is_trading_day


@router.get("/api/scheduled-tasks")
def list_scheduled_tasks(user: dict[str, Any] = Depends(get_current_user)) -> list[dict[str, Any]]:
    """列出当前用户的定时分析任务。"""
    from .scheduler import list_tasks
    return list_tasks(user["id"])



@router.post("/api/scheduled-tasks")
def create_scheduled_task(
    body: dict[str, Any],
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """创建定时分析任务。body: {name, symbols[], mode, cron_hour, cron_minute}"""
    from .scheduler import create_task
    name = body.get("name") or f"定时分析 {datetime.now().strftime('%m-%d %H:%M')}"
    symbols = body.get("symbols") or []
    if not symbols:
        from fastapi import HTTPException
        raise HTTPException(400, "至少选择一只股票")
    mode = body.get("mode", "standard")
    cron_hour = int(body.get("cron_hour", 15))
    cron_minute = int(body.get("cron_minute", 30))
    return create_task(user["id"], name, symbols, mode, cron_hour, cron_minute)



@router.put("/api/scheduled-tasks/{task_id}")
def update_scheduled_task(
    task_id: int,
    body: dict[str, Any],
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """更新定时分析任务。"""
    from .scheduler import update_task
    return update_task(
        task_id, user["id"],
        name=body.get("name"),
        symbols=body.get("symbols"),
        mode=body.get("mode"),
        cron_hour=body.get("cron_hour"),
        cron_minute=body.get("cron_minute"),
        enabled=body.get("enabled"),
    )



@router.delete("/api/scheduled-tasks/{task_id}")
def delete_scheduled_task(
    task_id: int,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, str]:
    """删除定时分析任务。"""
    from .scheduler import delete_task
    ok = delete_task(task_id, user["id"])
    return {"ok": "deleted" if ok else "not_found"}



@router.get("/api/scheduled-tasks/{task_id}/results")
def list_scheduled_results(
    task_id: int,
    limit: int = 10,
    user: dict[str, Any] = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """查看定时任务的历史执行结果。"""
    from .scheduler import list_results
    return list_results(task_id, limit)



@router.post("/api/scheduled-tasks/{task_id}/run")
def run_scheduled_task_now(
    task_id: int,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """手动触发一次定时任务（不等时间到，用于测试）。"""
    from .scheduler import run_task_now
    result = run_task_now(task_id)
    if result is None:
        from fastapi import HTTPException
        raise HTTPException(404, "任务不存在")
    return result



@router.get("/api/scheduled-tasks/trading-day")
def check_trading_day() -> dict[str, Any]:
    """查询今天是否交易日。"""
    from .scheduler import is_trading_day
    return {"trading_day": is_trading_day(), "date": date.today().isoformat()}


# ==================== 投资论文追踪 ====================


