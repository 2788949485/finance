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
    """统一股票代码格式：A股6位 / 港股 hk+5位 / 美股 us+代码。

    规则：hk/us 前缀原样保留；6位数字=A股；5位数字（如 00700）=港股；
    更短数字=港股补零；其他按原样返回（公司名交给 resolve_symbol 处理）。
    """
    s = symbol.strip().lower()
    if s.startswith(("hk", "us")):
        code = s[2:]
        if code.isdigit():
            return s[:2] + code.zfill(5) if len(code) < 5 else s[:2] + code
        return s[:2] + code.upper()  # 美股代码大写（usAAPL）
    if s.isdigit():
        if len(s) == 5:
            return "hk" + s
        if len(s) <= 4:
            return "hk" + s.zfill(5)
        return s.zfill(6)
    return s


def _market_prefix(symbol: str) -> str:
    """腾讯接口的市场前缀：A股 sh/sz/bj，港股/美股无需前缀（代码自带 hk/us）。"""
    if symbol.startswith(("hk", "us")):
        return ""
    if symbol[0] in "69":
        return "sh"
    if symbol[0] in "48":
        return "bj"
    return "sz"


def get_stock_brief(symbol: str, fresh: bool = False) -> Optional[dict[str, Any]]:
    """个股概览（腾讯实时行情），默认缓存 60 秒；fresh=True 时强制实时请求。

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

    if fresh:
        return _fetch()  # fresh=True：直连腾讯，不读缓存
    return cached(f"quote:{sym}", TTL["quote"], _fetch)


def _fetch_us_kline(sym: str, days: int) -> Optional[dict[str, Any]]:
    """美股日K线（新浪接口，1984年至今完整历史，国内直连）。

    返回 JSONP：var _=([{"d":"1984-09-07","o":"26.50","h":"26.87","l":"26.25","c":"26.50","v":...}, ...])
    """
    import json as _json

    ticker = sym[2:]  # usAAPL -> AAPL
    url = (
        "https://stock.finance.sina.com.cn/usstock/api/jsonp_v2.php/"
        f"var%20_=/US_MinKService.getDailyK?symbol={ticker}"
    )
    try:
        r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"})
        text = r.text
        start, end = text.find("(["), text.rfind("])")
        if start == -1 or end == -1:
            return None
        data = _json.loads(text[start + 1 : end + 1])
        bars = []
        for row in data[-days:]:
            try:
                bars.append({
                    "date": str(row["d"]),
                    "open": float(row["o"]),
                    "close": float(row["c"]),
                    "high": float(row["h"]),
                    "low": float(row["l"]),
                    "volume": float(row.get("v", 0)),
                })
            except (KeyError, ValueError, TypeError):
                continue
        return {"bars": bars} if bars else None
    except Exception:
        return None


def get_history(symbol: str, days: int = 250) -> Optional[pd.DataFrame]:
    """前复权日线行情（腾讯 K 线接口），缓存 1 小时。"""
    sym = _norm_symbol(symbol)

    def _fetch() -> Optional[dict[str, Any]]:
        # 美股：腾讯接口日K只返回最近2条，改用新浪美股日K（1984年至今完整历史）
        if sym.startswith("us"):
            return _fetch_us_kline(sym, days)
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


def get_flash_news(keyword: str = "", limit: int = 10) -> Optional[list[dict[str, str]]]:
    """实时财经快讯（新浪 7x24 全球财经直播），缓存 60 秒。

    keyword 非空时按关键词过滤（如个股名称/代码）；否则返回最新快讯。
    """
    def _fetch() -> Optional[list[dict[str, str]]]:
        url = (
            "https://zhibo.sina.com.cn/api/zhibo/feed?"
            "page=1&page_size=30&zhibo_id=152&tag_id=0&dire=f&dpc=1"
        )
        try:
            r = requests.get(url, timeout=12, headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://finance.sina.com.cn",
            })
            d = r.json()
            items = d.get("result", {}).get("data", {}).get("feed", {}).get("list", [])
            out = []
            for it in items:
                text = (it.get("rich_text") or "").strip()
                if not text:
                    continue
                out.append({
                    "title": text,
                    "time": (it.get("create_time") or "")[:16],
                })
            return out or None
        except Exception:
            return None

    data = cached(f"flash:{keyword or 'all'}", 60, _fetch)
    if data is None:
        return None
    if keyword:
        kw = keyword.lower()
        hits = [n for n in data if kw in n["title"].lower() or kw in n["time"]]
        return hits[:limit] or None
    return data[:limit]


def get_news(symbol: str) -> Optional[list[dict[str, str]]]:
    """个股新闻：新浪快讯按名称/代码过滤 + 东方财富个股新闻兜底，缓存 15 分钟。"""
    sym = _norm_symbol(symbol)
    brief = get_stock_brief(sym)
    name = brief.get("name", "") if brief else ""
    items: list[dict[str, str]] = []

    # 1) 实时快讯过滤（三市场通用）
    if name:
        # 名称可能带后缀（控股/集团/股份），截断核心名提高命中（腾讯控股 -> 腾讯）
        short = name
        for suffix in ("控股", "集团", "股份有限公司", "有限公司", "股份", "科技"):
            if short.endswith(suffix) and len(short) - len(suffix) >= 2:
                short = short[: -len(suffix)]
                break
        for kw in dict.fromkeys([name, short]):
            flash = get_flash_news(keyword=kw, limit=4)
            if flash:
                items.extend(flash)
        # 代码过滤（如 600519）
        code_hits = get_flash_news(keyword=sym.replace("hk", "").replace("us", ""), limit=3)
        if code_hits:
            items.extend(code_hits)

    # 2) A股专用：东方财富个股新闻（akshare，可能受限，仅作补充）
    if AK_AVAILABLE and not sym.startswith(("hk", "us")) and len(items) < 8:
        def _fetch() -> Optional[list[dict[str, str]]]:
            df = _safe(ak.stock_news_em, symbol=sym)
            if df is None or df.empty:
                return None
            out = []
            for _, row in df.head(6).iterrows():
                out.append({"title": str(row.get("新闻标题", "")), "time": str(row.get("发布时间", ""))[:16]})
            return out or None

        extra = cached(f"news:{sym}", TTL["news"], _fetch)
        if extra:
            items.extend(extra)

    # 去重（按标题）
    seen, uniq = set(), []
    for n in items:
        if n["title"] not in seen:
            seen.add(n["title"])
            uniq.append(n)
    return uniq[:8] or None


def get_minute_kline(symbol: str) -> Optional[dict[str, Any]]:
    """当日分时数据（腾讯分钟接口），缓存 30 秒。

    返回 {points: [[时间, 价, 均价, 量], ...], last_close: 昨收}
    """
    sym = _norm_symbol(symbol)
    code = f"{_market_prefix(sym)}{sym}"

    def _fetch() -> Optional[dict[str, Any]]:
        try:
            url = f"https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={code}"
            r = requests.get(url, timeout=12)
            d = r.json()
            node = d.get("data", {}).get(code, {})
            points = node.get("data", {}).get("data", [])
            qt = node.get("qt", {})
            last_close = None
            # 昨收在 qt[code] 数组 index 4（腾讯行情字段：0未知 1名字 2代码 3现价 4昨收）
            qt_arr = qt.get(code, []) if isinstance(qt, dict) else []
            if isinstance(qt_arr, list) and len(qt_arr) > 4:
                try:
                    last_close = float(qt_arr[4])
                except (ValueError, TypeError):
                    pass
            out = []
            for p in points[:500]:
                try:
                    # 腾讯分时格式: "0930 1350.06 235 31726410.00"（空格分隔字符串）
                    # 或旧格式: ["0930", "1358.00", "1358.50", "12345"]
                    parts = p.split() if isinstance(p, str) else p
                    t = str(parts[0])
                    if not (t.isdigit() and len(t) == 4):
                        continue
                    price = float(parts[1])
                    if price <= 0:
                        continue
                    vol = float(parts[2]) if len(parts) > 2 and parts[2] else 0    # 成交量(手)
                    amt = float(parts[3]) if len(parts) > 3 and parts[3] else 0     # 成交额
                    out.append({
                        "time": t,
                        "price": price,
                        "volume": vol if vol else None,
                        "amount": amt if amt else None,
                    })
                except (ValueError, IndexError, TypeError, AttributeError):
                    continue
            # 点数太少视为无效（盘前/异常）
            if len(out) < 2:
                return None
            # 计算分时均价（累计成交额 / 累计成交量）
            cum_amt = 0.0
            cum_vol = 0.0
            for pt in out:
                amt = pt.pop("amount", 0) or 0
                vol = pt.get("volume") or 0
                cum_amt += amt
                cum_vol += vol
                pt["avg"] = round(cum_amt / cum_vol, 2) if cum_vol > 0 else None
            return {"points": out, "last_close": last_close}
        except Exception:
            return None

    return cached(f"minute:{sym}", 30, _fetch)


def search_stocks(q: str, limit: int = 8) -> Optional[list[dict[str, str]]]:
    """股票搜索（腾讯智能搜索：代码/名称/拼音），缓存 5 分钟。

    返回 [{market, code, name, type}]，只含股票（GP），排除指数/基金。
    美股代码规范化：us~aapl.oq -> usAAPL
    """
    if not q or not q.strip():
        return None

    def _fetch() -> Optional[list[dict[str, str]]]:
        import json as _json
        import urllib.parse
        url = f"https://smartbox.gtimg.cn/s3/?v=2&q={urllib.parse.quote(q.strip())}&t=all"
        try:
            r = requests.get(url, timeout=8)
            r.encoding = "gbk"
            body = r.text.split('"')[1] if '"' in r.text else ""
            items = []
            for part in body.split("^"):
                fields = part.split("~")
                if len(fields) < 5:
                    continue
                market, code, raw_name, _pn, typ = fields[0], fields[1], fields[2], fields[3], fields[4]
                if not (typ.startswith("GP") or typ == "GP"):
                    continue  # 只留股票
                # 名称是 \uXXXX 转义，解码
                try:
                    name = _json.loads(f'"{raw_name}"')
                except Exception:
                    name = raw_name
                if market == "us":
                    std = "us" + code.split(".")[0].upper()
                elif market in ("hk", "bj"):
                    std = market + code
                elif market in ("sh", "sz"):
                    std = code.zfill(6)
                else:
                    continue
                items.append({"market": market, "code": std, "name": name, "type": typ})
                if len(items) >= limit:
                    break
            return items or None
        except Exception:
            return None

    return cached(f"search:{q.strip()}", 300, _fetch)


def get_history_all(symbol: str) -> Optional[pd.DataFrame]:
    """全量历史日K（至上市以来），缓存 6 小时。

    - A股：akshare stock_zh_a_daily（新浪源，全量，2001年至今）
    - 港股：akshare stock_hk_daily（腾讯源，全量）
    - 美股：新浪日K（1984年至今，复用 get_history 的 us 分支）
    统一返回 [date, open, close, high, low, volume] + ma5/ma20/ma60 的 DataFrame。
    """
    sym = _norm_symbol(symbol)
    if sym.startswith("us"):
        return get_history(sym, days=5000)

    def _fetch() -> Optional[dict[str, Any]]:
        try:
            if sym.startswith("hk"):
                df = _safe(ak.stock_hk_daily, symbol=sym[2:], adjust="qfq")
            else:
                df = _safe(ak.stock_zh_a_daily, symbol=f"{_market_prefix(sym)}{sym}", adjust="qfq")
            if df is None or df.empty:
                return None
            bars = []
            for _, row in df.iterrows():
                try:
                    bars.append({
                        "date": str(row["date"])[:10],
                        "open": float(row["open"]),
                        "close": float(row["close"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "volume": float(row.get("volume", 0) or 0),
                    })
                except (ValueError, TypeError, KeyError):
                    continue
            return {"bars": bars} if bars else None
        except Exception:
            return None

    data = cached(f"kline_all:{sym}", 6 * 3600, _fetch)
    if data is None or not data.get("bars"):
        return None
    df = pd.DataFrame(data["bars"])
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    df["ma5"] = df["close"].rolling(5).mean()
    df["ma20"] = df["close"].rolling(20).mean()
    df["ma60"] = df["close"].rolling(60).mean()
    return df


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
