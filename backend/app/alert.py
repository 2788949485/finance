"""价格预警系统：用户设置价格/涨跌幅预警，触发后推送通知。

表结构 alerts:
  id, user_id, symbol, symbol_name, alert_type, threshold, operator,
  status(active/triggered/expired), message, created_at, triggered_at

预警类型:
  price_above  -- 价格突破上阈值
  price_below  -- 价格跌破下阈值
  change_pct   -- 当日涨跌幅超阈值(正=涨幅预警 负=跌幅预警)
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any, Optional

from .config import DB_PATH


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_table() -> None:
    """创建 alerts 表（如不存在）。"""
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                symbol_name TEXT DEFAULT '',
                alert_type TEXT NOT NULL,
                threshold REAL NOT NULL,
                operator TEXT DEFAULT '',
                status TEXT DEFAULT 'active',
                message TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                triggered_at TEXT
            )
        """)


def create_alert(
    user_id: int,
    symbol: str,
    symbol_name: str,
    alert_type: str,
    threshold: float,
) -> dict[str, Any]:
    """创建预警规则。

    alert_type: price_above / price_below / change_pct_up / change_pct_down
    threshold: 价格或百分比数值
    """
    _ensure_table()
    operator = ">=" if alert_type in ("price_above", "change_pct_up") else "<="
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO alerts
               (user_id, symbol, symbol_name, alert_type, threshold, operator, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 'active', ?)""",
            (user_id, symbol, symbol_name, alert_type, threshold, operator,
             datetime.now().isoformat(timespec="seconds")),
        )
        alert_id = int(cur.lastrowid)
    return {
        "id": alert_id, "user_id": user_id, "symbol": symbol,
        "symbol_name": symbol_name, "alert_type": alert_type,
        "threshold": threshold, "status": "active",
    }


def list_alerts(user_id: int, status: str | None = None) -> list[dict[str, Any]]:
    """列出用户的预警规则。status=active/triggered/expired/all。"""
    _ensure_table()
    with _connect() as conn:
        if status and status != "all":
            rows = conn.execute(
                "SELECT * FROM alerts WHERE user_id=? AND status=? ORDER BY id DESC",
                (user_id, status),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM alerts WHERE user_id=? ORDER BY id DESC",
                (user_id,),
            ).fetchall()
    return [dict(r) for r in rows]


def delete_alert(alert_id: int, user_id: int) -> bool:
    """删除预警规则。"""
    _ensure_table()
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM alerts WHERE id=? AND user_id=?",
            (alert_id, user_id),
        )
        return cur.rowcount > 0


def update_alert_status(alert_id: int, status: str, message: str = "") -> None:
    """更新预警状态（触发/过期）。"""
    _ensure_table()
    with _connect() as conn:
        conn.execute(
            "UPDATE alerts SET status=?, message=?, triggered_at=? WHERE id=?",
            (status, message, datetime.now().isoformat(timespec="seconds"), alert_id),
        )


def check_alerts() -> list[dict[str, Any]]:
    """扫描所有 active 预警，检查是否触发。返回触发的预警列表。

    被 /api/alerts/check 端点调用（前端轮询或定时触发）。
    """
    from .data import fetcher as datalayer

    _ensure_table()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM alerts WHERE status='active'"
        ).fetchall()

    triggered: list[dict[str, Any]] = []
    for row in rows:
        alert = dict(row)
        symbol = alert["symbol"]
        atype = alert["alert_type"]
        threshold = alert["threshold"]

        # 获取实时行情
        brief = datalayer.get_stock_brief(symbol, fresh=True)
        if not brief:
            continue

        current_price = brief.get("price")
        change_pct = brief.get("change_pct")
        if current_price is None:
            continue

        hit = False
        msg = ""

        if atype == "price_above" and current_price >= threshold:
            hit = True
            msg = f"{alert.get('symbol_name', symbol)} 突破 {threshold}，现价 {current_price}"
        elif atype == "price_below" and current_price <= threshold:
            hit = True
            msg = f"{alert.get('symbol_name', symbol)} 跌破 {threshold}，现价 {current_price}"
        elif atype == "change_pct_up" and change_pct is not None and change_pct >= threshold:
            hit = True
            msg = f"{alert.get('symbol_name', symbol)} 涨幅达 {change_pct:+.2f}%，超过预警 {threshold}%"
        elif atype == "change_pct_down" and change_pct is not None and change_pct <= -threshold:
            hit = True
            msg = f"{alert.get('symbol_name', symbol)} 跌幅达 {change_pct:+.2f}%，超过预警 -{threshold}%"

        if hit:
            update_alert_status(alert["id"], "triggered", msg)
            alert["current_price"] = current_price
            alert["message"] = msg
            alert["status"] = "triggered"
            triggered.append(alert)

    return triggered
