"""社交情绪面数据（东财人气排名 + 雪球关注度 + 资金动能）。

get_social_sentiment：综合情绪评分（-100到100），仅支持A股。
"""
from __future__ import annotations

from typing import Any, Optional

from .utils import AK_AVAILABLE, _norm_symbol, ak, cached


def get_social_sentiment(symbol: str) -> Optional[dict[str, Any]]:
    """社交情绪数据：东财人气排名 + 雪球关注度 + 今日主力资金净流入 + 排名变化趋势。

    仅支持A股（港股美股东财人气榜无数据）。
    返回 {
        hot_rank: int,          # 东财人气榜当前排名(越小越热)
        hot_rank_trend: list,   # 最近20个时间点的排名
        xq_followers: int,      # 雪球关注人数
        main_net_inflow: float, # 今日主力净流入(元)
        super_large_net: float, # 超大单净流入(元)
        large_net: float,       # 大单净流入
        medium_net: float,      # 中单净流入
        small_net: float,       # 小单净流入
        sentiment_score: float, # 综合情绪评分(-100到100)
    }
    """
    sym = _norm_symbol(symbol)
    if sym.startswith(("hk", "us")):
        return None  # 港股美股无东财人气榜

    result: dict[str, Any] = {}

    # 1) 东财人气榜排名趋势（最近20个10分钟采样点，取最新排名）
    def _fetch_rank_trend() -> Optional[list[dict]]:
        try:
            import os as _os
            saved = {}
            for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
                if k in _os.environ:
                    saved[k] = _os.environ.pop(k)
            try:
                code = f"{'SH' if sym[0] in '69' else 'SZ'}{sym}"
                df = ak.stock_hot_rank_detail_realtime_em(symbol=code)
            finally:
                _os.environ.update(saved)
            if df is None or df.empty:
                return None
            recent = df.tail(20)
            return [
                {"time": str(r["时间"]), "rank": int(r["排名"])}
                for _, r in recent.iterrows()
            ]
        except Exception:
            return None

    result["hot_rank_trend"] = cached(f"ranktrend:{sym}", 300, _fetch_rank_trend)

    # 2) 雪球关注度
    def _fetch_xq() -> Optional[int]:
        try:
            import os as _os
            saved = {}
            for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
                if k in _os.environ:
                    saved[k] = _os.environ.pop(k)
            try:
                df = ak.stock_hot_follow_xq(symbol="最热门")
            finally:
                _os.environ.update(saved)
            if df is None or df.empty:
                return None
            code = f"{'SH' if sym[0] in '69' else 'SZ'}{sym}"
            row = df[df["股票代码"] == code]
            if not row.empty:
                return int(float(row.iloc[0].get("关注", 0)))
            return None
        except Exception:
            return None

    result["xq_followers"] = cached(f"xqfollow:{sym}", 600, _fetch_xq)

    # 3) 资金动能（从K线量价计算，不依赖被封的push2 API）
    #    放量上涨=资金流入，缩量下跌=资金流出
    def _fetch_fund_momentum() -> Optional[dict]:
        try:
            # 延迟导入以避免循环依赖
            from .a_stock import get_history
            hist = get_history(sym, days=10)
            if hist is None or len(hist) < 2:
                return None
            recent = hist.tail(5)
            prev = hist.tail(20).head(10) if len(hist) >= 20 else hist.head(len(hist)//2)
            # 最近5日平均量 vs 前期平均量
            recent_avg_vol = recent["volume"].mean()
            prev_avg_vol = prev["volume"].mean() if len(prev) > 0 else recent_avg_vol
            vol_ratio = recent_avg_vol / prev_avg_vol if prev_avg_vol > 0 else 1.0
            # 涨跌幅
            price_change = (recent.iloc[-1]["close"] - recent.iloc[0]["open"]) / recent.iloc[0]["open"] * 100
            # 量价方向：放量上涨=主力流入，缩量下跌=主力流出
            # 动能值 = 量比 * 涨跌方向
            momentum = (vol_ratio - 1) * (1 if price_change > 0 else -1) * 100
            return {
                "vol_ratio": round(vol_ratio, 2),
                "price_5d_chg": round(price_change, 2),
                "momentum": round(momentum, 1),
            }
        except Exception:
            return None

    fund = cached(f"fundmomentum:{sym}", 300, _fetch_fund_momentum)
    if fund:
        result["vol_ratio"] = fund["vol_ratio"]
        result["price_5d_chg"] = fund["price_5d_chg"]
        result["momentum"] = fund["momentum"]
    else:
        result["vol_ratio"] = None
        result["price_5d_chg"] = None
        result["momentum"] = None

    # 4) 综合情绪评分（-100到100）
    score = 0.0
    # 资金动能（量价关系）：momentum正值=放量上涨，负值=缩量下跌
    momentum = result.get("momentum")
    if momentum is not None:
        score += max(-40, min(40, momentum))
    # 人气排名：前50名加分（用趋势中最新排名）
    trend = result.get("hot_rank_trend")
    latest_rank = trend[-1]["rank"] if trend else result.get("hot_rank")
    if latest_rank and latest_rank > 0:
        if latest_rank <= 10:
            score += 30
        elif latest_rank <= 50:
            score += 15
        elif latest_rank <= 200:
            score += 5
        else:
            score -= 5
    # 雪球关注度：高关注+5
    if result.get("xq_followers") and result["xq_followers"] > 500000:
        score += 5

    result["sentiment_score"] = round(max(-100, min(100, score)), 1)
    return result if any(v is not None for v in result.values()) else None
