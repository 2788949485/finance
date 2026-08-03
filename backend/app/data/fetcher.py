"""A股数据获取层。

- 实时行情：腾讯 qt.gtimg.cn（绕开东方财富 push2 对 Python 客户端的 TLS 指纹封锁）
- 历史行情/财务/龙虎榜/新闻：akshare（东方财富 push2his / 同花顺 / 新浪）

所有函数容错：网络异常或接口变动时返回 None/空值，保证流水线降级运行。
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any, Optional

import pandas as pd
import requests

# A股数据源域名绕过代理直连（本机代理对国内数据源转发不稳定，
# 而直连东方财富/腾讯/新浪/同花顺均可达）。
_CN_DATA_DOMAINS = (
    "eastmoney.com,push2his.eastmoney.com,10jqka.com.cn,ths.cn,"
    "sina.com.cn,sse.com.cn,sseinfo.com,cninfo.com.cn,xueqiu.com,"
    "gtimg.cn,qq.com"
)
os.environ["NO_PROXY"] = os.environ.get("NO_PROXY", "") + "," + _CN_DATA_DOMAINS
os.environ["no_proxy"] = os.environ["NO_PROXY"]

try:
    import akshare as ak
    AK_AVAILABLE = True
except Exception:  # pragma: no cover
    ak = None
    AK_AVAILABLE = False

from ..cache import TTL, cached  # noqa: E402


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


def _market_prefix(symbol: str) -> str:
    """根据代码判断市场：sh/sz/bj。"""
    if symbol[0] in "69":
        return "sh"
    if symbol[0] in "48":
        return "bj"
    return "sz"


def get_stock_brief(symbol: str) -> Optional[dict[str, Any]]:
    """个股概览（腾讯实时行情），缓存 60 秒。

    腾讯接口返回 GBK 编码的 ~ 分隔字段，实测索引：
    p[1]=名称 p[3]=现价 p[32]=涨跌% p[38]=换手% p[39]=PE p[45]=总市值(亿) p[46]=PB
    """
    sym = _norm_symbol(symbol)

    def _fetch() -> Optional[dict[str, Any]]:
        url = f"https://qt.gtimg.cn/q={_market_prefix(sym)}{sym}"
        try:
            r = requests.get(url, timeout=10)
            r.encoding = "gbk"
            body = r.text.split('"')[1] if '"' in r.text else ""
            p = body.split("~")
            if len(p) < 47 or not p[1]:
                return None
            return {
                "symbol": sym,
                "name": p[1],
                "price": _to_float(p[3]),
                "change_pct": _to_float(p[32]),
                "market_cap": _to_float(p[45]),  # 单位：亿元
                "pe": _to_float(p[39]),
                "pb": _to_float(p[46]),
                "turnover": _to_float(p[38]),
                "industry": "",
            }
        except Exception:
            return None

    return cached(f"quote:{sym}", TTL["quote"], _fetch)


def get_history(symbol: str, days: int = 250) -> Optional[pd.DataFrame]:
    """前复权日线行情（腾讯 K 线接口），缓存 1 小时。"""
    sym = _norm_symbol(symbol)

    def _fetch() -> Optional[dict[str, Any]]:
        code = f"{_market_prefix(sym)}{sym}"
        url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,,,{days},qfq"
        try:
            r = requests.get(url, timeout=15)
            data = r.json()
            node = data["data"][code]
            key = "qfqday" if "qfqday" in node else "day"
            rows = node[key]
            # 部分行可能多出第7个字段（成交额等），只取前6列
            bars = []
            for row in rows:
                try:
                    bars.append({
                        "date": str(row[0]),
                        "open": float(row[1]),
                        "close": float(row[2]),
                        "high": float(row[3]),
                        "low": float(row[4]),
                        "volume": float(row[5]),
                    })
                except (ValueError, IndexError):
                    continue
            return {"bars": bars}
        except Exception:
            return None

    data = cached(f"kline:{sym}:{days}", TTL["kline"], _fetch)
    if data is None or not data.get("bars"):
        return None
    df = pd.DataFrame(data["bars"])
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
    delta = df["close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, 1e-9)
    sig["rsi14"] = _to_float((100 - 100 / (1 + rs)).iloc[-1])
    sig["volume_ratio"] = _to_float(df["volume"].iloc[-1] / df["volume"].tail(5).mean()) if len(df) >= 5 else None
    return sig


def get_financials(symbol: str) -> Optional[dict[str, Any]]:
    """财务摘要（同花顺），缓存 24 小时。

    注意：同花顺接口数据倒序（最新报告期在最后一行），数值带单位（亿/万/%）。
    """
    if not AK_AVAILABLE:
        return None
    sym = _norm_symbol(symbol)

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


def get_news(symbol: str) -> Optional[list[dict[str, str]]]:
    """个股新闻标题列表（东方财富），缓存 15 分钟。"""
    if not AK_AVAILABLE:
        return None
    sym = _norm_symbol(symbol)

    def _fetch() -> Optional[list[dict[str, str]]]:
        df = _safe(ak.stock_news_em, symbol=sym)
        if df is None or df.empty:
            return None
        items = []
        for _, row in df.head(8).iterrows():
            items.append({"title": str(row.get("新闻标题", "")), "time": str(row.get("发布时间", ""))[:16]})
        return items

    return cached(f"news:{sym}", TTL["news"], _fetch)


def _parse_num(v: Any) -> Optional[float]:
    """解析带单位数值：'54.27%'->54.27, '1.47亿'->147000000, False/None->None。"""
    if v is None or v is False:
        return None
    s = str(v).strip().replace("%", "")
    if s in ("", "--", "nan", "None", "False"):
        return None
    mult = 1.0
    if s.endswith("亿"):
        mult = 1e8
        s = s[:-1]
    elif s.endswith("万"):
        mult = 1e4
        s = s[:-1]
    try:
        f = float(s) * mult
        return f if f == f else None
    except ValueError:
        return None


def _to_float(v: Any) -> Optional[float]:
    try:
        f = float(v)
        return f if f == f else None  # NaN 过滤
    except (TypeError, ValueError):
        return None
