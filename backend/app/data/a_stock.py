"""A股数据获取 —— 向后兼容入口（原 700 行单文件已物理拆分）。

拆分目标文件（导入路径完全不变）：

    from app.data.a_stock import xxx   # 仍可用：本模块重导出所有公共符号

实际实现分布：
- stock_data.py    A股行情（get_stock_brief/get_history/get_history_multi/get_history_all/
                    get_minute_kline/_fetch_a_share_minute_akshare/PERIOD_MAP）
- tech_signals.py  技术指标（compute_tech_signals）
- financials.py    财务与龙虎榜（get_financials/get_lhb）
- search.py        股票搜索（search_stocks）

该模块同时承担"分发器"角色：对 hk/us 前缀的符号，由 stock_data 转发到 hk_us_stock 子模块。
所有函数容错：网络异常或接口变动时返回 None/空值。

所有函数签名、行为、返回值均未改变 —— 这是一次纯物理拆分。
"""
from __future__ import annotations

# ====== 从子模块重导出全部公共符号（向后兼容） ======

from .stock_data import (  # noqa: F401
    PERIOD_MAP,
    _fetch_a_share_minute_akshare,
    get_history,
    get_history_all,
    get_history_multi,
    get_minute_kline,
    get_stock_brief,
)
from .tech_signals import (  # noqa: F401
    compute_tech_signals,
)
from .financials import (  # noqa: F401
    get_financials,
    get_lhb,
)
from .search import (  # noqa: F401
    search_stocks,
)

__all__ = [
    "PERIOD_MAP",
    "_fetch_a_share_minute_akshare",
    "compute_tech_signals",
    "get_financials",
    "get_history",
    "get_history_all",
    "get_history_multi",
    "get_lhb",
    "get_minute_kline",
    "get_stock_brief",
    "search_stocks",
]
