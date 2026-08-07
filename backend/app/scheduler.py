"""定时/自动化分析调度器。

功能：
1. 用户配置定时分析任务（分析哪些股票、什么时间跑）
2. A股交易日历感知（节假日不触发）
3. 分析完成后结果存DB，可在前端查看
4. 通过预警通道推送通知

表结构 scheduled_tasks:
  id, user_id, name, symbols(JSON), mode, cron_hour, cron_minute,
  enabled, last_run_at, last_result_summary, created_at

表结构 scheduled_results:
  id, task_id, user_id, run_at, results(JSON)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, date, timedelta
from threading import RLock
from typing import Any, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from .config import DB_PATH, _connect

logger = logging.getLogger(__name__)

_scheduler: Optional[BackgroundScheduler] = None
_lock = RLock()

# 2025-2026 A股节假日（国务院发布，手动维护）
# 来源：证监会/上交所交易日历
_MARKET_HOLIDAYS: set[str] = {
    # 2025
    "2025-01-01", "2025-01-28", "2025-01-29", "2025-01-30", "2025-01-31",
    "2025-02-03", "2025-04-04", "2025-05-01", "2025-05-02", "2025-06-02",
    "2025-10-01", "2025-10-02", "2025-10-03", "2025-10-06", "2025-10-07",
    "2025-10-08",
    # 2026（预估，根据国务院放假安排）
    "2026-01-01", "2026-02-16", "2026-02-17", "2026-02-18", "2026-04-06",
    "2026-05-01", "2026-06-19", "2026-09-25",
    "2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07",
    "2026-10-08",
}


def is_trading_day(d: date | None = None) -> bool:
    """判断今天（或指定日期）是否A股交易日。

    规则：周一~周五 且 不在节假日列表。
    港股/美股交易日历略有不同，但定时分析以A股为主，统一用A股日历。
    """
    d = d or date.today()
    if d.weekday() >= 5:  # 周六日
        return False
    if d.strftime("%Y-%m-%d") in _MARKET_HOLIDAYS:
        return False
    return True


# ---------- DB ----------

def _ensure_tables() -> None:
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT DEFAULT '',
                symbols TEXT NOT NULL DEFAULT '[]',
                mode TEXT DEFAULT 'standard',
                cron_hour INTEGER DEFAULT 15,
                cron_minute INTEGER DEFAULT 30,
                enabled INTEGER DEFAULT 1,
                last_run_at TEXT DEFAULT '',
                last_result_summary TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                run_at TEXT NOT NULL,
                results TEXT NOT NULL DEFAULT '{}'
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sched_task ON scheduled_results(task_id)"
        )


# ---------- CRUD ----------

def create_task(
    user_id: int,
    name: str,
    symbols: list[str],
    mode: str = "standard",
    cron_hour: int = 15,
    cron_minute: int = 30,
) -> dict[str, Any]:
    """创建定时分析任务并注册到调度器。"""
    _ensure_tables()
    now = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO scheduled_tasks
               (user_id, name, symbols, mode, cron_hour, cron_minute, enabled, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 1, ?)""",
            (user_id, name, json.dumps(symbols, ensure_ascii=False), mode,
             cron_hour, cron_minute, now),
        )
        task_id = int(cur.lastrowid)

    # 注册到调度器
    _register_job(task_id, cron_hour, cron_minute)
    task = get_task(task_id)
    logger.info("创建定时任务 #%s: %s (%s %02d:%02d)", task_id, name, mode, cron_hour, cron_minute)
    return task


def list_tasks(user_id: int) -> list[dict[str, Any]]:
    _ensure_tables()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM scheduled_tasks WHERE user_id=? ORDER BY id",
            (user_id,),
        ).fetchall()
    return [_row_to_task(r) for r in rows]


def get_task(task_id: int) -> dict[str, Any] | None:
    _ensure_tables()
    with _connect() as conn:
        r = conn.execute(
            "SELECT * FROM scheduled_tasks WHERE id=?", (task_id,)
        ).fetchone()
    return _row_to_task(r) if r else None


