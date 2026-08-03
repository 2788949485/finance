"""记忆存储：分析历史记录（SQLite）。"""
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


def save_analysis(ticker: str, result: dict[str, Any], status: str = "completed") -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO analyses (ticker, created_at, status, result) VALUES (?, ?, ?, ?)",
            (ticker, datetime.now().isoformat(timespec="seconds"), status, json.dumps(result, ensure_ascii=False)),
        )
        return int(cur.lastrowid)


def update_analysis(analysis_id: int, result: dict[str, Any], status: str = "completed") -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE analyses SET status=?, result=? WHERE id=?",
            (status, json.dumps(result, ensure_ascii=False), analysis_id),
        )


def get_analysis(analysis_id: int) -> Optional[dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM analyses WHERE id=?", (analysis_id,)).fetchone()
    if row is None:
        return None
    out = dict(row)
    try:
        out["result"] = json.loads(out["result"]) if out["result"] else None
    except json.JSONDecodeError:
        out["result"] = None
    return out


def list_analyses(limit: int = 20) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, ticker, created_at, status FROM analyses ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]
