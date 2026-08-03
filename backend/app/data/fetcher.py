"""A股数据获取层：基于 akshare（免费数据源，东方财富/同花顺/新浪）。

所有函数容错：网络异常或接口变动时返回 None/空值，保证流水线降级运行。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

import pandas as pd

try:
    import akshare as ak
    AK_AVAILABLE = True
except Exception:  # pragma: no cover
    ak = None
    AK_AVAILABLE = False


def _safe(fn, *args, **kwargs):
    """统一容错包装。"""
    try:
        return fn(*args, **kwargs)
    except Exception:
        return None


def data_available() -> bool:
    return AK_AVAILABLE


def _norm_symbol(symbol: str) -> str:
    """统一为 6 位代码。"""
    s = symbol.strip()
    if s.isdigit() and len(s) <= 6:
        return s.zfill(6)
    return s


def get_stock_brief(symbol: str) -> Optional[dict[str, Any]]:
    """个股概览：名称、现价、涨跌幅、市值、PE/PB、行业。"""
    if not AK_AVAILABLE:
        return None
    sym = _norm_symbol(symbol)
    spot = _safe(ak.stock_zh_a_spot_em)
    if spot is None or spot.empty:
        return None
    row = spot[spot["代码"] == sym]
    if row.empty:
        return None
    row = row.iloc[0]
    info = _safe(ak.stock_individual_info_em, symbol=sym)
    industry, name = "", ""
    if info is not None and not info.empty:
        kv = dict(zip(info["item"], info["value"]))
        industry = str(kv.get("行业", ""))
        name = str(kv.get("股票简称", row.get("名称", "")))
    return {
        "symbol": sym,
        "name": name or str(row.get("名称", "")),
        "price": _to_float(row.get("最新价")),
        "change_pct": _to_float(row.get("涨跌幅")),
        "market_cap": _to_float(row.get("总市值")),
        "pe": _to_float(row.get("市盈率-动态")),
        "pb": _to_float(row.get("市净率")),
        "turnover": _to_float(row.get("换手率")),
        "industry": industry,
    }


def get_history(symbol: str, days: int = 250) -> Optional[pd.DataFrame]:
    """前复权日线行情，含基础技术指标计算。"""
    if not AK_AVAILABLE:
        return None
    sym = _norm_symbol(symbol)
    end = datetime.now()
    start = end - timedelta(days=days * 2)
    df = _safe(
        ak.stock_zh_a_hist,
        symbol=sym,
        period="daily",
        start_date=start.strftime("%Y%m%d"),
        end_date=end.strftime("%Y%m%d"),
        adjust="qfq",
    )
    if df is None or df.empty:
        return None
    df = df.rename(columns={"日期": "date", "开盘": "open", "收盘": "close", "最高": "high", "最低": "low", "成交量": "volume"})
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    df["ma5"] = df["close"].rolling(5).mean()
    df["ma20"] = df["close"].rolling(20).mean()
    df["ma60"] = df["close"].rolling(60).mean()
    return df


def compute_tech_signals(df: Optional[pd.DataFrame]) -> dict[str, Any]:
    """从日线数据计算技术面指标。"""
    if df is None or df.empty or len(df) < 20:
        return {"error": "历史数据不足"}
    last = df.iloc[-1]
    close = float(last["close"])
    sig = {
        "price": close,
        "ma5": _to_float(last.get("ma5")),
        "ma20": _to_float(last.get("ma20")),
        "ma60": _to_float(last.get("ma60")),
        "ret_5d": _to_float((close / df["close"].iloc[-6] - 1) * 100) if len(df) >= 6 else None,
        "ret_20d": _to_float((close / df["close"].iloc[-21] - 1) * 100) if len(df) >= 21 else None,
        "ret_60d": _to_float((close / df["close"].iloc[-61] - 1) * 100) if len(df) >= 61 else None,
        "high_60d": _to_float(df["high"].tail(60).max()),
        "low_60d": _to_float(df["low"].tail(60).min()),
    }
    # 简单 RSI(14)
    delta = df["close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, 1e-9)
    sig["rsi14"] = _to_float((100 - 100 / (1 + rs)).iloc[-1])
    sig["volume_ratio"] = _to_float(df["volume"].iloc[-1] / df["volume"].tail(5).mean()) if len(df) >= 5 else None
    return sig


def get_financials(symbol: str) -> Optional[dict[str, Any]]:
    """财务摘要：营收、净利润、增速、ROE、毛利率、资产负债率。"""
    if not AK_AVAILABLE:
        return None
    sym = _norm_symbol(symbol)
    # 同花顺财务指标（按报告期）
    df = _safe(ak.stock_financial_abstract_ths, symbol=sym, indicator="按报告期")
    if df is None or df.empty:
        # 备选：新浪财务摘要
        df = _safe(ak.stock_financial_abstract, symbol=sym)
    if df is None or df.empty:
        return None
    # 最新一期在前，取最近两期计算增速
    recent = df.iloc[0]
    prev = df.iloc[1] if len(df) > 1 else None
    out: dict[str, Any] = {"period": str(recent.get("报告期", ""))}
    for key, col in [
        ("revenue", "营业总收入"),
        ("net_profit", "净利润"),
        ("roe", "净资产收益率"),
        ("gross_margin", "销售毛利率"),
        ("debt_ratio", "资产负债率"),
    ]:
        out[key] = _to_float(recent.get(col))
        if prev is not None:
            prev_v = _to_float(prev.get(col))
            if prev_v not in (None, 0) and out[key] is not None:
                out[f"{key}_yoy"] = _to_float((out[key] / prev_v - 1) * 100)
    return out


def get_lhb(symbol: str, days: int = 30) -> Optional[dict[str, Any]]:
    """最近龙虎榜记录。"""
    if not AK_AVAILABLE:
        return None
    sym = _norm_symbol(symbol)
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


def get_news(symbol: str) -> Optional[list[dict[str, str]]]:
    """个股新闻标题列表（东方财富）。失败返回 None。"""
    if not AK_AVAILABLE:
        return None
    sym = _norm_symbol(symbol)
    df = _safe(ak.stock_news_em, symbol=sym)
    if df is None or df.empty:
        return None
    items = []
    for _, row in df.head(8).iterrows():
        items.append({"title": str(row.get("新闻标题", "")), "time": str(row.get("发布时间", ""))[:16]})
    return items


def _to_float(v: Any) -> Optional[float]:
    try:
        f = float(v)
        return f if f == f else None  # NaN 过滤
    except (TypeError, ValueError):
        return None
