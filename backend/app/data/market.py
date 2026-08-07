"""行业对比 / 热门股票（基于实时行情聚合）。

- get_industry_compare：从数据库读取同行列表，拉取实时 PE/PB + 行业均值
- get_hot_stocks：A股+港股+美股候选池拉通按涨幅排序取前6
"""
from __future__ import annotations

from typing import Any

from .utils import TTL, _norm_symbol, cached


# 行业同行映射（A股核心股票）
INDUSTRY_PEERS: dict[str, list[str]] = {
    "600519": ["000858", "000568", "002304", "603369", "600809"],  # 白酒：五粮液 泸州老窖 洋河 今世缘 山西汾酒
    "000858": ["600519", "000568", "002304", "603369", "600809"],
    "000001": ["600036", "601398", "601939", "601318", "600000"],  # 银行：招行 工行 建行 平安 浦发
    "600036": ["000001", "601398", "601939", "601318", "600000"],
    "300750": ["002594", "300014", "600089", "300274", "002460"],  # 新能源：比亚迪 亿纬锂能 特变电工 阳光电源 京东方
    "002594": ["300750", "601238", "600104", "601633", "000625"],  # 汽车：长安 上汽 长城 长安
    "600036": ["000001", "601398", "601939", "601318", "600000"],
    "601318": ["000001", "600036", "601398", "601628", "601601"],  # 保险：人寿 太保
}


def get_industry_compare(symbol: str):
    """行业对比：从数据库读取同行列表，拉取实时 PE/PB + 行业均值。"""
    sym = _norm_symbol(symbol)

    # 从数据库获取同行列表（没有则用 LLM 自动生成）
    try:
        from ..chat import get_peers, auto_generate_peers
        peers = get_peers(sym)
        if not peers:
            # 数据库没有，自动生成并缓存
            peers = auto_generate_peers(sym)
    except Exception:
        peers = None
    if not peers:
        return None

    def _fetch():
        # 延迟导入以避免循环依赖
        from .a_stock import get_stock_brief
        items = []
        all_codes = [sym] + peers
        for code in all_codes:
            brief = get_stock_brief(code)
            if brief and brief.get("pe"):
                items.append({
                    "code": code,
                    "name": brief.get("name", code),
                    "pe": brief.get("pe"),
                    "pb": brief.get("pb"),
                    "change_pct": brief.get("change_pct"),
                    "market_cap": brief.get("market_cap"),
                    "is_target": code == sym,
                })
        if len(items) < 2:
            return None
        pes = [i["pe"] for i in items if i["pe"] and i["pe"] > 0]
        pbs = [i["pb"] for i in items if i["pb"] and i["pb"] > 0]
        return {
            "peers": items,
            "avg_pe": round(sum(pes) / len(pes), 2) if pes else None,
            "avg_pb": round(sum(pbs) / len(pbs), 2) if pbs else None,
        }

    return cached(f"industry:{sym}", TTL["quote"], _fetch)


def get_hot_stocks() -> list[dict[str, Any]]:
    """每日热门股票：A股+港股+美股候选池拉通按涨幅排序取前6。"""
    all_pool = [
        # A股
        "600519", "601398", "300750", "600036", "000858",
        "601318", "000001", "600276", "601012", "002594",
        "600900", "000333", "601899", "600030", "002475",
        # 港股
        "hk00700", "hk09988", "hk01810", "hk03690",
        # 美股
        "usAAPL", "usTSLA", "usNVDA", "usMSFT",
    ]

    def _fetch() -> list[dict[str, Any]]:
        # 延迟导入以避免循环依赖
        from .a_stock import get_stock_brief
        quotes = []
        for code in all_pool:
            brief = get_stock_brief(code)
            if brief and brief.get("price"):
                quotes.append({
                    "code": code,
                    "name": brief["name"],
                    "change_pct": brief.get("change_pct", 0),
                })
        # 全市场拉通按涨幅排序取前6
        quotes.sort(key=lambda x: x.get("change_pct", 0), reverse=True)
        return quotes[:6]

    return cached("hot_stocks", 3600, _fetch)  # 缓存1小时
