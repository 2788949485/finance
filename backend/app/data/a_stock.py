"""A股数据获取（腾讯实时行情 / 腾讯fqkline K线 / 同花顺财务 / 东财龙虎榜）。

该模块同时承担"分发器"角色：对 hk/us 前缀的符号，转发到 hk_us_stock 子模块。
所有函数容错：网络异常或接口变动时返回 None/空值。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

import pandas as pd
import requests

from .utils import (
    AK_AVAILABLE,
    TTL,
    _market_prefix,
    _norm_symbol,
    _parse_num,
    _safe,
    _to_float,
    ak,
    cached,
)
from .hk_us_stock import (
    _fetch_us_kline,
    _fetch_us_kline_aggregated,
    _fetch_us_minute_kline,
    _us_minute_from_em,
)


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
                # 盘口数据
                "pre_close": _to_float(p[4]),       # 昨收
                "open": _to_float(p[5]),            # 今开
                "high": _to_float(p[33]),           # 最高
                "low": _to_float(p[34]),            # 最低
                "volume": _to_float(p[36]),         # 成交量(手)
                "amount": _to_float(p[37]),         # 成交额(万元)
                "limit_up": _to_float(p[47]),       # 涨停价
                "limit_down": _to_float(p[48]),     # 跌停价
                "volume_ratio": _to_float(p[49]),   # 量比
            }
        except Exception:
            return None

    if fresh:
        return _fetch()  # fresh=True：直连腾讯，不读缓存
    return cached(f"quote:{sym}", TTL["quote"], _fetch)


def _fetch_a_share_minute_akshare(sym: str, m_param: str, count: int) -> Optional[dict[str, Any]]:
    """A股分钟级K线（AKShare腾讯源，国内直连）。

    m_param: m5/m15/m30/m60
    sym: 6位A股代码(无前缀)
    """
    try:
        import akshare as ak
        # AKShare需要带市场前缀的代码: sh600519 / sz000001
        prefix = "sh" if sym.startswith(("6", "9")) else "sz"
        ak_code = f"{prefix}{sym}"
        period_map = {"m5": "5", "m15": "15", "m30": "30", "m60": "60"}
        ak_period = period_map.get(m_param, "5")
        df = ak.stock_zh_a_minute(symbol=ak_code, period=ak_period, adjust="qfq")
        if df is None or len(df) == 0:
            return None
        # AKShare列: day, open, high, low, close, volume, amount
        df = df.tail(count)
        bars = []
        for _, row in df.iterrows():
            dt_str = str(row["day"])[:16]  # YYYY-MM-DD HH:MM
            try:
                bars.append({
                    "date": dt_str,
                    "open": round(float(row["open"]), 4),
                    "close": round(float(row["close"]), 4),
                    "high": round(float(row["high"]), 4),
                    "low": round(float(row["low"]), 4),
                    "volume": int(float(row["volume"])),
                })
            except (ValueError, TypeError):
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


# ==================== 多周期K线 ====================

# 支持的周期: 日/周/月 用fqkline接口, 分钟级用mkline接口
PERIOD_MAP = {
    "day": {"type": "fqkline", "param": "day"},
    "week": {"type": "fqkline", "param": "week"},
    "month": {"type": "fqkline", "param": "month"},
    "5min": {"type": "mkline", "param": "m5"},
    "15min": {"type": "mkline", "param": "m15"},
    "30min": {"type": "mkline", "param": "m30"},
    "60min": {"type": "mkline", "param": "m60"},
}


def get_history_multi(symbol: str, period: str = "day", count: int = 250) -> Optional[pd.DataFrame]:
    """多周期K线数据。

    period: day/week/month/5min/15min/30min/60min
    count: 返回的K线数量

    数据源:
    - A股日K/周K/月K: 腾讯fqkline
    - A股分钟级: AKShare腾讯源(stock_zh_a_minute)
    - 美股日K: AKShare新浪源(stock_us_daily)
    - 美股周K/月K: 新浪日K聚合
    - 美股分钟级: yfinance
    """
    sym = _norm_symbol(symbol)
    period_info = PERIOD_MAP.get(period)
    if period_info is None:
        period_info = PERIOD_MAP["day"]

    cache_key = f"kline:{sym}:{period}:{count}"

    def _fetch() -> Optional[dict[str, Any]]:
        code = f"{_market_prefix(sym)}{sym}"
        try:
            # ===== 美股 =====
            if sym.startswith("us"):
                ticker = sym[2:]
                if period_info["type"] == "fqkline" and period_info["param"] != "day":
                    return _fetch_us_kline_aggregated(sym, period_info["param"], count)
                if period_info["type"] == "fqkline" and period_info["param"] == "day":
                    return _fetch_us_kline(sym, count)
                if period_info["type"] == "mkline":
                    return _fetch_us_minute_kline(ticker, period_info["param"], count)

            # ===== A股分钟级: AKShare腾讯源 =====
            if period_info["type"] == "mkline":
                return _fetch_a_share_minute_akshare(sym, period_info["param"], count)

            # ===== A股日K/周K/月K: 腾讯fqkline =====
            if period_info["type"] == "fqkline":
                # 日/周/月K线
                url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},{period_info['param']},,,{count},qfq"
                r = requests.get(url, timeout=15)
                data = r.json()
                node = data["data"][code]
                key = f"qfq{period_info['param']}"
                rows = node.get(key, node.get(period_info["param"], []))
                if isinstance(rows, dict):
                    rows = rows.get("data", [])
            else:
                # 分钟级K线
                url = f"https://ifzq.gtimg.cn/appstock/app/kline/mkline?param={code},{period_info['param']},,{count}"
                r = requests.get(url, timeout=15)
                data = r.json()
                node = data["data"][code]
                rows = node.get(period_info["param"], {})
                if isinstance(rows, dict):
                    rows = rows.get("data", [])

            bars = []
            for row in rows:
                try:
                    if period_info["type"] == "fqkline":
                        # 格式: ['2026-03-11', open, close, high, low, volume]
                        bars.append({
                            "date": str(row[0]),
                            "open": float(row[1]),
                            "close": float(row[2]),
                            "high": float(row[3]),
                            "low": float(row[4]),
                            "volume": float(row[5]),
                        })
                    else:
                        # 分钟格式: ['202607271445', open, close, high, low, volume, ...]
                        raw_date = str(row[0])
                        if len(raw_date) == 12:
                            # YYYYMMDDHHMM -> 格式化
                            dt_str = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]} {raw_date[8:10]}:{raw_date[10:12]}"
                        else:
                            dt_str = raw_date
                        bars.append({
                            "date": dt_str,
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

    data = cached(cache_key, TTL["kline"], _fetch)
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
    """从日线数据计算技术面指标。

    指标清单：
    - MA5/MA20/MA60 均线
    - RSI14 相对强弱
    - MACD 指数平滑异同
    - KDJ 随机指标
    - BOLL 布林带
    - OBV 能量潮
    - CCI 顺势指标
    - WR 威廉指标
    - DMI 趋向指标(ADX/DI+/DI-)
    - SAR 抛物线转向
    - 量比
    """
    if df is None or df.empty or len(df) < 20:
        return {"error": "历史数据不足"}
    last = df.iloc[-1]
    close = float(last["close"])
    high = float(last["high"])
    low = float(last["low"])
    vol = float(last["volume"])
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

    # RSI14
    delta = df["close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, 1e-9)
    sig["rsi14"] = _to_float((100 - 100 / (1 + rs)).iloc[-1])

    # MACD (12,26,9)
    ema12 = df["close"].ewm(span=12, adjust=False).mean()
    ema26 = df["close"].ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    sig["macd_dif"] = round(_to_float(dif.iloc[-1]), 4)
    sig["macd_dea"] = round(_to_float(dea.iloc[-1]), 4)
    sig["macd_hist"] = round(_to_float((dif - dea).iloc[-1] * 2), 4)

    # KDJ (9,3,3)
    low_9 = df["low"].rolling(9).min()
    high_9 = df["high"].rolling(9).max()
    rsv = (df["close"] - low_9) / (high_9 - low_9).replace(0, 1e-9) * 100
    k = rsv.ewm(com=2, adjust=False).mean()
    d = k.ewm(com=2, adjust=False).mean()
    sig["kdj_k"] = round(_to_float(k.iloc[-1]), 2)
    sig["kdj_d"] = round(_to_float(d.iloc[-1]), 2)
    sig["kdj_j"] = round(_to_float(3 * k.iloc[-1] - 2 * d.iloc[-1]), 2)

    # BOLL (20,2)
    boll_mid = df["close"].rolling(20).mean()
    boll_std = df["close"].rolling(20).std()
    sig["boll_mid"] = round(_to_float(boll_mid.iloc[-1]), 2)
    sig["boll_upper"] = round(_to_float(boll_mid.iloc[-1] + 2 * boll_std.iloc[-1]), 2)
    sig["boll_lower"] = round(_to_float(boll_mid.iloc[-1] - 2 * boll_std.iloc[-1]), 2)
    sig["boll_width"] = round(_to_float((sig["boll_upper"] - sig["boll_lower"]) / max(sig["boll_mid"], 1) * 100), 2)

    # OBV 能量潮
    obv = (df["volume"] * (df["close"].diff() > 0).astype(int) * 2 - 1).cumsum()
    sig["obv"] = _to_float(obv.iloc[-1])
    sig["obv_ma5"] = _to_float(obv.tail(5).mean()) if len(obv) >= 5 else None

    # CCI 顺势指标 (14)
    tp = (df["high"] + df["low"] + df["close"]) / 3
    tp_sma = tp.rolling(14).mean()
    tp_md = tp.rolling(14).apply(lambda x: abs(x - x.mean()).mean(), raw=True)
    cci = (tp - tp_sma) / (0.015 * tp_md.replace(0, 1e-9))
    sig["cci14"] = round(_to_float(cci.iloc[-1]), 2)

    # WR 威廉指标 (14)
    wr_high = df["high"].rolling(14).max()
    wr_low = df["low"].rolling(14).min()
    wr = (wr_high - df["close"]) / (wr_high - wr_low).replace(0, 1e-9) * -100
    sig["wr14"] = round(_to_float(wr.iloc[-1]), 2)

    # DMI 趋向指标 (14)
    if len(df) >= 28:
        up_move = df["high"].diff()
        down_move = -df["low"].diff()
        plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0)
        minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0)
        tr = pd.concat([df["high"] - df["low"], (df["high"] - df["close"].shift()).abs(), (df["low"] - df["close"].shift()).abs()], axis=1).max(axis=1)
        atr_dmi = tr.rolling(14).mean()
        plus_di = 100 * plus_dm.rolling(14).mean() / atr_dmi.replace(0, 1e-9)
        minus_di = 100 * minus_dm.rolling(14).mean() / atr_dmi.replace(0, 1e-9)
        dx = (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1e-9) * 100
        adx = dx.rolling(14).mean()
        sig["adx"] = round(_to_float(adx.iloc[-1]), 2)
        sig["di_plus"] = round(_to_float(plus_di.iloc[-1]), 2)
        sig["di_minus"] = round(_to_float(minus_di.iloc[-1]), 2)
    else:
        sig["adx"] = sig["di_plus"] = sig["di_minus"] = None

    # SAR 抛物线转向 (简化版)
    if len(df) >= 10:
        af = 0.02
        max_af = 0.2
        sar_list = []
        is_up = True
        ep = float(df["high"].iloc[0])
        sar = float(df["low"].iloc[0])
        for i in range(1, len(df)):
            h = float(df["high"].iloc[i])
            l = float(df["low"].iloc[i])
            sar = sar + af * (ep - sar)
            if is_up:
                if l < sar:
                    is_up = False
                    sar = ep
                    ep = l
                    af = 0.02
                else:
                    if h > ep:
                        ep = h
                        af = min(af + 0.02, max_af)
            else:
                if h > sar:
                    is_up = True
                    sar = ep
                    ep = h
                    af = 0.02
                else:
                    if l < ep:
                        ep = l
                        af = min(af + 0.02, max_af)
            sar_list.append(sar)
        sig["sar"] = round(sar, 2)
        sig["sar_signal"] = "看多" if is_up else "看空"

    sig["volume_ratio"] = _to_float(df["volume"].iloc[-1] / df["volume"].tail(5).mean()) if len(df) >= 5 else None
    return sig


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


def get_minute_kline(symbol: str) -> Optional[dict[str, Any]]:
    """当日分时数据。A股/港股用腾讯接口，美股用东财分时(curl_cffi)。

    返回 {points: [[时间, 价, 均价, 量], ...], last_close: 昨收}
    """
    sym = _norm_symbol(symbol)

    # 美股：用东财分时接口(curl_cffi绕过TLS封锁)
    if sym.startswith("us"):
        return _us_minute_from_em(sym)

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
            # 点数太少视为无效（盘前/异常），但美股/港股盘后可能只有1-2条
            if len(out) < 1:
                return None
            # 判断市场：A股成交量单位=手(需*100转股)，港股/美股=股数(直接用)
            is_a_share = not (sym.startswith("hk") or sym.startswith("us"))
            vol_factor = 100 if is_a_share else 1
            # 计算分时均价（累计成交额 / (累计成交量 * vol_factor)）
            cum_amt = 0.0
            cum_vol = 0.0
            for pt in out:
                amt = pt.pop("amount", 0) or 0
                vol = pt.get("volume") or 0
                cum_amt += amt
                cum_vol += vol
                pt["avg"] = round(cum_amt / (cum_vol * vol_factor), 2) if cum_vol > 0 else None
            # 从 qt 数组提取数据日期
            data_date = ""
            is_today = True
            try:
                from datetime import datetime as _dt
                today_str = _dt.now().strftime("%Y%m%d")
                if isinstance(qt_arr, list) and len(qt_arr) > 30 and qt_arr[30]:
                    raw_date = str(qt_arr[30])[:8]
                    if raw_date.isdigit() and len(raw_date) == 8:
                        data_date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
                        is_today = (raw_date == today_str)
            except Exception:
                pass
            return {"points": out, "last_close": last_close, "data_date": data_date, "is_today": is_today}
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

    # 腾讯搜索不支持 us/hk 前缀，去掉后再搜
    search_q = q.strip()
    if search_q.lower().startswith("us"):
        search_q = search_q[2:]
    elif search_q.lower().startswith("hk"):
        search_q = search_q[2:].lstrip("0") or search_q[2:]

    def _fetch() -> Optional[list[dict[str, str]]]:
        import json as _json
        import urllib.parse
        url = f"https://smartbox.gtimg.cn/s3/?v=2&q={urllib.parse.quote(search_q)}&t=all"
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
