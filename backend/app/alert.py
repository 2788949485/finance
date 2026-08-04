"""价格预警系统：用户设置价格/涨跌幅/技术指标预警，触发后推送通知。

表结构 alerts:
  id, user_id, symbol, symbol_name, alert_type, threshold, operator,
  status(active/triggered/expired), message, created_at, triggered_at

预警类型:
  price_above / price_below        -- 价格突破/跌破
  change_pct_up / change_pct_down  -- 当日涨跌幅超阈值
  ma_cross_up / ma_cross_down      -- 均线金叉/死叉（MA5穿越MA20）
  volume_surge                     -- 放量突破（量比超阈值倍数）
"""
from __future__ import annotations

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
                triggered_at TEXT,
                trigger_count INTEGER DEFAULT 0
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
                ma_cross_up / ma_cross_down / volume_surge
    threshold: 价格/百分比/量比倍数
    """
    _ensure_table()
    operator = ">=" if alert_type in ("price_above", "change_pct_up", "ma_cross_up", "volume_surge") else "<="
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO alerts
               (user_id, symbol, symbol_name, alert_type, threshold, operator, status, created_at, trigger_count)
               VALUES (?, ?, ?, ?, ?, ?, 'active', ?, 0)""",
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


def reactivate_alert(alert_id: int, user_id: int) -> bool:
    """重新激活已触发的预警（re-arm），支持重复触发。"""
    _ensure_table()
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE alerts SET status='active', message='', triggered_at=NULL WHERE id=? AND user_id=?",
            (alert_id, user_id),
        )
        return cur.rowcount > 0


def _mark_triggered(alert_id: int, message: str) -> None:
    """标记预警已触发。"""
    _ensure_table()
    with _connect() as conn:
        conn.execute(
            "UPDATE alerts SET status='triggered', message=?, triggered_at=?, trigger_count=trigger_count+1 WHERE id=?",
            (message, datetime.now().isoformat(timespec="seconds"), alert_id),
        )


def _check_technical_alerts(symbol: str, atype: str, threshold: float) -> tuple[bool, str]:
    """检查技术指标类预警（均线交叉/放量突破）。

    返回 (是否触发, 消息)。需要K线数据。
    """
    from .data import fetcher as datalayer

    hist = datalayer.get_history(symbol, days=30)
    if hist is None or len(hist) < 21:
        return False, ""

    closes = hist["close"].tolist()
    vols = hist["volume"].tolist()

    if atype == "ma_cross_up":
        # MA5上穿MA20（昨天MA5<MA20，今天MA5>MA20）
        if len(closes) < 22:
            return False, ""
        ma5_today = sum(closes[-5:]) / 5
        ma20_today = sum(closes[-20:]) / 20
        ma5_yest = sum(closes[-6:-1]) / 5
        ma20_yest = sum(closes[-21:-1]) / 20
        if ma5_yest <= ma20_yest and ma5_today > ma20_today:
            return True, f"MA5({ma5_today:.2f})上穿MA20({ma20_today:.2f})，金叉信号"
        return False, ""

    if atype == "ma_cross_down":
        if len(closes) < 22:
            return False, ""
        ma5_today = sum(closes[-5:]) / 5
        ma20_today = sum(closes[-20:]) / 20
        ma5_yest = sum(closes[-6:-1]) / 5
        ma20_yest = sum(closes[-21:-1]) / 20
        if ma5_yest >= ma20_yest and ma5_today < ma20_today:
            return True, f"MA5({ma5_today:.2f})下穿MA20({ma20_today:.2f})，死叉信号"
        return False, ""

    if atype == "volume_surge":
        # 今日量比 = 今天成交量 / 前5日均量
        if len(vols) < 6:
            return False, ""
        today_vol = vols[-1]
        avg5_vol = sum(vols[-6:-1]) / 5
        if avg5_vol <= 0:
            return False, ""
        vol_ratio = today_vol / avg5_vol
        if vol_ratio >= threshold:
            return True, f"放量突破！今日量比 {vol_ratio:.1f}倍（阈值{threshold}倍）"
        return False, ""

    return False, ""


def check_alerts() -> list[dict[str, Any]]:
    """扫描所有 active 预警，检查是否触发。返回触发的预警列表。

    被 /api/alerts/check 端点调用（前端轮询或定时触发）。
    优化：按symbol分组批量查询，避免N+1。
    """
    from .data import fetcher as datalayer

    _ensure_table()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM alerts WHERE status='active'"
        ).fetchall()

    if not rows:
        return []

    # 按 symbol 分组，减少重复查询
    symbol_set: dict[str, list[dict]] = {}
    for row in rows:
        alert = dict(row)
        sym = alert["symbol"]
        symbol_set.setdefault(sym, []).append(alert)

    # 批量获取行情：每个symbol只查一次
    brief_cache: dict[str, Optional[dict]] = {}
    for sym in symbol_set:
        try:
            brief_cache[sym] = datalayer.get_stock_brief(sym, fresh=True)
        except Exception:
            brief_cache[sym] = None

    triggered: list[dict[str, Any]] = []
    for sym, alert_list in symbol_set.items():
        brief = brief_cache.get(sym)
        for alert in alert_list:
            atype = alert["alert_type"]
            threshold = alert["threshold"]
            name = alert.get("symbol_name") or sym
            hit = False
            msg = ""

            # 价格类预警
            if atype in ("price_above", "price_below"):
                if not brief:
                    continue
                price = brief.get("price")
                if price is None:
                    continue
                if atype == "price_above" and price >= threshold:
                    hit, msg = True, f"{name} 突破 {threshold}，现价 {price}"
                elif atype == "price_below" and price <= threshold:
                    hit, msg = True, f"{name} 跌破 {threshold}，现价 {price}"

            # 涨跌幅预警
            elif atype in ("change_pct_up", "change_pct_down"):
                if not brief:
                    continue
                chg = brief.get("change_pct")
                if chg is None:
                    continue
                if atype == "change_pct_up" and chg >= threshold:
                    hit, msg = True, f"{name} 涨幅达 {chg:+.2f}%，超过预警 {threshold}%"
                elif atype == "change_pct_down" and chg <= -threshold:
                    hit, msg = True, f"{name} 跌幅达 {chg:+.2f}%，超过预警 -{threshold}%"

            # 技术指标预警
            elif atype in ("ma_cross_up", "ma_cross_down", "volume_surge"):
                try:
                    hit, msg = _check_technical_alerts(sym, atype, threshold)
                    if hit:
                        msg = f"{name} {msg}"
                except Exception:
                    pass

            if hit:
                _mark_triggered(alert["id"], msg)
                alert["current_price"] = brief.get("price") if brief else None
                alert["message"] = msg
                alert["status"] = "triggered"
                triggered.append(alert)

    return triggered
