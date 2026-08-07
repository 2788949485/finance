"""投资论文追踪系统（Investment Thesis Tracking）。

功能：
1. 买入/分析时记录投资论文（核心逻辑 + 关键假设 + 证伪条件）
2. 定时/手动检查证伪条件是否触发
3. 论文漂移检测：对比同一标的两次分析的核心结论变化

与 reflection_engine 的区别：
- reflection_engine = 事后收益归因（决策→等N天→算收益→反思）
- thesis_tracker = 事前逻辑追踪（买入理由→持续监控→逻辑是否被证伪）

表结构 investment_theses:
  id, user_id, ticker, name, thesis_text, key_assumptions(JSON),
  invalidation_conditions(JSON), score, horizon, status(active/invalidated/updated),
  created_at, updated_at, invalidated_at, invalidation_reason

表结构 thesis_checks:
  id, thesis_id, checked_at, status(valid/warning/invalidated),
  checks_detail(JSON), price_at_check
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, date
from typing import Any, Optional

from .config import _connect

logger = logging.getLogger(__name__)


def _ensure_tables() -> None:
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS investment_theses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                ticker TEXT NOT NULL,
                name TEXT DEFAULT '',
                thesis_text TEXT NOT NULL,
                key_assumptions TEXT DEFAULT '[]',
                invalidation_conditions TEXT DEFAULT '[]',
                score REAL DEFAULT 0,
                horizon TEXT DEFAULT '',
                status TEXT DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT DEFAULT '',
                invalidated_at TEXT DEFAULT '',
                invalidation_reason TEXT DEFAULT ''
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS thesis_checks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thesis_id INTEGER NOT NULL,
                checked_at TEXT NOT NULL,
                status TEXT DEFAULT 'valid',
                checks_detail TEXT DEFAULT '{}',
                price_at_check REAL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_thesis_user ON investment_theses(user_id, status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_thesis_check ON thesis_checks(thesis_id)"
        )


# ---------- CRUD ----------

def create_thesis(
    user_id: int,
    ticker: str,
    name: str,
    thesis_text: str,
    key_assumptions: list[str] | None = None,
    invalidation_conditions: list[str] | None = None,
    score: float = 0,
    horizon: str = "",
) -> dict[str, Any]:
    """记录一条投资论文。

    Args:
        ticker: 股票代码
        name: 股票名称
        thesis_text: 投资论文正文（为什么买入/看好）
        key_assumptions: 关键假设列表（如["白酒消费升级持续","茅台保持90%毛利率"]）
        invalidation_conditions: 证伪条件列表（如["毛利率跌破80%","销量连续2季下滑"]）
        score: 当时的共识评分 -10~+10
        horizon: 投资周期（短线/中线/长线）
    """
    _ensure_tables()
    now = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO investment_theses
               (user_id, ticker, name, thesis_text, key_assumptions,
                invalidation_conditions, score, horizon, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)""",
            (user_id, ticker, name, thesis_text,
             json.dumps(key_assumptions or [], ensure_ascii=False),
             json.dumps(invalidation_conditions or [], ensure_ascii=False),
             score, horizon, now),
        )
        thesis_id = int(cur.lastrowid)
    logger.info("创建投资论文 #%s: %s(%s)", thesis_id, name, ticker)
    return get_thesis(thesis_id)


def list_theses(
    user_id: int,
    status: str = "all",
    ticker: str | None = None,
) -> list[dict[str, Any]]:
    """列出用户的投资论文。status=active/invalidated/all。"""
    _ensure_tables()
    sql = "SELECT * FROM investment_theses WHERE user_id=?"
    params: list[Any] = [user_id]
    if status != "all":
        sql += " AND status=?"
        params.append(status)
    if ticker:
        sql += " AND ticker=?"
        params.append(ticker)
    sql += " ORDER BY id DESC"
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_thesis(r) for r in rows]


def get_thesis(thesis_id: int) -> dict[str, Any] | None:
    _ensure_tables()
    with _connect() as conn:
        r = conn.execute(
            "SELECT * FROM investment_theses WHERE id=?", (thesis_id,)
        ).fetchone()
    return _row_to_thesis(r) if r else None


