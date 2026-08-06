"""交易后反思学习闭环。

记录每次分析师决策，N 个交易日后结算实际收益，调用 LLM 反思判断质量，
将经验教训注入下次分析 prompt，形成"决策 → 结算 → 反思 → 反哺"的闭环。

设计原则：
- 所有异常都不阻塞主投研流程（reflect 失败 ≠ 分析失败）
- LLM 无 key 时返回 mock 数据，闭环仍可运转（仅反思文本为占位）
- 收益计算用腾讯 K 线接口（与 datalayer.get_history 同源），CSI300 指数用 sh000300
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Optional

from . import data as datalayer
from .config import _connect
from .llm import LLMClient

logger = logging.getLogger(__name__)

# 沪深300指数在腾讯接口的市场前缀（指数走 sh，不能用 datalayer 默认的 sz）
_CSI300_SYMBOL = "000300"

# ---------- 1. 记录决策 ----------

def record_decision(
    ticker: str,
    role: str,
    score: float,
    summary: str,
    decision_date: str | None = None,
) -> int:
    """记录一条决策到 reflection_memos（status=pending）。

    Args:
        ticker: 股票代码
        role: 分析师角色 macro/fundamental/tech/sentiment/capital/consensus
        score: 当时的评分 -10~+10
        summary: 当时的摘要
        decision_date: 决策日期 YYYY-MM-DD，缺省今天

    Returns:
        插入行的 id；失败返回 -1。
    """
    if decision_date is None:
        decision_date = datetime.now().strftime("%Y-%m-%d")
    try:
        score = float(score) if score is not None else 0.0
    except (TypeError, ValueError):
        score = 0.0
    summary = (summary or "")[:2000]
    try:
        with _connect() as conn:
            cur = conn.execute(
                """INSERT INTO reflection_memos
                   (ticker, role, decision_date, decision_score, decision_summary, status)
                   VALUES (?, ?, ?, ?, ?, 'pending')""",
                (ticker, role, decision_date, score, summary),
            )
            return int(cur.lastrowid)
    except Exception as e:
        logger.warning("record_decision 失败 ticker=%s role=%s: %s", ticker, role, e)
        return -1


# ---------- 2. 结算 pending 决策 ----------

def settle_pending(
    ticker: str,
    llm: LLMClient | None = None,
    settlement_days: int = 5,
    force: bool = False,
) -> int:
    """结算某 ticker 的 pending 决策。

    流程：
    1. 查所有 pending 记录
    2. 对每条：decision_date + settlement_days <= today 才结算
       （force=True 时跳过时间检查，立即结算）
    3. 计算个股 raw_return 与相对沪深300的 alpha_return
    4. LLM 反思下结论（correct/wrong/unclear）
    5. 更新 status=settled

    Returns:
        本次结算的条数。
    """
    today = datetime.now().date()
    cutoff = today - timedelta(days=settlement_days)
    settled_count = 0

    try:
        with _connect() as conn:
            rows = conn.execute(
                """SELECT id, role, decision_date, decision_score, decision_summary
                   FROM reflection_memos
                   WHERE ticker = ? AND status = 'pending'""",
                (ticker,),
            ).fetchall()
    except Exception as e:
        logger.warning("settle_pending 查询失败 ticker=%s: %s", ticker, e)
        return 0

    if llm is None:
        try:
            llm = LLMClient()
        except Exception:
            llm = None

    for row in rows:
        try:
            dec_date_str = row["decision_date"]
            dec_date = datetime.strptime(dec_date_str, "%Y-%m-%d").date()
            if not force and dec_date > cutoff:
                # 还没到结算窗口，跳过
                continue
            raw_ret, alpha_ret = _calc_return(ticker, dec_date_str, settlement_days)
            verdict, reflection = _llm_reflect(
                llm,
                ticker=ticker,
                role=row["role"],
                score=row["decision_score"] or 0.0,
                summary=row["decision_summary"] or "",
                raw_return=raw_ret,
                alpha_return=alpha_ret,
                decision_date=dec_date_str,
            )
            with _connect() as conn:
                conn.execute(
                    """UPDATE reflection_memos
                       SET raw_return = ?, alpha_return = ?, reflection = ?,
                           verdict = ?, settled_at = ?, status = 'settled'
                       WHERE id = ?""",
                    (
                        raw_ret,
                        alpha_ret,
                        reflection,
                        verdict,
                        datetime.now().isoformat(timespec="seconds"),
                        row["id"],
                    ),
                )
            settled_count += 1
        except Exception as e:
            logger.warning(
                "settle_pending 单条结算失败 id=%s ticker=%s: %s",
                row["id"],
                ticker,
                e,
            )
            continue
    return settled_count


# ---------- 3. 查询反思记录 ----------

def get_recent_memos(ticker: str, limit: int = 5) -> list[dict]:
    """获取某 ticker 最近的已结算反思记录（用于注入下次分析）。"""
    try:
        with _connect() as conn:
            rows = conn.execute(
                """SELECT ticker, role, decision_date, decision_score,
                          decision_summary, raw_return, alpha_return,
                          reflection, verdict, settled_at
                   FROM reflection_memos
                   WHERE ticker = ? AND status = 'settled'
                   ORDER BY settled_at DESC
                   LIMIT ?""",
                (ticker, limit),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning("get_recent_memos 失败 ticker=%s: %s", ticker, e)
        return []


def get_cross_ticker_memos(limit: int = 3) -> list[dict]:
    """获取跨 ticker 的反思记录（不同股票的经验教训）。"""
    try:
        with _connect() as conn:
            rows = conn.execute(
                """SELECT ticker, role, decision_date, decision_score,
                          raw_return, alpha_return, reflection, verdict
                   FROM reflection_memos
                   WHERE status = 'settled'
                   ORDER BY settled_at DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning("get_cross_ticker_memos 失败: %s", e)
        return []


