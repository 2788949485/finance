"""FinanceCrew 后端 API 入口。

启动: uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import auth, chat as chat_service, memory, alert, valuation, portfolio, backtest, llm_compare
from .logger import setup_logging, get_logger

# 初始化日志系统
setup_logging()
logger = get_logger("main")
from .pipeline import run_analysis, _GRAPH
from .config import PROVIDER_PRESETS, get_config, save_config
from .cache import cached, TTL
from .data import fetcher as datalayer
from .models import AnalysisRequest, LLMConfig

app = FastAPI(title="FinanceCrew API", version="0.3.0")

# CORS: 只允许本机和局域网（收紧安全）
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8000", "http://127.0.0.1:8000",
        "http://localhost:5173", "http://127.0.0.1:5173",
    ],
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
    allow_credentials=True,
)

# ---------- 请求频率限制中间件（反爬） ----------
from collections import defaultdict

_rate_map: dict[str, list[float]] = defaultdict(list)
RATE_WINDOW = 60  # 60秒窗口
RATE_MAX = 200    # 每窗口最多200次请求（K线轮询+行情+预警+搜索正常使用需要余量）


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """全局频率限制：每IP每60秒最多60次请求，超过返回429。"""
    # 跳过健康检查
    if request.url.path == "/api/health":
        return await call_next(request)

    client_ip = request.client.host if request.client else "unknown"
    now = time.time()
    reqs = _rate_map[client_ip]
    # 清理过期记录
    _rate_map[client_ip] = [t for t in reqs if now - t < RATE_WINDOW]
    if len(_rate_map[client_ip]) >= RATE_MAX:
        return JSONResponse(
            status_code=429,
            content={"detail": "请求过于频繁，请稍后再试"},
        )
    _rate_map[client_ip].append(now)
    return await call_next(request)

FRONTEND_DIST = Path(os.environ.get("FRONTEND_DIST", Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"))


# ---------- 认证依赖 ----------

def get_current_user(authorization: str = Header(default="")) -> dict[str, Any]:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未登录")
    payload = auth.decode_token(authorization[7:].strip())
    if not payload:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    user = auth.get_user(int(payload["sub"]))
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")
    if not user.get("is_active"):
        raise HTTPException(status_code=403, detail="账号已被禁用")
    return user


def require_admin(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    """管理员守卫：非管理员返回403。"""
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


# ---------- 基础 ----------

def _mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "****"
    return key[:4] + "****" + key[-4:]


def _public_config() -> dict[str, Any]:
    cfg = get_config()
    out = dict(cfg)
    out["api_key"] = _mask_key(str(cfg.get("api_key", "")))
    return out


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/providers")
def providers() -> dict[str, Any]:
    return PROVIDER_PRESETS


@app.get("/api/config")
def read_config() -> dict[str, Any]:
    return _public_config()


@app.put("/api/config")
def write_config(cfg: LLMConfig) -> dict[str, Any]:
    save_config(cfg.model_dump())
    return _public_config()


# ---------- 认证与用户画像 ----------

@app.post("/api/auth/register")
def register(body: dict[str, str], request: Request) -> dict[str, Any]:
    client_ip = request.client.host if request.client else "unknown"
    allowed, msg = auth.check_rate_limit(f"register:{client_ip}")
    if not allowed:
        raise HTTPException(429, msg)
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    invite_code = body.get("invite_code", "").strip()
    if len(username) < 2 or len(username) > 20:
        raise HTTPException(400, "用户名需 2-20 个字符")
    if len(password) < 6:
        raise HTTPException(400, "密码至少 6 位")
    try:
        user = auth.create_user(username, password, invite_code)
    except ValueError as e:
        raise HTTPException(400, str(e))
    token = auth.create_token(user["id"], user["username"])
    auth.record_login_success(f"register:{client_ip}")
    auth.audit_log(user["id"], username, "register", f"invite_code={invite_code}", client_ip)
    return {"token": token, "user": user, "profile": auth.get_profile(user["id"])}


@app.post("/api/auth/login")
def login(body: dict[str, str], request: Request) -> dict[str, Any]:
    client_ip = request.client.host if request.client else "unknown"
    username = (body.get("username") or "").strip()
    for ident in [f"login_ip:{client_ip}", f"login_user:{username}"]:
        allowed, msg = auth.check_rate_limit(ident)
        if not allowed:
            raise HTTPException(429, msg)

    result = auth.authenticate(username, body.get("password") or "")
    if not result:
        for ident in [f"login_ip:{client_ip}", f"login_user:{username}"]:
            auth.record_login_fail(ident)
        raise HTTPException(401, "用户名或密码错误")
    if result.get("_disabled"):
        raise HTTPException(403, "账号已被禁用，请联系管理员")
    user = result
    token = auth.create_token(user["id"], user["username"])
    for ident in [f"login_ip:{client_ip}", f"login_user:{username}"]:
        auth.record_login_success(ident)
    auth.audit_log(user["id"], username, "login", ip=client_ip)
    return {"token": token, "user": user, "profile": auth.get_profile(user["id"])}


@app.get("/api/auth/me")
def me(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return {"user": user, "profile": auth.get_profile(user["id"])}


@app.get("/api/auth/profile")
def get_profile(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return auth.get_profile(user["id"])


@app.put("/api/auth/profile")
def put_profile(body: dict[str, Any], user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    try:
        return auth.update_profile(
            user["id"],
            risk_preference=body.get("risk_preference"),
            watchlist=body.get("watchlist"),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/auth/change-password")
async def change_password_api(request: Request, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, str]:
    """修改密码。需提供旧密码。"""
    body = await request.json()
    old_pwd = body.get("old_password", "")
    new_pwd = body.get("new_password", "")
    if not old_pwd or not new_pwd:
        raise HTTPException(400, "请填写旧密码和新密码")
    try:
        ok = auth.change_password(user["id"], old_pwd, new_pwd)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not ok:
        raise HTTPException(400, "旧密码错误")
    return {"status": "ok"}


# ---------- per-user LLM 配置 ----------

@app.get("/api/auth/llm-config")
def get_llm_config_api(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    """获取当前用户的LLM配置（api_key脱敏）。"""
    return auth.get_user_llm_config(user["id"]) | {"api_key": auth._mask_key(auth.get_user_llm_config(user["id"])["api_key"])}


@app.put("/api/auth/llm-config")
async def save_llm_config_api(request: Request, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    """保存当前用户的LLM配置（api_key加密存储）。"""
    body = await request.json()
    return auth.save_user_llm_config(user["id"], body)


# ---------- 投研分析 ----------

def _resolve_ticker(ticker: str) -> str | None:
    """把用户输入（公司名/代码）解析为标准代码，复用 tools.resolve_symbol。"""
    from .tools import resolve_symbol
    resolved = resolve_symbol(ticker)
    # 校验是否合法：A股6位数字 / hk+5位 / us+代码
    if resolved.isdigit() and len(resolved) == 6:
        return resolved
    if resolved.startswith(("hk", "us")):
        return resolved
    return None


@app.post("/api/analysis")
def create_analysis(req: AnalysisRequest, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    ticker = req.ticker.strip()
    if not ticker:
        raise HTTPException(status_code=400, detail="请输入股票代码或名称（如 600519 / hk00700 / usAAPL）")
    resolved = _resolve_ticker(ticker)
    if not resolved:
        raise HTTPException(status_code=400, detail=f"无法识别 {ticker}")
    try:
        return run_analysis(resolved, req.topic, user_id=user["id"])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"分析失败: {e}")


# 节点名 -> 中文标签映射（SSE 推送给前端展示）
_NODE_LABELS = {
    "collect_data": "数据收集",
    "run_analyst": "分析师研判",
    "aggregate_views": "汇总观点",
    "debate_node": "多空辩论",
    "consensus_node": "形成共识",
    "risk_node": "风控审查",
    "trader_node": "制定交易计划",
    "abstain": "风险规避",
    "finalize": "报告生成",
}


@app.post("/api/analysis/stream")
def stream_analysis(req: AnalysisRequest, user: dict[str, Any] = Depends(get_current_user)):
    """投研分析 SSE 流式：逐节点推送进展 + 最终结果。"""
    ticker = req.ticker.strip()
    if not ticker:
        raise HTTPException(status_code=400, detail="请输入股票代码或名称")
    resolved = _resolve_ticker(ticker)
    if not resolved:
        raise HTTPException(status_code=400, detail=f"无法识别 {ticker}")
    ticker = resolved

    def _sse(obj: dict) -> str:
        import json
        return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

    def _generate():
        try:
            yield _sse({"type": "step", "node": "collect_data", "label": "数据收集", "status": "running"})
            config: dict[str, Any] = {"configurable": {}}
            state: dict[str, Any] = {"ticker": ticker, "topic": req.topic, "user_id": user["id"]}

            for chunk in _GRAPH.stream(state, config=config, stream_mode="updates"):
                for node_name, node_output in chunk.items():
                    label = _NODE_LABELS.get(node_name, node_name)
                    # 推送步骤进展
                    yield _sse({"type": "step", "node": node_name, "label": label, "status": "done"})

                    # 如果是分析师节点，推送观点摘要
                    if node_name == "run_analyst" and isinstance(node_output, dict):
                        vm = node_output.get("view_map", {})
                        for role, view in vm.items():
                            yield _sse({
                                "type": "analyst",
                                "role": role,
                                "title": getattr(view, "title", role),
                                "summary": getattr(view, "summary", ""),
                                "score": getattr(view, "score", 0),
                            })

                    # 风控审查结果推送（Human-in-the-loop：前端展示确认按钮）
                    if node_name == "risk_node" and isinstance(node_output, dict):
                        review = node_output.get("risk_review")
                        if review:
                            yield _sse({
                                "type": "risk_review",
                                "approved": getattr(review, "approved", True),
                                "verdict": getattr(review, "verdict", ""),
                                "max_position_pct": getattr(review, "max_position_pct", 0),
                                "stop_loss_pct": getattr(review, "stop_loss_pct", 0),
                            })

                    # finalize 节点推送最终结果
                    if node_name == "finalize" and isinstance(node_output, dict):
                        result = node_output.get("result")
                        if result:
                            yield _sse({"type": "result", "data": result})

            yield _sse({"type": "done"})
        except Exception as e:
            yield _sse({"type": "error", "message": str(e)})

    return StreamingResponse(_generate(), media_type="text/event-stream")


@app.get("/api/analysis/{analysis_id}")
def get_one(analysis_id: int, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    row = memory.get_analysis(analysis_id)
    if row is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    return row


@app.get("/api/history")
def history(limit: int = 20, user: dict[str, Any] = Depends(get_current_user)) -> list[dict[str, Any]]:
    return memory.list_analyses(limit=min(limit, 100), user_id=user["id"])


@app.delete("/api/history/{analysis_id}")
def delete_history(analysis_id: int, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, str]:
    ok = memory.delete_analysis(analysis_id, user_id=user["id"])
    return {"status": "ok" if ok else "not_found"}


# ---------- 行情 K 线 ----------

@app.get("/api/quote/{symbol}")
def quote(symbol: str, days: int = 120, mode: str = "day", fresh: int = 0, all: int = 0) -> dict[str, Any]:
    """行情接口：brief(实时概览) + kline(日K/分时/全量) + tech(技术指标)。

    - mode=day 日K线；mode=minute 当日分时
    - all=1 全量历史K线（至上市以来，A股/港股 akshare 新浪/腾讯源，美股新浪）
    - fresh=1 绕过行情缓存（实时刷新最新价，配合前端轮询）
    """
    sym = datalayer._norm_symbol(symbol)
    brief = datalayer.get_stock_brief(sym, fresh=bool(fresh))
    if not brief:
        raise HTTPException(404, f"未查询到 {symbol} 行情")

    out: dict[str, Any] = {"brief": brief, "kline": [], "tech": {}}
    if mode == "minute":
        m = datalayer.get_minute_kline(sym)
        if m:
            out["kline"] = m["points"]
            out["last_close"] = m["last_close"]
            out["data_date"] = m.get("data_date", "")
            out["is_today"] = m.get("is_today", True)
    elif all:
        hist = datalayer.get_history_all(sym)
        bars: list[dict[str, Any]] = []
        if hist is not None and not hist.empty:
            for _, row in hist.iterrows():
                bars.append({
                    "date": str(row["date"].date()),
                    "open": _num(row["open"]),
                    "close": _num(row["close"]),
                    "high": _num(row["high"]),
                    "low": _num(row["low"]),
                    "volume": _num(row["volume"]),
                })
            out["tech"] = datalayer.compute_tech_signals(hist) or {}
        out["kline"] = bars
    else:
        days = min(max(days, 30), 500)
        hist = datalayer.get_history(sym, days=days)
        bars = []
        if hist is not None and not hist.empty:
            for _, row in hist.tail(days).iterrows():
                bars.append({
                    "date": str(row["date"].date()),
                    "open": _num(row["open"]),
                    "close": _num(row["close"]),
                    "high": _num(row["high"]),
                    "low": _num(row["low"]),
                    "volume": _num(row["volume"]),
                })
            out["tech"] = datalayer.compute_tech_signals(hist) or {}
        out["kline"] = bars
    return out


@app.get("/api/search/{q}")
def search(q: str) -> dict[str, Any]:
    """股票搜索（代码/名称/拼音，A股/港股/美股）。"""
    cache_key = f"search:{q}"
    result = cached(cache_key, 3600, lambda: {"query": q, "results": datalayer.search_stocks(q, limit=8) or []})
    return result


@app.get("/api/news/{symbol}")
def news(symbol: str) -> dict[str, Any]:
    """个股新闻（实时快讯过滤 + 东财兜底）。"""
    sym = datalayer._norm_symbol(symbol)
    cache_key = f"news:{sym}"
    result = cached(cache_key, TTL["news"], lambda: {"symbol": sym, "news": datalayer.get_news(sym) or []})
    return result


@app.get("/api/hot")
def hot_stocks() -> list[dict[str, Any]]:
    """每日热门股票（涨幅排序，动态变化）。"""
    cache_key = "hot_stocks"
    result = cached(cache_key, 300, lambda: datalayer.get_hot_stocks())
    return result or []


@app.get("/api/industry/{symbol}")
def industry_compare(symbol: str) -> dict[str, Any]:
    """行业对比：同行 PE/PB 均值。"""
    sym = datalayer._norm_symbol(symbol)
    cache_key = f"industry:{sym}"
    result = cached(cache_key, TTL["financials"], lambda: datalayer.get_industry_compare(sym) or {"peers": [], "avg_pe": None, "avg_pb": None})
    return result


@app.get("/api/sentiment/{symbol}")
def sentiment_data(symbol: str) -> dict[str, Any]:
    """社交情绪面数据：东财人气榜+雪球关注+主力资金流+情绪评分。"""
    sym = datalayer._norm_symbol(symbol)
    cache_key = f"sentiment:{sym}"
    result = cached(cache_key, 900, lambda: datalayer.get_social_sentiment(sym) or {"error": "暂无情绪数据"})
    return result


@app.get("/api/dcf/{symbol}")
def dcf_valuation(symbol: str) -> dict[str, Any]:
    """DCF现金流折现估值。"""
    sym = datalayer._norm_symbol(symbol)
    cache_key = f"dcf:{sym}"
    result = cached(cache_key, TTL["financials"], lambda: valuation.compute_dcf(sym) or {"error": "无法计算估值（财务数据不足）"})
    return result


# ---------- 投资组合 ----------

@app.get("/api/portfolio")
def get_portfolio_api(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    """获取投资组合：持仓+实时盈亏+总览。"""
    return portfolio.get_portfolio(user["id"])


@app.post("/api/portfolio/buy")
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


@app.post("/api/portfolio/sell")
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


@app.delete("/api/portfolio/{symbol}")
def remove_position_api(symbol: str, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, str]:
    """删除持仓。"""
    ok = portfolio.remove_position(user["id"], datalayer._norm_symbol(symbol))
    return {"status": "ok" if ok else "not_found"}


@app.get("/api/portfolio/transactions")
def transactions_api(user: dict[str, Any] = Depends(get_current_user)) -> list[dict[str, Any]]:
    """交易历史。"""
    return portfolio.list_transactions(user["id"])


# ---------- 回测系统 ----------

@app.get("/api/analysts")
def list_analysts() -> list[dict[str, str]]:
    """返回所有可用分析师列表。"""
    from .agents.analysts import ALL_ANALYSTS
    return [{"role": cls.role, "title": cls.title, "description": getattr(cls, "__doc__", "")} for cls in ALL_ANALYSTS]


@app.put("/api/auth/analyst-config")
def save_analyst_config(body: dict[str, Any], user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    """保存用户分析师配置（启用哪些分析师）。"""
    enabled = body.get("enabled_analysts")
    if not isinstance(enabled, list):
        raise HTTPException(status_code=400, detail="enabled_analysts must be a list")
    from .agents.analysts import ALL_ANALYSTS
    valid_roles = {cls.role for cls in ALL_ANALYSTS}
    enabled = [r for r in enabled if r in valid_roles]
    auth.update_profile(user["id"], analyst_config=enabled)
    return {"enabled_analysts": enabled}


@app.get("/api/multi-period/{symbol}")
def multi_period_api(symbol: str) -> dict[str, Any]:
    """多周期共振分析：日线/周线/月线趋势是否一致。"""
    from .multi_period import get_multi_period_analysis
    result = get_multi_period_analysis(symbol)
    return result or {"error": "数据不足（需要至少60个交易日）"}


@app.get("/api/kline/{symbol}")
def kline_multi_period_api(symbol: str, period: str = "day", count: int = 250) -> dict[str, Any]:
    """多周期K线数据。period: day/week/month/5min/15min/30min/60min
    分钟级默认显示最近5个交易日，日K/周K/月K显示全部。
    """
    sym = datalayer._norm_symbol(symbol)
    # 分钟级默认只取最近5个交易日的数据
    if period.endswith("min"):
        bars_per_day = {"5min": 48, "15min": 16, "30min": 8, "60min": 4}
        count = min(count, bars_per_day.get(period, 48) * 5)  # 最近5个交易日
    df = datalayer.get_history_multi(sym, period=period, count=count)
    if df is None or len(df) == 0:
        return {"error": "数据不足"}
    bars = []
    for _, row in df.iterrows():
        bars.append({
            "date": row["date"].strftime("%Y-%m-%d %H:%M") if period.endswith("min") else row["date"].strftime("%Y-%m-%d"),
            "open": round(float(row["open"]), 4),
            "close": round(float(row["close"]), 4),
            "high": round(float(row["high"]), 4),
            "low": round(float(row["low"]), 4),
            "volume": int(row["volume"]),
        })
    # 技术指标
    tech = datalayer.compute_tech_signals(df)
    return {"symbol": sym, "period": period, "bars": bars, "tech": tech}


@app.get("/api/fund-flow/{symbol}")
def fund_flow_api(symbol: str, days: int = 10) -> dict[str, Any]:
    """个股资金流向：主力/超大单/大单净流入。"""
    from .fund_flow import get_fund_flow
    sym = datalayer._norm_symbol(symbol)
    result = get_fund_flow(sym, days=days)
    return result or {"error": "资金流向数据获取失败（可能为港股美股或东财接口超时）"}


@app.get("/api/patterns/{symbol}")
def patterns_api(symbol: str) -> dict[str, Any]:
    """K线形态自动识别。"""
    from .patterns import get_pattern_summary
    sym = datalayer._norm_symbol(symbol)
    df = datalayer.get_history(sym, days=30)
    if df is None or len(df) < 3:
        return {"error": "数据不足"}
    result = get_pattern_summary(df)
    return result or {"pattern": None, "description": "近期无明显K线形态"}


@app.get("/api/backtest/analysis/{symbol}")
def backtest_analysis_api(
    symbol: str,
    strategy: str = "ma_cross",
    days: int = 120,
    analysis_type: str = "full",
) -> dict[str, Any]:
    """回测深度分析：PF/RF/综合评分 + 蒙特卡洛 + 分层测试 + 参数敏感度。

    analysis_type: full / monte_carlo / layered / sensitivity / score
    """
    from . import backtest_analysis as ba
    sym = datalayer._norm_symbol(symbol)

    if analysis_type == "monte_carlo":
        return ba.run_monte_carlo(sym, strategy=strategy, days=days)
    elif analysis_type == "layered":
        return ba.run_layered_test(sym, days=days)
    elif analysis_type == "sensitivity":
        return ba.run_parameter_sensitivity(sym, strategy=strategy, days=days)
    elif analysis_type == "score":
        r = backtest.run_backtest(sym, strategy=strategy, days=days)
        if not r:
            return {"error": "数据不足"}
        pf = ba.calc_profit_factor(r["trades_log"])
        rf = ba.calc_recovery_factor(r["final_value"] - 100000, r["max_drawdown"])
        score = ba.calc_comprehensive_score(r["total_return"], r["max_drawdown"], pf, rf, r["trades"])
        return {"profit_factor": pf, "recovery_factor": rf, "score": score}
    else:
        return ba.run_full_analysis(sym, strategy=strategy, days=days)


@app.get("/api/backtest/{symbol}")
def backtest_api(symbol: str, strategy: str = "ma_cross", days: int = 120, record_signals: int = 0, enable_cost: int = 1) -> dict[str, Any]:
    """策略回测：在历史K线上模拟交易策略。
    strategy: ma_cross / grid / hold / ai
    record_signals: 1=记录ML信号特征快照（生成CSV供训练用）
    enable_cost: 1=含A股交易成本(印花税+佣金+过户费), 0=不含
    """
    sym = datalayer._norm_symbol(symbol)
    result = backtest.run_backtest(sym, strategy=strategy, days=days, record_signals=bool(record_signals), enable_cost=bool(enable_cost))
    return result or {"error": "回测数据不足（需要至少30个交易日）"}


# ---------- 多LLM对比 ----------

@app.post("/api/llm-compare")
async def llm_compare_api(request: Request, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    """多LLM模型对比：同一prompt调用多个模型，对比回答。需要登录。
    body: {prompt, models: [{name, base_url, api_key, model}]}
    """
    body = await request.json()
    prompt = body.get("prompt", "").strip()
    models = body.get("models", [])
    if not prompt or not models:
        raise HTTPException(400, "请提供prompt和models列表")
    results = llm_compare.compare_models(prompt, models)
    return {"results": results}


@app.get("/api/peers")
def list_peers() -> list[dict[str, Any]]:
    """列出所有行业同行映射。"""
    return chat_service.list_industry_peers()


@app.put("/api/peers/{code}")
async def save_peer(code: str, request: Request) -> dict[str, str]:
    """新增或更新行业同行映射。"""
    body = await request.json()
    chat_service.save_peers(code, body.get("name", code), body.get("peers", []))
    return {"status": "ok"}


@app.delete("/api/peers/{code}")
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

@app.get("/api/alerts")
def list_alerts_api(user: dict[str, Any] = Depends(get_current_user), status: str = "all") -> list[dict[str, Any]]:
    """列出用户的预警规则。status: active/triggered/all。"""
    return alert.list_alerts(user["id"], status=status)


@app.post("/api/alerts")
async def create_alert_api(request: Request, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    """创建价格预警。

    body: {symbol, symbol_name, alert_type, threshold}
    alert_type: price_above / price_below / change_pct_up / change_pct_down
    """
    body = await request.json()
    symbol = (body.get("symbol") or "").strip()
    if not symbol:
        raise HTTPException(400, "请提供股票代码")
    resolved = _resolve_ticker(symbol)
    if not resolved:
        raise HTTPException(400, f"无法识别 {symbol}")
    alert_type = body.get("alert_type", "")
    if alert_type not in ("price_above", "price_below", "change_pct_up", "change_pct_down"):
        raise HTTPException(400, "alert_type 必须为 price_above/price_below/change_pct_up/change_pct_down")
    threshold = float(body.get("threshold", 0))
    if threshold <= 0:
        raise HTTPException(400, "阈值必须大于0")
    symbol_name = body.get("symbol_name", "")
    if not symbol_name:
        brief = datalayer.get_stock_brief(resolved)
        symbol_name = brief.get("name", resolved) if brief else resolved
    return alert.create_alert(user["id"], resolved, symbol_name, alert_type, threshold)


@app.delete("/api/alerts/{alert_id}")
def delete_alert_api(alert_id: int, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, str]:
    """删除预警规则。"""
    ok = alert.delete_alert(alert_id, user["id"])
    return {"status": "ok" if ok else "not_found"}


@app.post("/api/alerts/{alert_id}/reactivate")
def reactivate_alert_api(alert_id: int, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, str]:
    """重新激活已触发的预警（re-arm），支持重复触发。"""
    ok = alert.reactivate_alert(alert_id, user["id"])
    return {"status": "ok" if ok else "not_found"}


@app.post("/api/alerts/check")
def check_alerts_api() -> dict[str, Any]:
    """扫描所有 active 预警（定时轮询触发），返回新触发的预警列表。

    前端每30秒轮询此端点，收到触发的预警后弹出通知。
    """
    triggered = alert.check_alerts()
    return {"triggered": triggered, "count": len(triggered)}


# ---------- 智能对话 ----------

@app.post("/api/chat/session")
def new_chat(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    sid = chat_service.create_session(user["id"])
    return {"session_id": sid}


@app.post("/api/chat/stream")
def chat_stream(body: dict[str, Any], user: dict[str, Any] = Depends(get_current_user)) -> StreamingResponse:
    """流式对话（SSE）：实时推送工具调用工作流事件。"""
    message = str(body.get("message", "")).strip()
    if not message:
        raise HTTPException(400, "消息不能为空")
    session_id = body.get("session_id")
    if session_id is None:
        session_id = chat_service.create_session(user["id"])
    session_id = int(session_id)
    return StreamingResponse(
        chat_service.stream_chat(session_id, user["id"], message),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/chat/sessions")
def chat_sessions(user: dict[str, Any] = Depends(get_current_user)) -> list[dict[str, Any]]:
    return chat_service.list_sessions(user["id"])


@app.delete("/api/chat/{session_id}")
def delete_chat(session_id: int, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    ok = chat_service.delete_session(session_id, user["id"])
    if not ok:
        raise HTTPException(404, "会话不存在或无权限")
    return {"deleted": session_id}


@app.get("/api/chat/search")
def chat_search(q: str, user: dict[str, Any] = Depends(get_current_user)) -> list[dict[str, Any]]:
    if not q.strip():
        return []
    return chat_service.search_messages(user["id"], q.strip())


@app.get("/api/chat/{session_id}/messages")
def chat_messages(session_id: int, user: dict[str, Any] = Depends(get_current_user)) -> list[dict[str, Any]]:
    return chat_service.get_messages(session_id, user["id"])


@app.post("/api/chat")
def chat(body: dict[str, Any], user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    message = (body.get("message") or "").strip()
    if not message:
        raise HTTPException(400, "消息不能为空")
    session_id = body.get("session_id")
    if not session_id:
        session_id = chat_service.create_session(user["id"])
    # 校验会话归属
    sessions = {s["id"] for s in chat_service.list_sessions(user["id"], limit=100)}
    if int(session_id) not in sessions:
        raise HTTPException(403, "会话不存在或无权限")
    return chat_service.chat(int(session_id), user["id"], message)


# ---------- 管理员 API ----------

@app.get("/api/admin/users")
def admin_list_users(admin: dict[str, Any] = Depends(require_admin)) -> list[dict[str, Any]]:
    return auth.list_all_users()


@app.post("/api/admin/users/{user_id}/toggle-active")
def admin_toggle_user(user_id: int, admin: dict[str, Any] = Depends(require_admin)) -> dict[str, str]:
    ok = auth.toggle_user_active(user_id)
    auth.audit_log(admin["id"], admin["username"], "toggle_user", f"target_id={user_id}")
    return {"status": "ok" if ok else "not_found"}


@app.post("/api/admin/users/{user_id}/set-admin")
async def admin_set_admin(user_id: int, request: Request, admin: dict[str, Any] = Depends(require_admin)) -> dict[str, str]:
    body = await request.json()
    ok = auth.set_user_admin(user_id, bool(body.get("is_admin", False)))
    auth.audit_log(admin["id"], admin["username"], "set_admin", f"target_id={user_id} value={body.get('is_admin')}")
    return {"status": "ok" if ok else "not_found"}


@app.post("/api/admin/invite-codes")
async def admin_create_invite(request: Request, admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    body = await request.json()
    note = body.get("note", "")
    code = auth.create_invite_code(admin["id"], note)
    auth.audit_log(admin["id"], admin["username"], "create_invite", f"code={code['code']}")
    return code


@app.get("/api/admin/invite-codes")
def admin_list_invites(admin: dict[str, Any] = Depends(require_admin)) -> list[dict[str, Any]]:
    return auth.list_invite_codes()


@app.get("/api/admin/audit-logs")
def admin_audit_logs(admin: dict[str, Any] = Depends(require_admin), limit: int = 100) -> list[dict[str, Any]]:
    return auth.list_audit_logs(limit)


@app.get("/api/admin/stats")
def admin_stats(admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    return auth.get_system_stats()


@app.get("/api/auth/is-admin")
def check_is_admin(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return {"is_admin": bool(user.get("is_admin"))}


# 前端静态托管（构建后可用）-- index.html 不缓存确保加载最新 JS
if FRONTEND_DIST.exists():
    from starlette.middleware.base import BaseHTTPMiddleware

    class NoCacheHtmlMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            resp = await call_next(request)
            if request.url.path == '/' or request.url.path.endswith('.html'):
                resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
            return resp

    app.add_middleware(NoCacheHtmlMiddleware)
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")