def update_thesis(
    thesis_id: int,
    user_id: int,
    thesis_text: str | None = None,
    key_assumptions: list[str] | None = None,
    invalidation_conditions: list[str] | None = None,
    score: float | None = None,
    status: str | None = None,
    invalidation_reason: str | None = None,
) -> dict[str, Any] | None:
    _ensure_tables()
    sets: list[str] = []
    params: list[Any] = []
    if thesis_text is not None:
        sets.append("thesis_text=?")
        params.append(thesis_text)
    if key_assumptions is not None:
        sets.append("key_assumptions=?")
        params.append(json.dumps(key_assumptions, ensure_ascii=False))
    if invalidation_conditions is not None:
        sets.append("invalidation_conditions=?")
        params.append(json.dumps(invalidation_conditions, ensure_ascii=False))
    if score is not None:
        sets.append("score=?")
        params.append(score)
    if status is not None:
        sets.append("status=?")
        params.append(status)
        if status == "invalidated":
            sets.append("invalidated_at=?")
            params.append(datetime.now().isoformat(timespec="seconds"))
        if invalidation_reason:
            sets.append("invalidation_reason=?")
            params.append(invalidation_reason)
    if not sets:
        return get_thesis(thesis_id)
    sets.append("updated_at=?")
    params.append(datetime.now().isoformat(timespec="seconds"))
    params.append(thesis_id)
    params.append(user_id)
    with _connect() as conn:
        conn.execute(
            f"UPDATE investment_theses SET {', '.join(sets)} WHERE id=? AND user_id=?",
            params,
        )
    return get_thesis(thesis_id)


def delete_thesis(thesis_id: int, user_id: int) -> bool:
    _ensure_tables()
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM investment_theses WHERE id=? AND user_id=?",
            (thesis_id, user_id),
        )
        conn.execute("DELETE FROM thesis_checks WHERE thesis_id=?", (thesis_id,))
        return cur.rowcount > 0


# ---------- 证伪检查 ----------

def check_thesis(thesis_id: int, llm=None) -> dict[str, Any]:
    """检查一条投资论文的证伪条件是否触发。

    逻辑：
    1. 读取论文的证伪条件列表
    2. 获取最新市场数据（价格/涨跌/财务）
    3. 用LLM判断每个证伪条件是否被触发
    4. 返回检查结果，更新状态

    Returns:
        {thesis_id, status: valid/warning/invalidated, checks: [...], price}
    """
    _ensure_tables()
    thesis = get_thesis(thesis_id)
    if not thesis:
        return {"error": "论文不存在"}

    if thesis["status"] != "active":
        return {"thesis_id": thesis_id, "status": thesis["status"], "skipped": True}

    ticker = thesis["ticker"]
    conditions = thesis.get("invalidation_conditions") or []

    # 获取最新行情
    from .data import fetcher as datalayer
    brief = datalayer.get_stock_brief(ticker) or {}
    price = brief.get("price")
    change_pct = brief.get("change_pct")
    name = brief.get("name") or thesis.get("name") or ticker

    checks_detail: list[dict[str, Any]] = []
    overall_status = "valid"  # valid → warning → invalidated

    if not conditions:
        # 无明确证伪条件，用价格变动做基本判断
        score = thesis.get("score", 0)
        if score > 0 and change_pct is not None and change_pct < -10:
            overall_status = "warning"
            checks_detail.append({
                "condition": "持仓期间跌幅超过10%",
                "triggered": True,
                "detail": f"当前涨跌 {change_pct:.1f}%，买入时评分为看多({score:.1f})",
            })
        else:
            checks_detail.append({
                "condition": "无明确证伪条件",
                "triggered": False,
                "detail": f"当前价格 {price}，涨跌 {change_pct}%",
            })
    else:
        # 有明确证伪条件，用LLM判断
        for cond in conditions:
            detail = _check_condition_llm(ticker, name, cond, brief, llm)
            checks_detail.append(detail)
            if detail.get("triggered"):
                if detail.get("severity") == "high":
                    overall_status = "invalidated"
                elif overall_status == "valid":
                    overall_status = "warning"

    result = {
        "thesis_id": thesis_id,
        "ticker": ticker,
        "name": name,
        "status": overall_status,
        "checks": checks_detail,
        "price": price,
        "change_pct": change_pct,
    }

    # 记录检查结果
    now = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        conn.execute(
            """INSERT INTO thesis_checks
               (thesis_id, checked_at, status, checks_detail, price_at_check)
               VALUES (?, ?, ?, ?, ?)""",
            (thesis_id, now, overall_status,
             json.dumps(checks_detail, ensure_ascii=False), price),
        )

    # 如果被证伪，更新论文状态
    if overall_status == "invalidated":
        triggered = [c["condition"] for c in checks_detail if c.get("triggered")]
        reason = "; ".join(triggered)
        update_thesis(thesis_id, thesis["user_id"], status="invalidated", invalidation_reason=reason)
        logger.info("投资论文 #%s 被证伪: %s", thesis_id, reason)

    return result


