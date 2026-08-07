"""路由模块: portfolio"""
from __future__ import annotations
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..auth import get_profile
from ..config import get_config, save_config, PROVIDER_PRESETS
from ..deps import get_current_user, require_admin

router = APIRouter()

from .. import portfolio as pf
from ..chat import get_peers, auto_generate_peers


@router.get("/api/portfolio")
def get_portfolio_api(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    """获取投资组合：持仓+实时盈亏+总览。"""
    return portfolio.get_portfolio(user["id"])



@router.post("/api/portfolio/buy")
async def buy_api(request: Request, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    """买入股票。body: {symbol, shares, price, date?, note?}"""
    body = await request.json()
    symbol = (body.get("symbol") or "").strip()
    if not symbol:
        raise HTTPException(400, "请提供股票代码")
    resolved = _resolve_ticker(symbol)
    if not resolved:
        raise HTTPException(400, f"无法识别 {symbol}")
    shares = float(body.get("shares", 0))
    price = float(body.get("price", 0))
    if shares <= 0 or price <= 0:
        raise HTTPException(400, "数量和价格必须大于0")
    name = body.get("symbol_name", "")
    if not name:
        brief = datalayer.get_stock_brief(resolved)
        name = brief.get("name", resolved) if brief else resolved
    result = portfolio.buy_stock(user["id"], resolved, name, shares, price,
                                 body.get("date", ""), body.get("note", ""))
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result



@router.post("/api/portfolio/sell")
async def sell_api(request: Request, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    """卖出股票。"""
    body = await request.json()
    symbol = (body.get("symbol") or "").strip()
    resolved = _resolve_ticker(symbol) or symbol
    shares = float(body.get("shares", 0))
    price = float(body.get("price", 0))
    if shares <= 0 or price <= 0:
        raise HTTPException(400, "数量和价格必须大于0")
    result = portfolio.sell_stock(user["id"], resolved, shares, price,
                                  body.get("date", ""), body.get("note", ""))
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result



@router.delete("/api/portfolio/{symbol}")
def remove_position_api(symbol: str, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, str]:
    """删除持仓。"""
    ok = portfolio.remove_position(user["id"], datalayer._norm_symbol(symbol))
    return {"status": "ok" if ok else "not_found"}



@router.get("/api/portfolio/transactions")
def transactions_api(user: dict[str, Any] = Depends(get_current_user)) -> list[dict[str, Any]]:
    """交易历史。"""
    return portfolio.list_transactions(user["id"])


# ---------- 回测系统 ----------


@router.get("/api/peers")
def list_peers() -> list[dict[str, Any]]:
    """列出所有行业同行映射。"""
    return chat_service.list_industry_peers()



@router.put("/api/peers/{code}")
async def save_peer(code: str, request: Request) -> dict[str, str]:
    """新增或更新行业同行映射。"""
    body = await request.json()
    chat_service.save_peers(code, body.get("name", code), body.get("peers", []))
    return {"status": "ok"}



@router.delete("/api/peers/{code}")
def delete_peer(code: str) -> dict[str, str]:
    """删除行业同行映射。"""
    ok = chat_service.delete_peers(code)
    return {"status": "ok" if ok else "not_found"}


def _num(v: Any):
    try:
        if v is None:
            return None
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


# ---------- 价格预警 ----------