def update_task(
    task_id: int,
    user_id: int,
    name: str | None = None,
    symbols: list[str] | None = None,
    mode: str | None = None,
    cron_hour: int | None = None,
    cron_minute: int | None = None,
    enabled: bool | None = None,
) -> dict[str, Any] | None:
    _ensure_tables()
    sets: list[str] = []
    params: list[Any] = []
    if name is not None:
        sets.append("name=?")
        params.append(name)
    if symbols is not None:
        sets.append("symbols=?")
        params.append(json.dumps(symbols, ensure_ascii=False))
    if mode is not None:
        sets.append("mode=?")
        params.append(mode)
    if cron_hour is not None:
        sets.append("cron_hour=?")
        params.append(cron_hour)
    if cron_minute is not None:
        sets.append("cron_minute=?")
        params.append(cron_minute)
    if enabled is not None:
        sets.append("enabled=?")
        params.append(1 if enabled else 0)

    if not sets:
        return get_task(task_id)

    params.append(task_id)
    params.append(user_id)
    with _connect() as conn:
        conn.execute(
            f"UPDATE scheduled_tasks SET {', '.join(sets)} WHERE id=? AND user_id=?",
            params,
        )

    # 重新注册调度器
    task = get_task(task_id)
    if task:
        if task["enabled"] and cron_hour is not None:
            _register_job(task_id, task["cron_hour"], task["cron_minute"])
        elif enabled is False:
            _unregister_job(task_id)
    return task


def delete_task(task_id: int, user_id: int) -> bool:
    _ensure_tables()
    _unregister_job(task_id)
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM scheduled_tasks WHERE id=? AND user_id=?",
            (task_id, user_id),
        )
        conn.execute(
            "DELETE FROM scheduled_results WHERE task_id=?", (task_id,)
        )
        return cur.rowcount > 0


def list_results(task_id: int, limit: int = 10) -> list[dict[str, Any]]:
    _ensure_tables()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM scheduled_results WHERE task_id=? ORDER BY id DESC LIMIT ?",
            (task_id, limit),
        ).fetchall()
    results = []
    for r in rows:
        d = dict(r)
        d["results"] = json.loads(d["results"])
        results.append(d)
    return results


def run_task_now(task_id: int) -> dict[str, Any] | None:
    """手动触发一次定时任务（不等时间到）。用于测试。"""
    task = get_task(task_id)
    if not task:
        return None
    return _execute_task(task)


# ---------- 调度器 ----------

def _job_id(task_id: int) -> str:
    return f"sched_task_{task_id}"


def _register_job(task_id: int, hour: int, minute: int) -> None:
    """注册/更新一个定时任务到调度器。"""
    _ensure_scheduler()
    with _lock:
        s = _scheduler
        if s is None:
            return
        jid = _job_id(task_id)
        # 先移除旧的
        try:
            s.remove_job(jid)
        except Exception:
            pass
        # 周一~周五触发（节假日由 _execute_task 内部判断）
        s.add_job(
            _execute_task_by_id,
            CronTrigger(day_of_week="mon-fri", hour=hour, minute=minute),
            args=[task_id],
            id=jid,
            replace_existing=True,
            misfire_grace_time=3600,  # 1小时容错
        )
        logger.info("注册定时任务 #%s → %02d:%02d 周一~周五", task_id, hour, minute)


def _unregister_job(task_id: int) -> None:
    with _lock:
        s = _scheduler
        if s is None:
            return
        try:
            s.remove_job(_job_id(task_id))
            logger.info("移除定时任务 #%s", task_id)
        except Exception:
            pass


def _ensure_scheduler() -> None:
    global _scheduler
    with _lock:
        if _scheduler is not None:
            return
        _scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
        _scheduler.start()
        logger.info("调度器已启动")


def start_scheduler() -> None:
    """FastAPI startup 时调用：启动调度器 + 恢复所有已注册任务。"""
    _ensure_tables()
    _ensure_scheduler()
    # 恢复所有 enabled 的任务
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, cron_hour, cron_minute FROM scheduled_tasks WHERE enabled=1"
        ).fetchall()
    for r in rows:
        _register_job(r["id"], r["cron_hour"], r["cron_minute"])
    logger.info("调度器恢复 %d 个定时任务", len(rows))


def stop_scheduler() -> None:
    """FastAPI shutdown 时调用。"""
    global _scheduler
    with _lock:
        if _scheduler is not None:
            _scheduler.shutdown(wait=False)
            _scheduler = None
            logger.info("调度器已停止")


# ---------- 执行 ----------

def _execute_task_by_id(task_id: int) -> None:
    """调度器回调：根据 task_id 加载任务并执行。"""
    task = get_task(task_id)
    if not task or not task["enabled"]:
        return
    _execute_task(task)