def check_all_active_theses(user_id: int | None = None, llm=None) -> list[dict[str, Any]]:
    """批量检查所有active论文（可限定用户）。供定时任务调用。"""
    _ensure_tables()
    sql = "SELECT id FROM investment_theses WHERE status='active'"
    params: list[Any] = []
    if user_id:
        sql += " AND user_id=?"
        params.append(user_id)
    with _connect() as conn:
        ids = [r["id"] for r in conn.execute(sql, params).fetchall()]

    results = []
    for tid in ids:
        try:
            r = check_thesis(tid, llm)
            results.append(r)
        except Exception as e:
            logger.warning("检查论文 #%s 失败: %s", tid, e)
    return results


# ---------- 漂移检测 ----------

def detect_thesis_drift(ticker: str, llm=None) -> dict[str, Any] | None:
    """论文漂移检测：对比同一标的最近的两次投研分析，检测核心结论变化。

    从 analyses 表取最近两次分析结果，对比：
    - 共识评分变化（方向翻转/大幅变动）
    - 交易建议变化（买入→卖出等）
    - 关键风险点变化

    Returns:
        {ticker, date1, date2, score_drift, action_changed, new_risks, summary}
    """
    # 直接查 analyses 表取最近2条（含完整 result JSON）
    import json as _json
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, ticker, created_at, result FROM analyses "
            "WHERE ticker=? AND status='completed' "
            "ORDER BY id DESC LIMIT 2",
            (ticker,),
        ).fetchall()

    if len(rows) < 2:
        return None

    old_raw = _json.loads(rows[1]["result"]) if rows[1]["result"] else {}
    new_raw = _json.loads(rows[0]["result"]) if rows[0]["result"] else {}

    old = {
        "created_at": rows[1]["created_at"],
        "consensus_score": old_raw.get("consensus_score", 0),
        "consensus_verdict": old_raw.get("consensus_verdict", ""),
        "trade_plan": old_raw.get("trade_plan") or {},
        "analyst_views": old_raw.get("analyst_views") or [],
        "name": old_raw.get("name", ticker),
    }
    new = {
        "created_at": rows[0]["created_at"],
        "consensus_score": new_raw.get("consensus_score", 0),
        "consensus_verdict": new_raw.get("consensus_verdict", ""),
        "trade_plan": new_raw.get("trade_plan") or {},
        "analyst_views": new_raw.get("analyst_views") or [],
        "name": new_raw.get("name", ticker),
    }

    old_score = old.get("consensus_score", 0)
    new_score = new.get("consensus_score", 0)
    score_drift = new_score - old_score

    old_tp = (old.get("trade_plan") or {})
    new_tp = (new.get("trade_plan") or {})
    old_action = old_tp.get("action", "")
    new_action = new_tp.get("action", "")
    action_changed = old_action != new_action

    # 提取新增的风险点
    old_risks = set()
    for v in old.get("analyst_views", []):
        old_risks.update(v.get("risk_points", []))
    new_risks = set()
    for v in new.get("analyst_views", []):
        new_risks.update(v.get("risk_points", []))
    added_risks = list(new_risks - old_risks)
    removed_risks = list(old_risks - new_risks)

    # 评分方向翻转
    direction_flipped = (old_score > 0) != (new_score > 0)

    # LLM生成漂移摘要
    drift_summary = _llm_drift_summary(
        ticker, old, new, score_drift, action_changed, direction_flipped,
        added_risks, removed_risks, llm,
    )

    return {
        "ticker": ticker,
        "name": new.get("name", ticker),
        "date1": old.get("created_at", ""),
        "date2": new.get("created_at", ""),
        "score_drift": round(score_drift, 1),
        "direction_flipped": direction_flipped,
        "action_changed": action_changed,
        "old_action": old_action,
        "new_action": new_action,
        "added_risks": added_risks[:5],
        "removed_risks": removed_risks[:5],
        "summary": drift_summary,
    }


