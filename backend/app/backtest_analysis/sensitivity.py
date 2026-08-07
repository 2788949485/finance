"""回测深度分析 - sensitivity模块"""
from __future__ import annotations
from typing import Any
import pandas as pd


def run_parameter_sensitivity(
    symbol: str,
    strategy: str = "ma_cross",
    days: int = 120,
    initial_capital: float = 100000.0,
) -> dict[str, Any]:
    """参数敏感性分析：测试关键参数上下浮动后的表现。

    找"稳定平台"而非"最高点"——
    健康的参数是周围参数变化后仍然能盈利的参数。
    """
    sym = datalayer._norm_symbol(symbol)

    # 测试MA周期组合
    ma_combos = [
        (3, 10), (3, 15), (3, 20),
        (5, 10), (5, 15), (5, 20), (5, 25),
        (7, 15), (7, 20), (7, 25), (7, 30),
        (10, 20), (10, 25), (10, 30), (10, 40),
        (12, 26), (15, 30), (15, 40), (20, 40), (20, 60),
    ]

    results = []
    for fast, slow in ma_combos:
        r = bt.run_backtest(sym, strategy=strategy, days=days, initial_capital=initial_capital,
                           fast_period=fast, slow_period=slow)
        if r and r.get("trades_log"):
            pf = calc_profit_factor(r["trades_log"])
            results.append({
                "fast": fast,
                "slow": slow,
                "total_return": r["total_return"],
                "max_drawdown": r["max_drawdown"],
                "trades": len(r["trades_log"]),
                "pf": pf,
            })

    if not results:
        return {"error": "无有效结果"}

    # 分析稳定性
    returns = [r["total_return"] for r in results]
    dds = [r["max_drawdown"] for r in results]
    pfs = [r["pf"] for r in results]

    # 找稳定平台：收益标准差小、中位数收益正的区域
    median_return = float(np.median(returns))
    std_return = float(np.std(returns))
    profitable_count = sum(1 for r in returns if r > 0)

    # 找最优参数（不是最高收益，是综合最好的）
    best = max(results, key=lambda x: x["total_return"])
    worst = min(results, key=lambda x: x["total_return"])

    return {
        "symbol": sym,
        "param_grid": "MA快线 x MA慢线",
        "combos_tested": len(results),
        "results": results,
        "median_return": round(median_return, 2),
        "std_return": round(std_return, 2),
        "profitable_ratio": round(profitable_count / len(results) * 100, 1),
        "best": best,
        "worst": worst,
        "stability_verdict": _verdict_stability(returns, dds, pfs),
    }




def _verdict_stability(returns: list[float], dds: list[float], pfs: list[float]) -> str:
    """判断参数稳定性。"""
    profitable = sum(1 for r in returns if r > 0) / len(returns)
    std = float(np.std(returns))

    if profitable > 0.7 and std < 10:
        return "STABLE: 多数参数组合盈利且波动小，策略稳健"
    elif profitable > 0.5:
        return "MODERATE: 一半参数组合盈利，策略有一定依赖性"
    elif profitable > 0.3:
        return "SENSITIVE: 少数参数组合盈利，策略对参数敏感"
    else:
        return "UNSTABLE: 大多数参数组合亏损，策略不稳健"


# ==================== 5. 一键完整分析 ====================