# ---------- 4. 构建 prompt 记忆块 ----------

def _fmt_view(score: float) -> str:
    """评分转中文方向标签。"""
    if score >= 4:
        return "看多"
    if score <= -4:
        return "看空"
    if score > 0:
        return "偏多"
    if score < 0:
        return "偏空"
    return "中性"


def _fmt_verdict(v: str) -> str:
    return {"correct": "判断正确", "wrong": "判断错误", "unclear": "方向模糊"}.get(
        v or "", v or ""
    )


def build_memory_block(ticker: str) -> str:
    """构建注入 prompt 的反思记忆块。

    返回空字符串表示无可用记忆（调用方应直接跳过注入）。
    """
    memos = get_recent_memos(ticker, limit=5)
    cross = get_cross_ticker_memos(limit=3)
    if not memos and not cross:
        return ""

    lines: list[str] = ["【历史决策反思】"]
    if memos:
        lines.append(f"该股票最近 {len(memos)} 次决策反思：")
        for m in memos:
            score = m.get("decision_score") or 0.0
            raw = m.get("raw_return")
            alpha = m.get("alpha_return")
            verdict = _fmt_verdict(m.get("verdict", ""))
            date = m.get("decision_date", "")
            reflection = (m.get("reflection") or "").strip().split("\n")[0][:80]
            ret_str = (
                f"实际涨跌 {raw:+.1f}%（超额 {alpha:+.1f}%）"
                if raw is not None and alpha is not None
                else "收益数据缺失"
            )
            lines.append(
                f"- {date} {_fmt_view(score)}(评分{score:+.0f})：{ret_str}，{verdict}。"
                f"反思：{reflection}"
            )
    if cross:
        lines.append("")
        lines.append("跨股票经验：")
        # 按 ticker 聚合统计正确率
        stats: dict[str, dict[str, int]] = {}
        role_stats: dict[str, dict[str, int]] = {}
        for c in cross:
            tk = c.get("ticker", "?")
            v = c.get("verdict", "")
            stats.setdefault(tk, {"correct": 0, "wrong": 0, "total": 0})
            stats[tk]["total"] += 1
            if v == "correct":
                stats[tk]["correct"] += 1
            elif v == "wrong":
                stats[tk]["wrong"] += 1
            rl = c.get("role", "?")
            role_stats.setdefault(rl, {"correct": 0, "total": 0})
            role_stats[rl]["total"] += 1
            if v == "correct":
                role_stats[rl]["correct"] += 1
        for tk, s in list(stats.items())[:3]:
            if s["total"]:
                lines.append(
                    f"- {tk}：历史 {s['total']} 次决策，{s['correct']} 次正确"
                    f"（准确率 {s['correct'] / s['total']:.0%}）"
                )
        for rl, s in list(role_stats.items())[:2]:
            if s["total"]:
                lines.append(
                    f"- {rl} 分析师：{s['correct']}/{s['total']} 次判断正确"
                )
    return "\n".join(lines)