def list_thesis_checks(thesis_id: int, limit: int = 10) -> list[dict[str, Any]]:
    """查看某条论文的检查历史。"""
    _ensure_tables()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM thesis_checks WHERE thesis_id=? ORDER BY id DESC LIMIT ?",
            (thesis_id, limit),
        ).fetchall()
    results = []
    for r in rows:
        d = dict(r)
        d["checks_detail"] = json.loads(d.get("checks_detail") or "{}")
        results.append(d)
    return results


# ---------- LLM 辅助 ----------

def _check_condition_llm(
    ticker: str, name: str, condition: str, brief: dict, llm=None,
) -> dict[str, Any]:
    """用LLM判断单个证伪条件是否触发。"""
    if llm is None:
        from .llm import LLMClient
        llm = LLMClient()

    price = brief.get("price")
    change = brief.get("change_pct")
    turnover = brief.get("turnover")
    industry = brief.get("industry")

    system = (
        "你是投资论文证伪检查员。给定一个证伪条件和该股票的最新数据，"
        "判断该条件是否被触发。输出JSON。"
    )
    user_msg = (
        f"股票: {name}({ticker})\n"
        f"行业: {industry}\n"
        f"最新价: {price}  涨跌: {change}%  换手率: {turnover}%\n\n"
        f"证伪条件: {condition}\n\n"
        f"判断该条件是否已触发。输出JSON:\n"
        f'{{"triggered": true/false, "severity": "high/medium/low", '
        f'"detail": "判断理由(1-2句)"}}'
    )
    try:
        result = llm.chat_json(system, user_msg)
        return {
            "condition": condition,
            "triggered": bool(result.get("triggered", False)),
            "severity": result.get("severity", "low"),
            "detail": result.get("detail", ""),
        }
    except Exception as e:
        return {
            "condition": condition,
            "triggered": False,
            "severity": "low",
            "detail": f"检查失败: {e}",
        }


def _llm_drift_summary(
    ticker: str, old: dict, new: dict,
    score_drift: float, action_changed: bool, direction_flipped: bool,
    added_risks: list[str], removed_risks: list[str], llm=None,
) -> str:
    """LLM生成漂移检测摘要。"""
    if llm is None:
        from .llm import LLMClient
        llm = LLMClient()

    old_verdict = old.get("consensus_verdict", "")
    new_verdict = new.get("consensus_verdict", "")

    # 如果变化不大，不做LLM调用
    if abs(score_drift) < 1 and not action_changed and not direction_flipped and not added_risks:
        return "两次分析结论基本一致，无显著漂移。"

    system = "你是投研分析师，负责检测分析结论的漂移变化。简洁输出中文。"
    user_msg = (
        f"股票: {ticker}\n"
        f"上次分析({old.get('created_at','')[:10]}): 评分{old.get('consensus_score',0):.1f} "
        f"{old_verdict} {old.get('trade_plan',{}).get('action','')}\n"
        f"本次分析({new.get('created_at','')[:10]}): 评分{new.get('consensus_score',0):.1f} "
        f"{new_verdict} {new.get('trade_plan',{}).get('action','')}\n"
        f"评分变化: {score_drift:+.1f}\n"
        f"新增风险: {added_risks[:3]}\n"
        f"消失风险: {removed_risks[:3]}\n\n"
        f"用2-3句话总结核心变化和潜在影响。"
    )
    try:
        return llm.chat(system, user_msg)
    except Exception:
        parts = []
        if direction_flipped:
            parts.append("评分方向翻转")
        if action_changed:
            parts.append(f"交易建议从'{old.get('trade_plan',{}).get('action','')}'变为'{new.get('trade_plan',{}).get('action','')}'")
        if added_risks:
            parts.append(f"新增{len(added_risks)}个风险点")
        return "；".join(parts) + "。" if parts else "无显著漂移。"


# ---------- utils ----------

def _row_to_thesis(r) -> dict[str, Any]:
    d = dict(r)
    d["key_assumptions"] = json.loads(d.get("key_assumptions") or "[]")
    d["invalidation_conditions"] = json.loads(d.get("invalidation_conditions") or "[]")
    return d
