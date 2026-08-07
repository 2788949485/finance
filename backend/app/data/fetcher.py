"""数据获取层 —— 向后兼容入口（原 1304 行单文件已物理拆分）。

拆分目标文件（导入路径完全不变）：

    from app.data.fetcher import xxx       # 仍可用：本模块重导出所有公共符号
    from app.data import fetcher as dl     # 仍可用：dl.xxx() 全部保留
    datalayer.fetcher.get_industry_compare # 仍可用（见 app/tools.py:318）

实际实现分布：
- utils.py          通用工具（_norm_symbol/_market_prefix/_to_float/_parse_num/_safe/cached包装/AK_AVAILABLE）
- hk_us_stock.py    港股美股（_fetch_us_kline/_fetch_us_kline_aggregated/_fetch_us_minute_kline/_us_minute_from_em）
- a_stock.py        A股分发器（get_stock_brief/get_history/get_history_multi/compute_tech_signals/
                     get_financials/get_lhb/get_minute_kline/search_stocks/get_history_all/
                     PERIOD_MAP/_fetch_a_share_minute_akshare）
- news.py           新闻快讯（get_flash_news/get_news）
- market.py         行业对比/热门（INDUSTRY_PEERS/get_industry_compare/get_hot_stocks）
- sentiment.py      社交情绪（get_social_sentiment）

所有函数签名、行为、返回值均未改变 —— 这是一次纯物理拆分。
"""
from __future__ import annotations

# ====== 从子模块重导出全部公共符号（向后兼容） ======
# 顺序无所谓；模块对象上的属性访问也会命中（datalayer.fetcher.xxx）。

from .utils import (  # noqa: F401
    AK_AVAILABLE,
    TTL,
    _market_prefix,
    _norm_symbol,
    _parse_num,
    _safe,
    _to_float,
    ak,
    cached,
    data_available,
)
from .hk_us_stock import (  # noqa: F401
    _fetch_us_kline,
    _fetch_us_kline_aggregated,
    _fetch_us_minute_kline,
    _us_minute_from_em,
)
from .a_stock import (  # noqa: F401
    PERIOD_MAP,
    _fetch_a_share_minute_akshare,
    compute_tech_signals,
    get_financials,
    get_history,
    get_history_all,
    get_history_multi,
    get_lhb,
    get_minute_kline,
    get_stock_brief,
    search_stocks,
)
from .news import (  # noqa: F401
    get_flash_news,
    get_news,
)
from .market import (  # noqa: F401
    INDUSTRY_PEERS,
    get_hot_stocks,
    get_industry_compare,
)
from .sentiment import (  # noqa: F401
    get_social_sentiment,
)

__all__ = [
    # utils
    "AK_AVAILABLE", "TTL", "_market_prefix", "_norm_symbol", "_parse_num",
    "_safe", "_to_float", "ak", "cached", "data_available",
    # hk_us_stock
    "_fetch_us_kline", "_fetch_us_kline_aggregated", "_fetch_us_minute_kline",
    "_us_minute_from_em",
    # a_stock
    "PERIOD_MAP", "_fetch_a_share_minute_akshare", "compute_tech_signals",
    "get_financials", "get_history", "get_history_all", "get_history_multi",
    "get_lhb", "get_minute_kline", "get_stock_brief", "search_stocks",
    # news
    "get_flash_news", "get_news",
    # market
    "INDUSTRY_PEERS", "get_hot_stocks", "get_industry_compare",
    # sentiment
    "get_social_sentiment",
]
