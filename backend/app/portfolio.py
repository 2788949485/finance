"""投资组合管理：持仓追踪、盈亏计算、仓位分配。

表结构 portfolio:
  id, user_id, symbol, symbol_name, shares, avg_cost, buy_date, note, created_at

表结构 transactions:
  id, user_id, symbol, symbol_name, action(buy/sell), shares, price, total, date, note
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


def _ensure_tables() -> None:
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS portfolio (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                symbol_name TEXT DEFAULT '',
                shares REAL NOT NULL DEFAULT 0,
                avg_cost REAL NOT NULL DEFAULT 0,
                buy_date TEXT DEFAULT '',
                note TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                UNIQUE(user_id, symbol)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                symbol_name TEXT DEFAULT '',
                action TEXT NOT NULL,
                shares REAL NOT NULL,
                price REAL NOT NULL,
                total REAL NOT NULL,
                date TEXT NOT NULL,
                note TEXT DEFAULT ''
            )
        """)


def buy_stock(
    user_id: int, symbol: str, symbol_name: str,
    shares: float, price: float, date: str = "", note: str = "",
) -> dict[str, Any]:
    """买入股票：更新持仓（加权平均成本）+ 记录交易。"""
    _ensure_tables()
    total = shares * price
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")
    now = datetime.now().isoformat(timespec="seconds")

    with _connect() as conn:
        # upsert 持仓
        row = conn.execute(
            "SELECT * FROM portfolio WHERE user_id=? AND symbol=?",
            (user_id, symbol),
        ).fetchone()
        if row:
            old = dict(row)
            new_shares = old["shares"] + shares
            new_avg = (old["shares"] * old["avg_cost"] + shares * price) / new_shares
            conn.execute(
                "UPDATE portfolio SET shares=?, avg_cost=?, symbol_name=? WHERE id=?",
                (new_shares, new_avg, symbol_name or old["symbol_name"], old["id"]),
            )
        else:
            conn.execute(
                """INSERT INTO portfolio (user_id, symbol, symbol_name, shares, avg_cost, buy_date, note, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, symbol, symbol_name, shares, price, date, note, now),
            )
        # 记录交易
        conn.execute(
            """INSERT INTO transactions (user_id, symbol, symbol_name, action, shares, price, total, date, note)
               VALUES (?, ?, ?, 'buy', ?, ?, ?, ?, ?)""",
            (user_id, symbol, symbol_name, shares, price, total, date, note),
        )
    return {"symbol": symbol, "action": "buy", "shares": shares, "price": price, "total": total}


def sell_stock(
    user_id: int, symbol: str, shares: float, price: float, date: str = "", note: str = "",
) -> dict[str, Any]:
    """卖出股票：减少持仓 + 记录交易。"""
    _ensure_tables()
    total = shares * price
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")

    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM portfolio WHERE user_id=? AND symbol=?",
            (user_id, symbol),
        ).fetchone()
        if not row:
            return {"error": "无持仓"}
        old = dict(row)
        if old["shares"] < shares:
            return {"error": f"持仓不足（当前{old['shares']}股）"}
        new_shares = old["shares"] - shares
        if new_shares <= 0.0001:
            conn.execute("DELETE FROM portfolio WHERE id=?", (old["id"],))
        else:
            conn.execute("UPDATE portfolio SET shares=? WHERE id=?", (new_shares, old["id"]))
        conn.execute(
            """INSERT INTO transactions (user_id, symbol, symbol_name, action, shares, price, total, date, note)
               VALUES (?, ?, ?, 'sell', ?, ?, ?, ?, ?)""",
            (user_id, symbol, old.get("symbol_name", symbol), shares, price, total, date, note),
        )
    return {"symbol": symbol, "action": "sell", "shares": shares, "price": price, "total": total}


def remove_position(user_id: int, symbol: str) -> bool:
    """直接删除持仓记录。"""
    _ensure_tables()
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM portfolio WHERE user_id=? AND symbol=?",
            (user_id, symbol),
        )
        return cur.rowcount > 0


def get_portfolio(user_id: int) -> dict[str, Any]:
    """获取投资组合概览：持仓列表 + 实时盈亏 + 总市值/总成本/总盈亏。"""
    from .data import fetcher as datalayer

    _ensure_tables()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM portfolio WHERE user_id=? ORDER BY id",
            (user_id,),
        ).fetchall()

    positions: list[dict[str, Any]] = []
    total_market_value = 0.0
    total_cost = 0.0
    total_pnl = 0.0

    for row in rows:
        pos = dict(row)
        brief = datalayer.get_stock_brief(pos["symbol"])
        current_price = brief.get("price") if brief else None
        if current_price:
            market_value = current_price * pos["shares"]
            cost = pos["avg_cost"] * pos["shares"]
            pnl = market_value - cost
            pnl_pct = (pnl / cost * 100) if cost > 0 else 0
            total_market_value += market_value
            total_cost += cost
            total_pnl += pnl
            pos.update({
                "current_price": current_price,
                "market_value": round(market_value, 2),
                "cost": round(cost, 2),
                "pnl": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 2),
                "change_pct": brief.get("change_pct"),
            })
        else:
            pos.update({"current_price": None, "market_value": None, "pnl": None, "pnl_pct": None})
        positions.append(pos)

    return {
        "positions": positions,
        "summary": {
            "total_market_value": round(total_market_value, 2),
            "total_cost": round(total_cost, 2),
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": round((total_pnl / total_cost * 100) if total_cost > 0 else 0, 2),
            "position_count": len(positions),
        },
    }


def list_transactions(user_id: int, limit: int = 50) -> list[dict[str, Any]]:
    """交易历史。"""
    _ensure_tables()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM transactions WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]