# ---------- 5. 收益计算 ----------

def _calc_return(ticker: str, start_date: str, days: int) -> tuple[float, float]:
    """计算个股 days 天后的 raw_return 和 alpha_return（相对沪深300）。

    Returns:
        (raw_return_pct, alpha_return_pct)。数据缺失时对应项返回 0.0。
    """
    raw = _calc_stock_return(ticker, start_date, days)
    bench = _calc_stock_return(_CSI300_SYMBOL, start_date, days, is_index=True)
    alpha = round(raw - bench, 2) if (raw is not None and bench is not None) else 0.0
    return (raw if raw is not None else 0.0, alpha)


def _calc_stock_return(
    ticker: str, start_date: str, days: int, is_index: bool = False
) -> Optional[float]:
    """计算从 start_date 起 days 个交易日后的涨跌幅（%）。

    策略：拉取足够长的 K 线，定位 start_date 当天收盘价作为基准，
    取之后第 `days` 个交易日的收盘价计算收益率。
    """
    try:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None
    # 多拉一些天数确保覆盖 start_date + days 个交易日
    fetch_days = max(days + 60, 120)
    try:
        if is_index and ticker == _CSI300_SYMBOL:
            df = _fetch_csi300_history(fetch_days)
        else:
            df = datalayer.get_history(ticker, days=fetch_days)
    except Exception as e:
        logger.debug("_calc_stock_return 拉取失败 %s: %s", ticker, e)
        return None
    if df is None or len(df) == 0:
        return None
    try:
        df = df.copy()
        df["date_only"] = df["date"].dt.date if hasattr(df["date"].dt, "date") else df["date"]
    except Exception:
        df["date_only"] = df["date"]

    # 找 start_date 当天或之前最近的一条作为基准
    before = df[df["date_only"] <= start_dt]
    if before.empty:
        # start_date 早于所有数据，用最早一条
        base_row = df.iloc[0]
    else:
        base_row = before.iloc[-1]
    base_close = float(base_row["close"])

    # 找基准之后的第 days 个交易日
    after = df[df["date_only"] > (base_row["date_only"])]
    if len(after) <= days - 1:
        # 数据不足，用最后一条
        end_row = df.iloc[-1]
    else:
        end_row = after.iloc[days - 1]
    end_close = float(end_row["close"])

    if base_close <= 0:
        return None
    return round((end_close - base_close) / base_close * 100, 2)


