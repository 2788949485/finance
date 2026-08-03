"""对话智能体工具集：把数据层与投研流水线包装为 LangChain 工具。

智能体在对话中自主决定调用哪些工具、以什么顺序调用，基于真实数据回答。
"""
from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import tool

from . import data as datalayer
from .pipeline import run_analysis


def _j(data: Any) -> str:
    """dict 转紧凑 JSON 字符串（中文不转义）。"""
    return json.dumps(data, ensure_ascii=False, default=str)


@tool
def get_quote(symbol: str) -> str:
    """查询A股实时行情快照：现价、涨跌幅、换手率、市盈率PE、市净率PB、总市值。
    参数 symbol: 6位股票代码，如 600519。"""
    brief = datalayer.get_stock_brief(symbol)
    if brief is None:
        return "未查询到该股票行情，请确认代码正确（6位数字，如 600519）"
    return _j(brief)


@tool
def get_kline(symbol: str, days: int = 120) -> str:
    """查询A股日K线数据（前复权），返回最近 days 个交易日的 OHLCV（日期/开/收/高/低/量）。
    参数 symbol: 6位股票代码；days: 返回天数，默认120，最大500。"""
    df = datalayer.get_history(symbol, days=min(max(days, 30), 500))
    if df is None or df.empty:
        return "未获取到K线数据"
    rows = df.tail(days)[["date", "open", "close", "high", "low", "volume"]].to_dict(orient="records")
    return _j({"symbol": symbol, "bars": rows})


@tool
def get_financials(symbol: str) -> str:
    """查询A股财务摘要：最新报告期营收、净利润及同比增速、ROE、毛利率、资产负债率。
    参数 symbol: 6位股票代码。"""
    fin = datalayer.get_financials(symbol)
    if fin is None:
        return "未获取到财务数据"
    return _j(fin)


@tool
def get_lhb(symbol: str) -> str:
    """查询A股最近30日龙虎榜记录（上榜原因、净买额、买卖额）。
    参数 symbol: 6位股票代码。"""
    lhb = datalayer.get_lhb(symbol)
    if lhb is None:
        return "近30日无龙虎榜记录"
    return _j(lhb)


@tool
def get_news(symbol: str) -> str:
    """查询A股个股最新新闻标题与发布时间（最多8条）。
    参数 symbol: 6位股票代码。"""
    news = datalayer.get_news(symbol)
    if not news:
        return "暂无新闻数据"
    return _j(news)


@tool
def run_research(symbol: str, topic: str = "") -> str:
    """运行完整多智能体投研分析：5位分析师（宏观/基本面/技术面/情绪面/资金面）独立研判、
    多空辩论、共识评分、风控审查、交易计划。返回结构化报告JSON。
    参数 symbol: 6位股票代码；topic: 可选分析主题。耗时较长（约1-2分钟）。"""
    try:
        result = run_analysis(symbol, topic or None)
        return _j(result)
    except Exception as e:
        return f"投研分析失败: {e}"


FINANCE_TOOLS = [get_quote, get_kline, get_financials, get_lhb, get_news, run_research]
