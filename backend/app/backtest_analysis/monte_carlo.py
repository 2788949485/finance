"""回测深度分析 - monte_carlo模块"""
from __future__ import annotations
from typing import Any
import random
import math


def run_monte_carlo(
    symbol: str,
    strategy: str = "ma_cross",
    days: int = 120,
    initial_capital: float = 100000.0,
    simulations: int = 1000,
    scenarios: list[str] | None = None,
) -> dict[str, Any]:
    """蒙特卡洛压力测试。

    对原始交易结果进行多种随机扰动，模拟实盘中的不确定性：
    - 打乱交易顺序
    - 随机滑点
    - 随机漏单
    - 盈利缩减/亏损扩大
    - 点差扩大

    参数:
        symbol: 股票代码
        strategy: 策略
        days: 回测天数
        initial_capital: 初始资金
        simulations: 模拟次数（默认1000）
        scenarios: 测试场景列表，默认全部

    返回:
    {
        original: {total_return, max_drawdown, ...},  # 原始回测结果
        simulations: int,
        p95_max_drawdown: float,   # 95%分位最大回撤
        p99_max_drawdown: float,   # 99%分位
        worst_max_drawdown: float, # 最差情况
        worst_consecutive_losses: int,
        blowup_probability: float, # 爆仓概率
        final_value_p5: float,     # 5%分位期末净值
        final_value_p50: float,    # 中位数
        final_value_p95: float,    # 95%分位
        recovery_time_p95: int,    # 95%分位回撤恢复时间
        scenario_breakdown: dict,  # 各场景结果
    }
    """
    if scenarios is None:
        scenarios = ["shuffle", "slippage", "miss", "spread_widen", "profit_cut", "loss_expand"]

    # 原始回测
    original = bt.run_backtest(symbol, strategy=strategy, days=days, initial_capital=initial_capital)
    if not original or not original.get("trades_log"):
        return {"error": "回测数据不足或无交易"}

    original_trades = original["trades_log"]
    original_return = original["total_return"]
    original_dd = original["max_drawdown"]

    # 从交易记录提取每笔完整交易的盈亏（买入-卖出配对）
    trade_pnls = _extract_trade_pnls(original_trades)

    if not trade_pnls:
        return {"error": "无法提取完整交易对"}

    # 运行蒙特卡洛模拟
    all_final_returns = []
    all_max_drawdowns = []
    all_consecutive_losses = []
    blowups = 0
    rng = random.Random(42)

    for _ in range(simulations):
        # 随机选择一个扰动场景
        scenario = rng.choice(scenarios)
        modified_pnls = _apply_perturbation(trade_pnls, scenario, rng)

        # 计算权益曲线
        equity = [initial_capital]
        for pnl in modified_pnls:
            equity.append(equity[-1] + pnl)
            # 爆仓检查
            if equity[-1] <= 0:
                blowups += 1
                break

        if equity[-1] <= 0:
            continue

        total_ret = (equity[-1] / initial_capital - 1) * 100
        all_final_returns.append(total_ret)

        # 最大回撤
        peak = initial_capital
        max_dd = 0
        for v in equity:
            if v > peak:
                peak = v
            dd = (peak - v) / peak * 100
            if dd > max_dd:
                max_dd = dd
        all_max_drawdowns.append(max_dd)

        # 连续亏损
        consec = _max_consecutive_losses(modified_pnls)
        all_consecutive_losses.append(consec)

    # 统计
    results = {
        "original_return": round(original_return, 2),
        "original_drawdown": round(original_dd, 2),
        "simulations": len(all_final_returns),
        "p95_max_drawdown": round(np.percentile(all_max_drawdowns, 95), 2) if all_max_drawdowns else 0,
        "p99_max_drawdown": round(np.percentile(all_max_drawdowns, 99), 2) if all_max_drawdowns else 0,
        "worst_max_drawdown": round(max(all_max_drawdowns), 2) if all_max_drawdowns else 0,
        "worst_consecutive_losses": max(all_consecutive_losses) if all_consecutive_losses else 0,
        "blowup_probability": round(blowups / simulations * 100, 2),
        "final_return_p5": round(np.percentile(all_final_returns, 5), 2) if all_final_returns else 0,
        "final_return_p50": round(np.percentile(all_final_returns, 50), 2) if all_final_returns else 0,
        "final_return_p95": round(np.percentile(all_final_returns, 95), 2) if all_final_returns else 0,
        # 分布直方图数据（20个桶）
        "histogram": _build_histogram(all_final_returns, 20),
        # 回撤分布直方图
        "drawdown_histogram": _build_histogram(all_max_drawdowns, 20),
    }

    # 仓位建议：基于95%分位回撤
    if results["p95_max_drawdown"] > 0:
        suggested_risk = min(20.0 / results["p95_max_drawdown"], 1.0)
        results["suggested_position_ratio"] = round(suggested_risk, 2)

    return results




def _build_histogram(data: list[float], bins: int = 20) -> list[dict]:
    """构建直方图数据，返回[{bin_start, bin_end, count, label}, ...]"""
    if not data or len(data) < 2:
        return []
    arr = np.array(data)
    counts, edges = np.histogram(arr, bins=bins)
    result = []
    for i in range(len(counts)):
        result.append({
            "bin_start": round(float(edges[i]), 2),
            "bin_end": round(float(edges[i + 1]), 2),
            "count": int(counts[i]),
            "label": f"{edges[i]:.1f}~{edges[i+1]:.1f}",
        })
    return result




def _extract_trade_pnls(trades_log: list[dict]) -> list[float]:
    """从交易记录提取每笔完整交易的盈亏。"""
    pnls = []
    shares = 0
    buy_price = 0.0
    for t in trades_log:
        if t["action"] == "BUY":
            shares = t["shares"]
            buy_price = t["price"]
        elif t["action"] == "SELL" and shares > 0:
            pnls.append((t["price"] - buy_price) * shares)
            shares = 0
    return pnls




def _max_consecutive_losses(pnls: list[float]) -> int:
    """计算最大连续亏损次数。"""
    max_streak = 0
    current = 0
    for p in pnls:
        if p < 0:
            current += 1
            max_streak = max(max_streak, current)
        else:
            current = 0
    return max_streak




def _apply_perturbation(pnls: list[float], scenario: str, rng: random.Random) -> list[float]:
    """对交易盈亏应用随机扰动。"""
    result = list(pnls)

    if scenario == "shuffle":
        # 随机打乱交易顺序
        rng.shuffle(result)

    elif scenario == "slippage":
        # 随机滑点（每笔减少0.1%-0.5%）
        for i in range(len(result)):
            slippage = rng.uniform(0.001, 0.005) * abs(result[i])
            result[i] -= slippage

    elif scenario == "miss":
        # 随机漏掉5%的交易
        result = [p for p in result if rng.random() > 0.05]

    elif scenario == "spread_widen":
        # 点差扩大（每笔成本增加20%-50%）
        for i in range(len(result)):
            cost = rng.uniform(0.002, 0.005) * abs(result[i])
            result[i] -= cost

    elif scenario == "profit_cut":
        # 盈利减少5%-10%
        for i in range(len(result)):
            if result[i] > 0:
                result[i] *= rng.uniform(0.90, 0.95)

    elif scenario == "loss_expand":
        # 亏损扩大5%-15%
        for i in range(len(result)):
            if result[i] < 0:
                result[i] *= rng.uniform(1.05, 1.15)

    return result


# ==================== 3. 分层测试（逐个加入过滤器） ====================

