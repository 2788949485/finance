"""财务数据与龙虎榜（同花顺财务摘要 / 东财龙虎榜 / yfinance港股美股）。

从原 a_stock.py 拆分而来；函数签名、行为、返回值均未改变。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from .utils import (
    AK_AVAILABLE,
    TTL,
    _norm_symbol,
    _parse_num,
    _safe,
    _to_float,
    ak,
    cached,
)


def get_financials(symbol: str) -> Optional[dict[str, Any]]:
    """财务摘要：A股用同花顺接口，港股/美股用 yfinance。缓存 24 小时。"""
    sym = _norm_symbol(symbol)

    # 港股/美股：用 yfinance
    if sym.startswith("hk") or sym.startswith("us"):
        def _fetch_yf() -> Optional[dict[str, Any]]:
            try:
                import yfinance as yf
                # 转换代码：hk00700 -> 0700.HK，usAAPL -> AAPL
                if sym.startswith("hk"):
                    yf_sym = sym[2:] + ".HK"
                else:
                    yf_sym = sym[2:]
                info = yf.Ticker(yf_sym).info
                if not info:
                    return None
                return {
                    "period": "最新报告期",
                    "revenue": info.get("totalRevenue"),
                    "revenue_yoy": None,
                    "net_profit": info.get("netIncomeToCommon"),
                    "net_profit_yoy": None,
                    "roe": round(info.get("returnOnEquity", 0) * 100, 2) if info.get("returnOnEquity") else None,
                    "gross_margin": round(info.get("grossMargins", 0) * 100, 2) if info.get("grossMargins") else None,
                    "debt_ratio": info.get("debtToEquity"),
                }
            except Exception:
                return None
        return cached(f"financials:{sym}", TTL["financials"], _fetch_yf)

    # A股：同花顺接口
    if not AK_AVAILABLE:
        return None

    def _fetch() -> Optional[dict[str, Any]]:
        df = _safe(ak.stock_financial_abstract_ths, symbol=sym, indicator="按报告期")
        if df is None or df.empty:
            return None
        recent = df.iloc[-1]
        out: dict[str, Any] = {"period": str(recent.get("报告期", ""))}
        for key, col in [
            ("revenue", "营业总收入"),
            ("revenue_yoy", "营业总收入同比增长率"),
            ("net_profit", "净利润"),
            ("net_profit_yoy", "净利润同比增长率"),
            ("roe", "净资产收益率"),
            ("gross_margin", "销售毛利率"),
            ("debt_ratio", "资产负债率"),
        ]:
            out[key] = _parse_num(recent.get(col))
        return out

    return cached(f"financials:{sym}", TTL["financials"], _fetch)


def get_lhb(symbol: str, days: int = 30) -> Optional[dict[str, Any]]:
    """最近龙虎榜记录，缓存 6 小时。"""
    if not AK_AVAILABLE:
        return None
    sym = _norm_symbol(symbol)

    def _fetch() -> Optional[dict[str, Any]]:
        end = datetime.now()
        start = end - timedelta(days=days)
        df = _safe(
            ak.stock_lhb_detail_em,
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
        )
        if df is None or df.empty:
            return None
        rows = df[df["代码"].astype(str).str.zfill(6) == sym]
        if rows.empty:
            return None
        recent = rows.sort_values("上榜日", ascending=False).iloc[0]
        return {
            "date": str(recent.get("上榜日", "")),
            "reason": str(recent.get("上榜原因", "")),
            "net_buy": _to_float(recent.get("龙虎榜净买额")),
            "buy_total": _to_float(recent.get("龙虎榜买入额")),
            "sell_total": _to_float(recent.get("龙虎榜卖出额")),
        }

    return cached(f"lhb:{sym}", TTL["lhb"], _fetch)
