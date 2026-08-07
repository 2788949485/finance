"""路由模块: market_data"""
from __future__ import annotations
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..auth import get_profile
from ..config import get_config, save_config, PROVIDER_PRESETS
from ..deps import get_current_user, require_admin

router = APIRouter()

from ..data import fetcher as datalayer
from ..data.north_flow import get_north_flow_overview, get_north_flow_stock, get_north_flow_top_stocks
from ..data.sector_flow import get_concept_sectors, get_industry_sectors
from ..data.stock_screener import screen_stocks
from ..data.margin_data import get_margin_detail, get_margin_top
from ..cache import cached, TTL


@router.get("/api/north-flow/overview")
def north_flow_overview_api() -> dict[str, Any]:
    """北向资金总览：近30天净流入趋势。"""
    return north_flow.get_north_flow_overview()



@router.get("/api/north-flow/stock/{symbol}")
def north_flow_stock_api(symbol: str) -> dict[str, Any]:
    """个股北向持股历史（最近60天）。"""
    sym = datalayer._norm_symbol(symbol)
    return north_flow.get_north_flow_stock(sym)



@router.get("/api/north-flow/top-stocks")
def north_flow_top_stocks_api(market: str = "沪股通", period: str = "5日排行") -> dict[str, Any]:
    """北向持股增持排行 TOP20。market: 北向/沪股通/深股通；period: 今日/3日/5日/10日/月/季/年排行。"""
    return north_flow.get_north_flow_top_stocks(market=market, period=period)



@router.get("/api/sectors/concepts")
def sector_concepts_api(limit: int = 20) -> dict[str, Any]:
    """概念板块涨跌排行+主力资金流入。"""
    return sector_flow.get_concept_sectors(limit=limit)



@router.get("/api/sectors/industries")
def sector_industries_api(limit: int = 20) -> dict[str, Any]:
    """行业板块涨跌排行+主力资金流入。"""
    return sector_flow.get_industry_sectors(limit=limit)



@router.get("/api/screener")
def screener_api(
    pe_min: float | None = None,
    pe_max: float | None = None,
    pb_min: float | None = None,
    pb_max: float | None = None,
    change_pct_min: float | None = None,
    change_pct_max: float | None = None,
    turnover_min: float | None = None,
    turnover_max: float | None = None,
    market_cap_min: float | None = None,
    market_cap_max: float | None = None,
    sort_by: str = "change_pct",
    sort_desc: bool = True,
    limit: int = 50,
) -> dict[str, Any]:
    """条件选股：全市场快照+多条件过滤+排序。市值单位亿元。"""
    return stock_screener.screen_stocks(
        pe_min=pe_min, pe_max=pe_max,
        pb_min=pb_min, pb_max=pb_max,
        change_pct_min=change_pct_min, change_pct_max=change_pct_max,
        turnover_min=turnover_min, turnover_max=turnover_max,
        market_cap_min=market_cap_min, market_cap_max=market_cap_max,
        sort_by=sort_by, sort_desc=sort_desc, limit=limit,
    )



@router.get("/api/margin/detail")
def margin_detail_api(symbol: str | None = None, date: str | None = None) -> dict[str, Any]:
    """融资融券明细（上交所+深交所合并，可按 code 过滤）。date: YYYYMMDD。"""
    return margin_mod.get_margin_detail(symbol=symbol, date=date)



@router.get("/api/margin/top")
def margin_top_api(date: str | None = None, limit: int = 20) -> dict[str, Any]:
    """融资余额 TOP / 融券余量 TOP。date: YYYYMMDD。"""
    return margin_mod.get_margin_top(date=date, limit=limit)


# ==================== 交易后反思学习闭环 ====================

