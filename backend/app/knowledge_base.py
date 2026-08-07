"""投研知识库：把用户的历史投研分析变成可检索的知识。

不引入向量数据库——投研分析是结构化的（ticker/name/verdict/score/views），
用 SQLite 直接检索更高效、更准确。

功能：
1. 按股票/关键词搜索用户的历史投研分析
2. 获取某只股票的所有历史分析摘要
3. 构建知识块注入LLM对话（让AI引用用户过去的研究结论）

数据隔离：所有查询按 user_id 过滤，用户只能看到自己的分析。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from .config import _connect

logger = logging.getLogger(__name__)


def search_knowledge(user_id: int, query: str, limit: int = 10) -> list[dict[str, Any]]:
    """搜索用户的历史投研分析。

    支持：
    - 按股票代码/名称搜索（如 "600519" "茅台"）
    - 按关键词搜索（如 "估值" "技术面" "买入"）

    Returns:
        [{id, ticker, name, created_at, consensus_score, consensus_verdict, action, summary}]
    """
    kw = f"%{query.strip()}%"
    with _connect() as conn:
        rows = conn.execute(
            """SELECT id, ticker, created_at, result FROM analyses
               WHERE user_id=? AND status='completed' AND result LIKE ?
               ORDER BY id DESC LIMIT ?""",
            (user_id, kw, limit),
        ).fetchall()

    results = []
    for r in rows:
        item = _parse_analysis(dict(r))
        if item:
            results.append(item)
    return results


def get_stock_history(user_id: int, ticker: str, limit: int = 20) -> list[dict[str, Any]]:
    """获取用户对某只股票的所有历史投研分析（按时间倒序）。"""
    with _connect() as conn:
        rows = conn.execute(
            """SELECT id, ticker, created_at, result FROM analyses
               WHERE user_id=? AND ticker=? AND status='completed'
               ORDER BY id DESC LIMIT ?""",
            (user_id, ticker, limit),
        ).fetchall()

    results = []
    for r in rows:
        item = _parse_analysis(dict(r))
        if item:
            results.append(item)
    return results


def list_all_knowledge(
    user_id: int,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """列出用户所有投研分析（分页）。"""
    with _connect() as conn:
        rows = conn.execute(
            """SELECT id, ticker, created_at, result FROM analyses
               WHERE user_id=? AND status='completed'
               ORDER BY id DESC LIMIT ? OFFSET ?""",
            (user_id, limit, offset),
        ).fetchall()

    results = []
    for r in rows:
        item = _parse_analysis(dict(r))
        if item:
            results.append(item)
    return results


def get_knowledge_stats(user_id: int) -> dict[str, Any]:
    """知识库统计：总数、覆盖股票数、最近分析时间。"""
    with _connect() as conn:
        total = conn.execute(
            "SELECT COUNT(*) as c FROM analyses WHERE user_id=? AND status='completed'",
            (user_id,),
        ).fetchone()["c"]
        tickers = conn.execute(
            "SELECT COUNT(DISTINCT ticker) as c FROM analyses WHERE user_id=? AND status='completed'",
            (user_id,),
        ).fetchone()["c"]
        latest = conn.execute(
            "SELECT MAX(created_at) as t FROM analyses WHERE user_id=? AND status='completed'",
            (user_id,),
        ).fetchone()["t"]

        # 各股票分析次数
        per_stock = conn.execute(
            """SELECT ticker, COUNT(*) as cnt, MAX(created_at) as latest
               FROM analyses WHERE user_id=? AND status='completed'
               GROUP BY ticker ORDER BY cnt DESC LIMIT 20""",
            (user_id,),
        ).fetchall()

    return {
        "total": total,
        "stock_count": tickers,
        "latest_at": latest or "",
        "top_stocks": [{"ticker": r["ticker"], "count": r["cnt"], "latest": r["latest"]} for r in per_stock],
    }


def build_knowledge_context(user_id: int, ticker: str | None = None) -> str:
    """构建注入LLM的知识上下文（让AI引用用户过去的研究）。

    格式：
    【你的历史投研】
    贵州茅台(600519) 共3次分析:
    - 2026-08-07 评分+6.2 看多 买入 "估值合理，业绩确定性强"
    - 2026-07-15 评分-2.1 中性 观望 "短期估值偏高"
    ...
    """
    if ticker:
        items = get_stock_history(user_id, ticker, limit=5)
    else:
        items = list_all_knowledge(user_id, limit=10)

    if not items:
        return ""

    # 按ticker分组
    groups: dict[str, list[dict]] = {}
    for it in items:
        groups.setdefault(it["ticker"], []).append(it)

    lines = ["【你的历史投研记录】"]
    for tk, records in groups.items():
        name = records[0].get("name", tk)
        lines.append(f"{name}({tk}) 共{len(records)}次分析:")
        for r in records[:5]:
            score = r.get("consensus_score", 0)
            verdict = (r.get("consensus_verdict") or "")[:60]
            action = r.get("action", "")
            date = r.get("created_at", "")[:10]
            lines.append(f"  - {date} 评分{score:+.1f} {action} {verdict}")
    return "\n".join(lines)


def _parse_analysis(row: dict) -> dict[str, Any] | None:
    """从 analyses 表行解析出知识条目。"""
    raw = row.get("result")
    if not raw:
        return None
    try:
        r = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return None

    tp = r.get("trade_plan") or {}
    return {
        "id": row["id"],
        "ticker": row["ticker"],
        "name": r.get("name", row["ticker"]),
        "created_at": row["created_at"],
        "consensus_score": r.get("consensus_score", 0),
        "consensus_verdict": r.get("consensus_verdict", ""),
        "action": tp.get("action", ""),
        "target_price": tp.get("target_price"),
        "stop_loss": tp.get("stop_loss"),
        "analyst_count": len(r.get("analyst_views", [])),
        "price": r.get("price"),
        "change_pct": r.get("change_pct"),
    }
