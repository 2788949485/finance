"""FinanceCrew 后端 API 入口。

启动: uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import auth, chat as chat_service, memory
from .pipeline import run_analysis, _GRAPH
from .config import PROVIDER_PRESETS, get_config, save_config
from .data import fetcher as datalayer
from .models import AnalysisRequest, LLMConfig

app = FastAPI(title="FinanceCrew API", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

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
def register(body: dict[str, str]) -> dict[str, Any]:
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    if len(username) < 2 or len(username) > 20:
        raise HTTPException(400, "用户名需 2-20 个字符")
    if len(password) < 6:
        raise HTTPException(400, "密码至少 6 位")
    try:
        user = auth.create_user(username, password)
    except ValueError as e:
        raise HTTPException(400, str(e))
    token = auth.create_token(user["id"], user["username"])
    return {"token": token, "user": user, "profile": auth.get_profile(user["id"])}


@app.post("/api/auth/login")
def login(body: dict[str, str]) -> dict[str, Any]:
    user = auth.authenticate((body.get("username") or "").strip(), body.get("password") or "")
    if not user:
        raise HTTPException(401, "用户名或密码错误")
    token = auth.create_token(user["id"], user["username"])
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
def get_one(analysis_id: int) -> dict[str, Any]:
    row = memory.get_analysis(analysis_id)
    if row is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    return row


@app.get("/api/history")
def history(limit: int = 20, user: dict[str, Any] = Depends(get_current_user)) -> list[dict[str, Any]]:
    return memory.list_analyses(limit=min(limit, 100), user_id=user["id"])


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
    items = datalayer.search_stocks(q, limit=8)
    return {"query": q, "results": items or []}


@app.get("/api/news/{symbol}")
def news(symbol: str) -> dict[str, Any]:
    """个股新闻（实时快讯过滤 + 东财兜底）。"""
    sym = datalayer._norm_symbol(symbol)
    items = datalayer.get_news(sym)
    return {"symbol": sym, "news": items or []}


def _num(v: Any):
    try:
        if v is None:
            return None
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


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
