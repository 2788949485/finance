"""分析师工具集：每个工具是一个可被 LLM 调用的函数。
工具返回结构化数据文本，LLM 根据返回内容决定下一步。

设计要点：
- 每个工具一个纯函数 (ticker) -> str，错误自吞自报
- TOOL_REGISTRY 全局注册表（装饰器自动登记）
- ANALYST_TOOLS 按角色限定可用工具集（最小权限）
"""
from __future__ import annotations

from typing import Callable

# 工具注册表：name -> 函数
TOOL_REGISTRY: dict[str, Callable] = {}


def tool(name: str, description: str):
    """装饰器：把函数注册到 TOOL_REGISTRY，并打上 __tool_name__/__tool_desc__。"""

    def decorator(fn):
        fn.__tool_name__ = name
        fn.__tool_desc__ = description
        TOOL_REGISTRY[name] = fn
        return fn

    return decorator


# --- 数据层懒加载辅助 ---
# 注意：data.__init__ 未导出 get_industry_compare，故按需从 fetcher 直接取
def _datalayer():
    from .. import data as datalayer

    return datalayer


def _get_industry_compare(ticker: str):
    from .. import data as datalayer

    fn = getattr(datalayer, "get_industry_compare", None)
    if fn is None:
        from ..data import fetcher

        fn = getattr(fetcher, "get_industry_compare", None)
    return fn(ticker) if fn else None


# ==================== 工具实现 ====================


@tool("get_quote", "获取股票基本信息(名称/价格/涨跌幅/PE/PB/市值/换手率)")
def get_quote(ticker: str) -> str:
    dl = _datalayer()
    brief = dl.get_stock_brief(ticker) or {}
    lines = [f"名称: {brief.get('name', '未知')}"]
    for k in ["price", "change_pct", "pe", "pb", "market_cap", "turnover", "volume_ratio"]:
        if brief.get(k) is not None:
            lines.append(f"{k}: {brief[k]}")
    return "\n".join(lines)


@tool("get_kline", "获取K线数据和技术指标(MACD/KDJ/BOLL/均线)")
def get_kline(ticker: str) -> str:
    try:
        dl = _datalayer()
        history = dl.get_history(ticker)
        if history is None or len(history) == 0:
            return "K线数据不可用"
        tech = dl.compute_tech_signals(history)
        lines = [f"最近{len(history)}根K线"]
        if "error" not in tech:
            for k, v in tech.items():
                if isinstance(v, (int, float, str)):
                    lines.append(f"{k}: {v}")
        return "\n".join(lines)
    except Exception as e:
        return f"K线获取失败: {e}"


@tool("get_financials", "获取财务数据(营收/利润/ROE/负债率/现金流)")
def get_financials(ticker: str) -> str:
    try:
        dl = _datalayer()
        fin = dl.get_financials(ticker) or {}
        if not fin:
            return "财务数据不可用"
        lines = []
        for k, v in fin.items():
            if v is not None:
                lines.append(f"{k}: {v}")
        return "\n".join(lines[:20])
    except Exception as e:
        return f"财务数据获取失败: {e}"


@tool("get_news", "获取最新新闻和公告")
def get_news(ticker: str) -> str:
    try:
        dl = _datalayer()
        news = dl.get_news(ticker) or []
        if not news:
            return "无新闻数据"
        lines = [f"共{len(news)}条新闻"]
        for n in news[:5]:
            lines.append(f"- {n.get('title', '')} ({n.get('date') or n.get('time', '')})")
        return "\n".join(lines)
    except Exception as e:
        return f"新闻获取失败: {e}"


@tool("get_lhb", "获取龙虎榜数据(游资/机构席位)")
def get_lhb(ticker: str) -> str:
    try:
        dl = _datalayer()
        lhb = dl.get_lhb(ticker)
        if not lhb:
            return "无龙虎榜数据"
        # 兼容 dict（带 details）和 list 两种形态
        details = lhb.get("details", []) if isinstance(lhb, dict) else []
        lines = [f"龙虎榜上榜{len(details)}次"]
        for d in (details or [])[:3]:
            lines.append(f"- {d.get('date', '')}: {d.get('reason', '')}")
        return "\n".join(lines)
    except Exception as e:
        return f"龙虎榜获取失败: {e}"


@tool("get_industry_compare", "获取行业对比数据(同行业PE/PB/涨跌幅)")
def get_industry_compare(ticker: str) -> str:
    try:
        comp = _get_industry_compare(ticker) or {}
        peers = comp.get("peers", [])
        lines = [f"行业: {len(peers)}家可比公司"]
        lines.append(f"行业均PE: {comp.get('avg_pe', 'N/A')}")
        lines.append(f"行业均PB: {comp.get('avg_pb', 'N/A')}")
        for p in peers[:3]:
            lines.append(f"- {p.get('name', '')}: PE={p.get('pe', 'N/A')} 涨跌={p.get('change_pct', 'N/A')}%")
        return "\n".join(lines)
    except Exception as e:
        return f"行业对比获取失败: {e}"


@tool("get_reflection", "获取该股票的历史决策反思经验")
def get_reflection(ticker: str) -> str:
    try:
        from ..reflection_engine import build_memory_block  # 反思引擎为可选项

        block = build_memory_block(ticker)
        return block if block else "无历史决策反思记录"
    except Exception:
        return "反思系统不可用"


# ==================== 角色工具映射 ====================

# 每个分析师角色对应可用的工具集（最小权限原则）
ANALYST_TOOLS: dict[str, list[str]] = {
    "macro": ["get_quote", "get_news", "get_industry_compare"],
    "fundamental": ["get_quote", "get_financials", "get_industry_compare"],
    "tech": ["get_quote", "get_kline"],
    "sentiment": ["get_quote", "get_news", "get_lhb", "get_reflection"],
    "capital": ["get_quote", "get_kline", "get_lhb", "get_reflection"],
}


def get_tools_for_role(role: str) -> list[Callable]:
    """获取某角色的工具列表（跳过未注册的工具）。"""
    names = ANALYST_TOOLS.get(role, ["get_quote"])
    return [TOOL_REGISTRY[n] for n in names if n in TOOL_REGISTRY]


def build_tool_descriptions(role: str) -> str:
    """构建工具说明文本（给 LLM 看的，仅列可用工具）。"""
    names = ANALYST_TOOLS.get(role, ["get_quote"])
    lines = []
    for n in names:
        fn = TOOL_REGISTRY.get(n)
        if fn:
            # 用注册名 n 作为权威名字（函数可能被替换/lambda 无装饰器属性）
            desc = getattr(fn, "__tool_desc__", "") or ""
            lines.append(f"- {n}(ticker): {desc}")
    return "\n".join(lines)
