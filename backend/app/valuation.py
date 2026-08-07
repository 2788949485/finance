"""DCF（现金流折现）估值模型。

基于已有财务数据（营收/净利润/增长率）计算内在价值。
三阶段模型：高速增长期(5年) → 过渡期(5年) → 永续增长。

参数说明：
  fcf_margin: 自由现金流占净利润比例（默认80%，A股平均）
  high_growth: 前5年增长率（用历史营收增速，上限25%）
  terminal_growth: 永续增长率（默认3%）
  discount_rate: WACC折现率（默认10%，A股大盘平均）
"""
from __future__ import annotations

from typing import Any, Optional

from .data import fetcher as datalayer


def compute_dcf(symbol: str) -> Optional[dict[str, Any]]:
    """计算DCF估值。

    返回 {
        current_price: float,      # 当前价格
        intrinsic_value: float,    # 内在价值(每股)
        upside_pct: float,         # 上行空间(%)，正=低估 负=高估
        assumptions: dict,         # 假设参数
        projections: list,         # 10年FCF预测
        verdict: str,              # 结论文字
    }
    """
    sym = datalayer._norm_symbol(symbol)

    # 获取财务数据
    fin = datalayer.get_financials(sym)
    if not fin or not fin.get("net_profit") or not fin.get("revenue"):
        return None

    net_profit = fin.get("net_profit") or 0
    revenue = fin.get("revenue") or 0
    revenue_yoy = fin.get("revenue_yoy") or 0  # 百分比
    net_profit_yoy = fin.get("net_profit_yoy") or revenue_yoy

    # 年化处理：如果period是季度报告（含月日），乘以4
    period = str(fin.get("period", ""))
    if any(m in period for m in ["03-31", "06-30"]):
        # Q1或H1报告 → 年化（简单×4或×2）
        if "03-31" in period:
            net_profit *= 4
            revenue *= 4
        elif "06-30" in period:
            net_profit *= 2
            revenue *= 2
    elif "09-30" in period:
        # 三季报 → 加上Q4估算（Q4≈前三季的1/3）
        net_profit *= 4 / 3
        revenue *= 4 / 3

    # 获取当前价格和市值
    brief = datalayer.get_stock_brief(sym)
    if not brief or not brief.get("price"):
        return None
    current_price = float(brief["price"])
    market_cap = brief.get("market_cap")  # 亿元

    # DCF 假设参数
    FCF_MARGIN = 0.80       # 自由现金流 = 净利润 × 80%
    HIGH_GROWTH_YEARS = 5   # 高增长年数
    TRANSITION_YEARS = 5    # 过渡年数
    TERMINAL_GROWTH = 0.03  # 永续增长率 3%
    DISCOUNT_RATE = 0.10    # WACC 10%

    # 增长率：用历史增速，但限制在合理范围 [-5%, 25%]
    base_growth = max(-0.05, min(0.25, net_profit_yoy / 100))
    if base_growth <= 0:
        # 港股/美股 yfinance 可能无增速数据，用收入增速兜底，再不行用默认值
        if revenue_yoy and revenue_yoy > 0:
            base_growth = max(-0.05, min(0.25, revenue_yoy / 100))
        else:
            base_growth = 0.05  # 最低5%增长

    # 阶段1：高增长期（每年递减，从base_growth到base_growth*0.6）
    # 阶段2：过渡期（从base_growth*0.6线性降到terminal_growth）
    projections = []
    fcf = net_profit * FCF_MARGIN
    pv_total = 0.0

    for year in range(1, HIGH_GROWTH_YEARS + 1):
        # 高增长期：线性衰减
        decline = (base_growth - base_growth * 0.6) * (year - 1) / HIGH_GROWTH_YEARS
        growth_rate = base_growth - decline
        fcf *= (1 + growth_rate)
        pv = fcf / ((1 + DISCOUNT_RATE) ** year)
        pv_total += pv
        projections.append({
            "year": year,
            "growth_rate": round(growth_rate * 100, 1),
            "fcf": round(fcf / 1e8, 2),       # 亿元
            "pv": round(pv / 1e8, 2),         # 亿元
        })

    # 阶段2：过渡期
    transition_start = base_growth * 0.6
    for year in range(1, TRANSITION_YEARS + 1):
        growth_rate = transition_start - (transition_start - TERMINAL_GROWTH) * year / TRANSITION_YEARS
        fcf *= (1 + growth_rate)
        pv = fcf / ((1 + DISCOUNT_RATE) ** (HIGH_GROWTH_YEARS + year))
        pv_total += pv
        projections.append({
            "year": HIGH_GROWTH_YEARS + year,
            "growth_rate": round(growth_rate * 100, 1),
            "fcf": round(fcf / 1e8, 2),
            "pv": round(pv / 1e8, 2),
        })

    # 终值
    terminal_fcf = fcf * (1 + TERMINAL_GROWTH)
    terminal_value = terminal_fcf / (DISCOUNT_RATE - TERMINAL_GROWTH)
    terminal_pv = terminal_value / ((1 + DISCOUNT_RATE) ** (HIGH_GROWTH_YEARS + TRANSITION_YEARS))
    pv_total += terminal_pv

    # 计算每股内在价值
    # pv_total 是未来现金流现值总和（元），需要除以总股本
    # 市值(亿元) / 价格 = 总股本(亿股)
    total_shares = (market_cap / current_price) if market_cap and current_price else None
    if not total_shares or total_shares <= 0:
        return None

    intrinsic_value = pv_total / (total_shares * 1e8)  # 元/股
    upside_pct = (intrinsic_value - current_price) / current_price * 100

    # 结论判断
    if upside_pct > 20:
        verdict = f"严重低估，内在价值约 {intrinsic_value:.2f} 元，较现价有 {upside_pct:.1f}% 上行空间"
    elif upside_pct > 5:
        verdict = f"有所低估，内在价值约 {intrinsic_value:.2f} 元，上行空间 {upside_pct:.1f}%"
    elif upside_pct > -5:
        verdict = f"估值合理，内在价值约 {intrinsic_value:.2f} 元，与现价接近"
    elif upside_pct > -20:
        verdict = f"有所高估，内在价值约 {intrinsic_value:.2f} 元，偏离 {upside_pct:.1f}%"
    else:
        verdict = f"严重高估，内在价值约 {intrinsic_value:.2f} 元，偏离 {upside_pct:.1f}%"

    return {
        "current_price": current_price,
        "intrinsic_value": round(intrinsic_value, 2),
        "upside_pct": round(upside_pct, 1),
        "terminal_value": round(terminal_pv / 1e8, 2),
        "assumptions": {
            "base_growth": round(base_growth * 100, 1),
            "terminal_growth": round(TERMINAL_GROWTH * 100, 1),
            "discount_rate": round(DISCOUNT_RATE * 100, 1),
            "fcf_margin": round(FCF_MARGIN * 100, 0),
            "net_profit": round(net_profit / 1e8, 2),
            "revenue": round(revenue / 1e8, 2),
        },
        "projections": projections,
        "verdict": verdict,
    }