def _fetch_csi300_history(days: int):
    """拉取沪深300指数 K 线（腾讯接口，指数用 sh 前缀）。

    datalayer.get_history 对 000300 会错误地用 sz 前缀（首位 0 → sz），
    指数实际需要 sh，所以这里单独请求。
    """
    import requests

    code = f"sh{_CSI300_SYMBOL}"
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,,,{days},qfq"
    try:
        import pandas as pd

        r = requests.get(url, timeout=15)
        data = r.json()
        node = data["data"][code]
        key = "qfqday" if "qfqday" in node else "day"
        rows = node[key]
        bars = []
        for row in rows:
            try:
                bars.append(
                    {
                        "date": str(row[0]),
                        "open": float(row[1]),
                        "close": float(row[2]),
                        "high": float(row[3]),
                        "low": float(row[4]),
                        "volume": float(row[5]),
                    }
                )
            except (ValueError, IndexError):
                continue
        if not bars:
            return None
        df = pd.DataFrame(bars)
        df["date"] = pd.to_datetime(df["date"])
        return df.sort_values("date").reset_index(drop=True)
    except Exception as e:
        logger.debug("_fetch_csi300_history 失败: %s", e)
        return None


# ---------- 6. LLM 反思 ----------

def _llm_reflect(
    llm: LLMClient | None,
    ticker: str,
    role: str,
    score: float,
    summary: str,
    raw_return: float,
    alpha_return: float,
    decision_date: str = "",
) -> tuple[str, str]:
    """LLM 反思一条决策。

    Returns:
        (verdict: 'correct'|'wrong'|'unclear', reflection_text: 2-4 句中文反思)
    """
    direction = "看多" if score >= 0 else "看空"
    system = (
        "你是交易复盘教练。根据分析师当时的判断和后续实际走势，"
        "评估判断质量并总结经验。只输出 JSON。"
    )
    user = (
        f"标的 {ticker}，{role} 分析师在 {decision_date} 给出评分 {score:+.1f}"
        f"（{direction}）。\n"
        f"当时摘要：{summary[:300]}\n"
        f"5 个交易日后实际涨跌：{raw_return:+.2f}%，相对沪深300超额：{alpha_return:+.2f}%。\n\n"
        "请评估：\n"
        "1) 方向是否正确（看多实际涨=正确，看空实际跌=正确）\n"
        "2) 强度是否合理（评分绝对值与实际涨跌幅是否匹配）\n"
        "3) 总结 2 句经验教训\n\n"
        '输出 JSON：{"verdict": "correct"|"wrong"|"unclear", "reflection": "反思文字"}'
    )

    if llm is None:
        return _fallback_reflect(score, raw_return)

    try:
        data = llm.chat_json(system, user)
        verdict = str(data.get("verdict", "")).strip().lower()
        if verdict not in ("correct", "wrong", "unclear"):
            verdict = _fallback_reflect(score, raw_return)[0]
        reflection = str(data.get("reflection", "")).strip()
        if not reflection or reflection.startswith("（模拟"):
            verdict2, reflection = _fallback_reflect(score, raw_return)
            if not reflection:
                reflection = verdict2
        return verdict, reflection[:500]
    except Exception as e:
        logger.debug("_llm_reflect LLM 调用失败: %s", e)
        return _fallback_reflect(score, raw_return)


def _fallback_reflect(score: float, raw_return: float) -> tuple[str, str]:
    """无 LLM 或调用失败时的规则化反思。"""
    bullish = score >= 0
    correct_dir = (bullish and raw_return > 0) or (not bullish and raw_return < 0)
    verdict = "correct" if correct_dir else ("wrong" if abs(raw_return) > 1 else "unclear")
    mag = abs(raw_return)
    strength = abs(score)
    if correct_dir:
        reflection = (
            f"方向判断正确，实际{'涨' if raw_return > 0 else '跌'} {mag:.1f}%。"
            + ("评分强度合理。" if (strength >= 4) == (mag >= 2) else "评分强度可再校准。")
        )
    else:
        reflection = (
            f"方向判断错误，实际{'涨' if raw_return > 0 else '跌'} {mag:.1f}%，"
            "需复盘信号失效原因。"
        )
    return verdict, reflection