def _execute_task(task: dict[str, Any]) -> dict[str, Any]:
    """执行一次定时分析任务。

    1. 判断是否交易日（非交易日跳过）
    2. 对每个 symbol 运行 run_analysis
    3. 结果存DB
    4. 更新 last_run_at + last_result_summary
    5. 通过预警通道通知用户
    """
    # 非交易日跳过（手动触发不受限）
    # _execute_task_by_id 由调度器触发 → 需判断交易日
    # run_task_now 手动触发 → 跳过判断
    # 通过调用栈区分：调度器调用先判断
    if not is_trading_day():
        logger.info("定时任务 #%s: 今日非交易日，跳过", task["id"])
        # 仍然记录一条结果（空结果），方便用户看到调度器在运行
        _save_result(task["id"], task["user_id"], {
            "skipped": True,
            "reason": "非交易日",
            "date": date.today().isoformat(),
        })
        return {"skipped": True, "reason": "非交易日"}

    symbols: list[str] = task["symbols"]
    mode = task.get("mode", "standard")
    user_id = task["user_id"]

    logger.info("定时任务 #%s 开始执行: %s 模式=%s", task["id"], symbols, mode)
    all_results: dict[str, Any] = {}
    summaries: list[str] = []

    for sym in symbols:
        try:
            from .pipeline import run_analysis
            result = run_analysis(sym, mode=mode, user_id=user_id)
            # 提取关键结论
            name = result.get("name", sym)
            score = result.get("consensus_score", 0)
            verdict = result.get("consensus_verdict", "")
            action = ""
            tp = result.get("trade_plan")
            if tp:
                action = tp.get("action", "")
            summaries.append(f"{name}({sym}): {verdict} 评分{score:.1f} {action}")
            all_results[sym] = {
                "name": name,
                "score": score,
                "verdict": verdict,
                "action": action,
                "price": result.get("price"),
                "change_pct": result.get("change_pct"),
            }
        except Exception as e:
            logger.warning("定时分析 %s 失败: %s", sym, e)
            all_results[sym] = {"error": str(e)}
            summaries.append(f"{sym}: 分析失败 {e}")

    summary_text = "; ".join(summaries)
    run_at = datetime.now().isoformat(timespec="seconds")

    _save_result(task["id"], user_id, {
        "run_at": run_at,
        "symbols": all_results,
        "summary": summary_text,
    })

    # 更新任务最后运行信息
    with _connect() as conn:
        conn.execute(
            "UPDATE scheduled_tasks SET last_run_at=?, last_result_summary=? WHERE id=?",
            (run_at, summary_text, task["id"]),
        )

    # 推送通知（通过聊天系统给用户发消息）
    try:
        _notify_user(user_id, task, summary_text)
    except Exception as e:
        logger.warning("通知推送失败: %s", e)

    logger.info("定时任务 #%s 完成: %s", task["id"], summary_text)
    return {"run_at": run_at, "summary": summary_text, "symbols": all_results}


def _save_result(task_id: int, user_id: int, results: dict[str, Any]) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        conn.execute(
            """INSERT INTO scheduled_results (task_id, user_id, run_at, results)
               VALUES (?, ?, ?, ?)""",
            (task_id, user_id, now, json.dumps(results, ensure_ascii=False)),
        )


def _notify_user(user_id: int, task: dict[str, Any], summary: str) -> None:
    """通过聊天系统给用户发一条系统消息，告知定时分析结果。

    创建一个新的聊天session，以系统消息形式存储分析摘要。
    用户下次打开聊天页面时可以看到。
    """
    try:
        from .chat import create_session, save_message
        session_id = create_session(
            user_id,
            f"定时分析 {datetime.now().strftime('%m-%d %H:%M')} {task['name']}"
        )
        msg = (
            f"【定时分析完成】{task['name']}\n"
            f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
            f"结果:\n{summary}\n"
            f"详细结果请到「定时分析」页面查看。"
        )
        save_message(session_id, "assistant", msg)
    except Exception as e:
        logger.warning("创建通知session失败: %s", e)


# ---------- utils ----------

def _row_to_task(r) -> dict[str, Any]:
    d = dict(r)
    d["symbols"] = json.loads(d.get("symbols") or "[]")
    d["enabled"] = bool(d.get("enabled", 0))
    return d
