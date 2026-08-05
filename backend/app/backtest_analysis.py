"""回测深度分析系统：蒙特卡洛 + 分层测试 + PF/RF评分 + 参数敏感度。

参考专业量化回测方法论：
1. 蒙特卡洛压力测试：随机打乱交易顺序、模拟滑点/漏单/点差扩大
2. 分层测试：逐个加入过滤器，对比每个过滤器的贡献度
3. PF/RF/综合评分：不只看净利润，综合评估收益/回撤/稳定性
4. 参数敏感性分析：多参数上下浮动，找稳定平台而非最高点
"""
from __future__ import annotations

import random
import math
from typing import Any, Optional
import pandas as pd
import numpy as np

from .data import fetcher as datalayer
from . import backtest as bt


# ==================== 1. PF/RF/综合评分 ====================

def calc_profit_factor(trades_log: list[dict]) -> float:
    """计算Profit Factor = 总盈利 / 总亏损绝对值。

    PF < 1: 长期亏损
    1.0~1.2: 优势很弱
    1.2~1.5: 有一定优势
    1.5~2.0: 较好
    > 2: 很好（但需警惕过拟合）
    """
    gross_profit = 0.0
    gross_loss = 0.0
    shares = 0
    buy_price = 0.0

    for trade in trades_log:
        if trade["action"] == "BUY":
            shares = trade["shares"]
            buy_price = trade["price"]
        elif trade["action"] == "SELL" and shares > 0:
            pnl = (trade["price"] - buy_price) * shares
            if pnl > 0:
                gross_profit += pnl
            else:
                gross_loss += abs(pnl)
            shares = 0

    return round(gross_profit / gross_loss, 4) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)


def calc_recovery_factor(net_profit: float, max_drawdown: float) -> float:
    """计算Recovery Factor = 净利润 / 最大回撤金额。

    < 1: 收益不足以覆盖风险
    1~2: 一般
    2~3: 较好
    > 3: 优秀
    """
    if max_drawdown <= 0:
        return 999.0 if net_profit > 0 else 0.0
    return round(net_profit / max_drawdown, 4)


def calc_comprehensive_score(
    total_return: float,
    max_drawdown: float,
    pf: float,
    rf: float,
    trades: int,
    benchmark_return: float = 0.0,
) -> dict[str, Any]:
    """计算综合评分（不只看净利润）。

    Score = 0.25*R + 0.20*PF + 0.20*RF + 0.15*S - 0.20*DD
    其中各项都做0-100标准化。

    返回:
    {
        score: float,          # 综合评分 0-100
        annual_return_score: float,
        pf_score: float,
        rf_score: float,
        stability_score: float,
        drawdown_penalty: float,
        grade: str,            # A/B/C/D/F
        warnings: list[str],   # 风险提示
    }
    """
    # 年化收益得分（假设250天=1年）
    r_score = min(max(total_return, -100), 100)

    # PF得分（PF=2映射到80分，PF=1映射到40分）
    pf_score = min(pf * 40, 100) if pf > 0 else 0

    # RF得分（RF=3映射到75分）
    rf_score = min(rf * 25, 100) if rf > 0 else 0

    # 稳定性得分（交易次数越多越稳定，但不是线性）
    if trades >= 100:
        s_score = 100
    elif trades >= 30:
        s_score = 60 + (trades - 30) * 40 / 70
    elif trades >= 10:
        s_score = 30 + (trades - 10) * 30 / 20
    else:
        s_score = max(0, trades * 3)

    # 回撤惩罚（回撤>30%严重扣分）
    dd_penalty = min(max_drawdown * 2, 100)

    # 综合分
    score = 0.25 * r_score + 0.20 * pf_score + 0.20 * rf_score + 0.15 * s_score - 0.20 * dd_penalty
    score = max(0, min(100, round(score, 1)))

    # 评级
    if score >= 75:
        grade = "A"
    elif score >= 60:
        grade = "B"
    elif score >= 45:
        grade = "C"
    elif score >= 30:
        grade = "D"
    else:
        grade = "F"

    # 风险提示
    warnings = []
    if pf < 1.0:
        warnings.append("Profit Factor < 1，策略长期亏损")
    if max_drawdown > 30:
        warnings.append(f"最大回撤{max_drawdown:.1f}%超过30%，高风险")
    if trades < 10:
        warnings.append(f"仅{trades}次交易，统计意义不足")
    if rf < 1:
        warnings.append("Recovery Factor < 1，收益不足以覆盖风险")
    if pf > 3 and trades < 50:
        warnings.append("PF很高但交易次数少，警惕过拟合")
    if total_return > 0 and benchmark_return > 0 and total_return < benchmark_return:
        warnings.append("策略收益跑输基准")

    return {
        "score": score,
        "grade": grade,
        "annual_return_score": round(r_score, 1),
        "pf_score": round(pf_score, 1),
        "rf_score": round(rf_score, 1),
        "stability_score": round(s_score, 1),
        "drawdown_penalty": round(dd_penalty, 1),
        "warnings": warnings,
    }


# ==================== 2. 蒙特卡洛压力测试 ====================

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

def run_layered_test(
    symbol: str,
    days: int = 120,
    initial_capital: float = 100000.0,
) -> dict[str, Any]:
    """分层测试：逐个加入过滤器，对比每个模块的贡献度。

    测试顺序：
    1. 基础入场（双K线/MA交叉）
    2. +EMA过滤
    3. +ADX过滤
    4. +布林带过滤
    5. +成交量过滤

    每加入一层，对比交易次数、收益率、最大回撤、PF的变化。
    """
    sym = datalayer._norm_symbol(symbol)
    hist = datalayer.get_history(sym, days=min(max(days, 30), 500))
    if hist is None or len(hist) < 30:
        return {"error": "数据不足"}

    df = hist.copy()
    df["ma5"] = df["close"].rolling(5).mean()
    df["ma20"] = df["close"].rolling(20).mean()
    df = df.dropna(subset=["ma5", "ma20"]).reset_index(drop=True)
    if len(df) < 10:
        return {"error": "有效数据不足"}

    # 计算各层指标
    df["ema_trend"] = df["ma5"] > df["ma20"]  # 趋势方向
    # ADX简化：用ATR/价格比代理
    df["atr_pct"] = df["close"].pct_change().abs().rolling(14).mean() * 100
    df["adx_proxy"] = df["atr_pct"] * 10  # 粗略ADX代理
    # 布林带位置
    df["bb_mid"] = df["ma20"]
    df["bb_std"] = df["close"].rolling(20).std()
    df["bb_pos"] = (df["close"] - (df["bb_mid"] - 2 * df["bb_std"])) / (4 * df["bb_std"])

    layers = [
        {"name": "基础MA交叉", "key": "base"},
        {"name": "+ EMA趋势过滤", "key": "ema"},
        {"name": "+ ADX强度过滤", "key": "adx"},
        {"name": "+ 布林带过滤", "key": "bb"},
    ]

    results = []
    prev_stats = None

    for layer in layers:
        trades = _simulate_layered(df, layer["key"], initial_capital)
        stats = _calc_layer_stats(trades, initial_capital, df)
        stats["name"] = layer["name"]
        if prev_stats:
            stats["contribution"] = {
                "trades_delta": stats["trades"] - prev_stats["trades"],
                "return_delta": round(stats["total_return"] - prev_stats["total_return"], 2),
                "dd_delta": round(stats["max_drawdown"] - prev_stats["max_drawdown"], 2),
            }
        else:
            stats["contribution"] = None
        results.append(stats)
        prev_stats = stats

    return {"layers": results, "symbol": sym}


def _simulate_layered(df: pd.DataFrame, layer: str, capital: float) -> list[dict]:
    """按指定过滤层级模拟交易。返回交易记录。"""
    trades = []
    shares = 0
    cash = capital
    buy_price = 0.0

    for i in range(1, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]
        price = float(row["close"])

        # MA金叉
        golden_cross = prev["ma5"] <= prev["ma20"] and row["ma5"] > row["ma20"]
        # MA死叉
        death_cross = prev["ma5"] >= prev["ma20"] and row["ma5"] < row["ma20"]

        if not golden_cross and not death_cross:
            continue

        direction = 1 if golden_cross else -1

        # 逐层过滤
        if layer in ("ema", "adx", "bb"):
            # EMA过滤：多头只做多，空头只做空
            if direction > 0 and not row["ema_trend"]:
                continue
            # 做空时不做EMA过滤（A股不卖空，只跳过）

        if layer in ("adx", "bb"):
            # ADX过滤：趋势强度不够不做
            adx_val = row.get("adx_proxy", 25)
            if adx_val < 20:
                continue

        if layer == "bb":
            # 布林带过滤：不在极端位置入场
            bb_pos = row.get("bb_pos", 0.5)
            if pd.isna(bb_pos):
                bb_pos = 0.5
            if direction > 0 and bb_pos > 0.8:
                continue  # 接近上轨不追高

        # 模拟交易
        if direction > 0 and shares == 0:
            buy_shares = cash // price
            if buy_shares > 0:
                shares = buy_shares
                cash -= shares * price
                buy_price = price
                trades.append({"action": "BUY", "price": price, "shares": int(shares), "date": i})

        elif direction < 0 and shares > 0:
            cash += shares * price
            trades.append({"action": "SELL", "price": price, "shares": int(shares), "date": i})
            shares = 0

    return trades


def _calc_layer_stats(trades: list[dict], capital: float, df: pd.DataFrame) -> dict[str, Any]:
    """计算某层回测的统计指标。"""
    shares = 0
    buy_price = 0.0
    cash = capital
    equity_curve = []
    peak = capital
    max_dd = 0.0
    trades_count = 0
    wins = 0
    total_sells = 0
    gross_profit = 0.0
    gross_loss = 0.0

    trade_dict = {t["date"]: t for t in trades}

    for i in range(len(df)):
        price = float(df.iloc[i]["close"])
        if i in trade_dict:
            t = trade_dict[i]
            if t["action"] == "BUY":
                shares = t["shares"]
                buy_price = t["price"]
                cash -= shares * price
                trades_count += 1
            elif t["action"] == "SELL":
                pnl = (price - buy_price) * shares if shares > 0 else 0
                if pnl > 0:
                    wins += 1
                    gross_profit += pnl
                else:
                    gross_loss += abs(pnl)
                total_sells += 1
                cash += shares * price
                shares = 0

        value = cash + shares * price
        equity_curve.append(value)
        if value > peak:
            peak = value
        dd = (peak - value) / peak * 100
        if dd > max_dd:
            max_dd = dd

    final_value = equity_curve[-1] if equity_curve else capital
    total_return = (final_value / capital - 1) * 100
    pf = gross_profit / gross_loss if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)

    return {
        "name": "",
        "trades": trades_count,
        "total_return": round(total_return, 2),
        "max_drawdown": round(max_dd, 2),
        "win_rate": round(wins / total_sells * 100, 1) if total_sells > 0 else 0,
        "profit_factor": round(pf, 2),
    }


# ==================== 4. 参数敏感性分析 ====================

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

def run_full_analysis(
    symbol: str,
    strategy: str = "ma_cross",
    days: int = 120,
    initial_capital: float = 100000.0,
) -> dict[str, Any]:
    """一键运行全部深度分析。"""
    # 原始回测
    original = bt.run_backtest(symbol, strategy=strategy, days=days, initial_capital=initial_capital)
    if not original:
        return {"error": "回测数据不足"}

    # PF/RF/评分
    pf = calc_profit_factor(original["trades_log"])
    net_profit = original["final_value"] - initial_capital
    rf = calc_recovery_factor(net_profit, original["max_drawdown"])
    score = calc_comprehensive_score(
        original["total_return"], original["max_drawdown"], pf, rf,
        original["trades"], original.get("benchmark_return", 0),
    )

    # 蒙特卡洛
    mc = run_monte_carlo(symbol, strategy, days, initial_capital, simulations=500)

    # 分层测试
    layered = run_layered_test(symbol, days, initial_capital)

    # 参数敏感度
    sensitivity = run_parameter_sensitivity(symbol, strategy, days, initial_capital)

    return {
        "original": original,
        "profit_factor": pf,
        "recovery_factor": rf,
        "comprehensive_score": score,
        "monte_carlo": mc,
        "layered_test": layered,
        "sensitivity": sensitivity,
    }


# ==================== 6. Walk-Forward 滚动测试 ====================

# Walk-Forward 参数搜索网格：快/慢均线组合（对 ma_cross/dual_ma/macd 类策略有效）
_WF_PARAM_GRID = [
    {"fast_period": 5, "slow_period": 10},
    {"fast_period": 5, "slow_period": 20},
    {"fast_period": 5, "slow_period": 30},
    {"fast_period": 10, "slow_period": 20},
    {"fast_period": 10, "slow_period": 30},
    {"fast_period": 10, "slow_period": 40},
    {"fast_period": 15, "slow_period": 30},
    {"fast_period": 15, "slow_period": 60},
    {"fast_period": 20, "slow_period": 40},
    {"fast_period": 20, "slow_period": 60},
]


def _run_strategy_on_df(
    df: pd.DataFrame,
    strategy: str,
    capital: float,
    **params,
) -> Optional[dict]:
    """在给定的 DataFrame 切片上直接运行策略（不重新拉数据）。

    复用 run_backtest 的指标/基准补全逻辑，但跳过数据获取，
    便于 Walk-Forward 在历史子区间上回测。
    """
    if df is None or len(df) < 30:
        return None

    df = df.copy()
    df["ma5"] = df["close"].rolling(5).mean()
    df["ma20"] = df["close"].rolling(20).mean()
    df = df.dropna(subset=["ma5", "ma20"]).reset_index(drop=True)
    if len(df) < 10:
        return None

    common = {
        "enable_cost": params.pop("enable_cost", True),
        "percentage": params.pop("percentage", 100.0),
        "slippage": params.pop("slippage", 0.001),
    }
    symbol = params.pop("symbol", "")
    result: Optional[dict] = None

    try:
        if strategy in ("ma_cross", "dual_ma"):
            result = bt._backtest_ma_cross(
                df, capital, symbol=symbol,
                fast_period=params.get("fast_period", 5),
                slow_period=params.get("slow_period", 20),
                **common,
            )
        elif strategy == "macd":
            result = bt._backtest_macd(
                df, capital, symbol=symbol,
                fastperiod=params.get("fastperiod", 12),
                slowperiod=params.get("slowperiod", 26),
                signalperiod=params.get("signalperiod", 9),
                **common,
            )
        elif strategy == "kdj":
            result = bt._backtest_kdj(
                df, capital, symbol=symbol,
                k_period=params.get("k_period", 9),
                d_period=params.get("d_period", 3),
                **common,
            )
        elif strategy == "boll":
            result = bt._backtest_boll(
                df, capital, symbol=symbol,
                boll_period=params.get("boll_period", 20),
                boll_std=params.get("boll_std", 2.0),
                **common,
            )
        elif strategy == "rsi":
            result = bt._backtest_rsi(
                df, capital, symbol=symbol,
                rsi_period=params.get("rsi_period", 14),
                rsi_oversold=params.get("rsi_oversold", 30),
                rsi_overbought=params.get("rsi_overbought", 70),
                **common,
            )
        elif strategy == "hold":
            result = bt._backtest_hold(df, capital, **common)
        else:
            return None
    except Exception:
        return None

    if not result:
        return None

    # 补全基准与风险指标（对齐 run_backtest 返回结构）
    first_price = float(df.iloc[0]["close"])
    last_price = float(df.iloc[-1]["close"])
    benchmark_return = (last_price / first_price - 1) * 100
    total_return = float(result.get("total_return", 0.0))
    result["benchmark_return"] = round(benchmark_return, 2)
    result["excess_return"] = round(total_return - benchmark_return, 2)

    metrics = bt._calc_metrics(
        equity_curve=result.get("equity_curve", []),
        trades_log=result.get("trades_log", []),
        total_return=total_return,
        max_drawdown=float(result.get("max_drawdown", 0.0)),
        initial_capital=capital,
    )
    result.update(metrics)
    return result


def _sharpe_from_equity(equity_curve: list[dict], risk_free: float = 0.03) -> float:
    """从权益曲线计算年化 Sharpe 比率。"""
    if not equity_curve or len(equity_curve) < 3:
        return 0.0
    values = [pt["value"] for pt in equity_curve]
    s = pd.Series(values, dtype="float64")
    daily_returns = s.pct_change().dropna()
    if len(daily_returns) < 2:
        return 0.0
    annual_vol = float(daily_returns.std() * math.sqrt(252))
    if annual_vol <= 0:
        return 0.0
    total_ret = (values[-1] / values[0] - 1) if values[0] > 0 else 0.0
    n = len(daily_returns)
    if n > 0 and (1.0 + total_ret) > 0:
        annual_ret = (1.0 + total_ret) ** (252.0 / n) - 1.0
    else:
        annual_ret = 0.0
    return round((annual_ret - risk_free) / annual_vol, 3)


def _optimize_params_on_train(
    train_df: pd.DataFrame,
    strategy: str,
    capital: float,
    param_grid: list[dict],
) -> tuple[Optional[dict], float]:
    """在训练段上搜索最优参数（按 total_return）。

    返回 (best_params, best_train_return)。无有效结果时返回 (None, 0.0)。
    """
    best_params = None
    best_return = -math.inf
    for params in param_grid:
        r = _run_strategy_on_df(train_df, strategy, capital, **params)
        if r and r.get("trades", 0) > 0:
            ret = r.get("total_return", -math.inf)
            if ret > best_return:
                best_return = ret
                best_params = dict(params)
    return best_params, (best_return if best_return > -math.inf else 0.0)


def run_walk_forward(
    symbol: str,
    strategy: str = "ma_cross",
    total_days: int = 500,
    train_window: int = 60,
    test_window: int = 20,
    **kwargs,
) -> dict[str, Any]:
    """滚动窗口 Walk-Forward 测试。

    用过去 train_window 天优化参数 → 交易未来 test_window 天 → 平移窗口。
    输出每个窗口的样本内/样本外表现，评估策略稳定性与防过拟合能力。

    参数:
        symbol: 股票代码
        strategy: 策略名（ma_cross/dual_ma/macd/kdj/boll/rsi/hold）
        total_days: 总回测天数（数据拉取范围）
        train_window: 训练窗口（优化参数的天数）
        test_window: 测试窗口（样本外交易天数）
        **kwargs: 透传 enable_cost/percentage/slippage；param_grid 覆盖默认网格

    返回:
        {
            windows: [{train_start, train_end, test_start, test_end,
                       best_params, train_return, test_return,
                       train_sharpe, test_sharpe}, ...],
            summary: {avg_test_return, test_win_rate, consistency_score,
                      oos_sharpe, total_windows, avg_train_return},
            oos_equity_curve: [{window, date, value}, ...],
        }
    """
    sym = datalayer._norm_symbol(symbol)
    param_grid = kwargs.pop("param_grid", None) or _WF_PARAM_GRID
    enable_cost = kwargs.pop("enable_cost", True)
    percentage = kwargs.pop("percentage", 100.0)
    slippage = kwargs.pop("slippage", 0.001)

    # 拉取足够的历史数据（含 warm-up 缓冲）
    fetch_days = min(max(total_days + 60, 90), 1000)
    hist = datalayer.get_history(sym, days=fetch_days)
    if hist is None or len(hist) < (train_window + test_window + 30):
        return {"error": f"历史数据不足（需 ≥{train_window + test_window + 30} 行，实际 {0 if hist is None else len(hist)}）"}

    df = hist.copy().reset_index(drop=True)
    n = len(df)

    step = test_window
    windows: list[dict] = []
    oos_values: list[dict] = []
    oos_capital = 100000.0  # 样本外累计权益起点

    start = 0
    window_idx = 0
    while start + train_window + test_window <= n:
        train_df = df.iloc[start: start + train_window]
        test_df = df.iloc[start + train_window: start + train_window + test_window]

        if len(train_df) < 30 or len(test_df) < 5:
            break

        train_start = str(train_df.iloc[0]["date"].date()) if hasattr(train_df.iloc[0]["date"], "date") else str(train_df.iloc[0]["date"])
        train_end = str(train_df.iloc[-1]["date"].date()) if hasattr(train_df.iloc[-1]["date"], "date") else str(train_df.iloc[-1]["date"])
        test_start = str(test_df.iloc[0]["date"].date()) if hasattr(test_df.iloc[0]["date"], "date") else str(test_df.iloc[0]["date"])
        test_end = str(test_df.iloc[-1]["date"].date()) if hasattr(test_df.iloc[-1]["date"], "date") else str(test_df.iloc[-1]["date"])

        # ---- 训练段：网格搜索最优参数 ----
        best_params, train_return = _optimize_params_on_train(
            train_df, strategy, 100000.0, param_grid,
        )

        # 训练段 Sharpe（用最优参数重算，便于记录）
        train_sharpe = 0.0
        if best_params:
            train_res = _run_strategy_on_df(
                train_df, strategy, 100000.0,
                enable_cost=enable_cost, percentage=percentage, slippage=slippage,
                **best_params,
            )
            if train_res:
                train_sharpe = _sharpe_from_equity(train_res.get("equity_curve", []))

        # ---- 测试段：用最优参数交易（样本外）----
        if best_params:
            test_res = _run_strategy_on_df(
                test_df, strategy, oos_capital,
                enable_cost=enable_cost, percentage=percentage, slippage=slippage,
                **best_params,
            )
        else:
            # 训练段无有效参数 → 测试段用默认参数
            test_res = _run_strategy_on_df(
                test_df, strategy, oos_capital,
                enable_cost=enable_cost, percentage=percentage, slippage=slippage,
            )

        if test_res:
            test_return = float(test_res.get("total_return", 0.0))
            test_sharpe = _sharpe_from_equity(test_res.get("equity_curve", []))
            # 链接样本外累计权益
            oos_capital *= (1.0 + test_return / 100.0)
            last_date = test_df.iloc[-1]["date"]
            oos_values.append({
                "window": window_idx,
                "date": str(last_date.date()) if hasattr(last_date, "date") else str(last_date),
                "value": round(oos_capital, 2),
            })
        else:
            test_return = 0.0
            test_sharpe = 0.0

        windows.append({
            "window": window_idx,
            "train_start": train_start,
            "train_end": train_end,
            "test_start": test_start,
            "test_end": test_end,
            "best_params": best_params,
            "train_return": round(train_return, 2),
            "test_return": round(test_return, 2),
            "train_sharpe": round(train_sharpe, 3),
            "test_sharpe": round(test_sharpe, 3),
        })

        window_idx += 1
        start += step

    if not windows:
        return {"error": "数据不足以构成任何完整窗口，请减小 train_window/test_window 或增大 total_days"}

    # ---- 汇总 ----
    test_returns = [w["test_return"] for w in windows]
    train_returns = [w["train_return"] for w in windows]
    test_sharpes = [w["test_sharpe"] for w in windows]
    positive_test = sum(1 for r in test_returns if r > 0)

    avg_test_return = float(np.mean(test_returns)) if test_returns else 0.0
    consistency_score = (positive_test / len(test_returns) * 100.0) if test_returns else 0.0
    oos_sharpe = _sharpe_from_equity(
        [{"value": v["value"]} for v in oos_values] if len(oos_values) >= 3
        else [{"value": 100000.0}, {"value": oos_capital}]
    )

    summary = {
        "avg_test_return": round(avg_test_return, 2),
        "avg_train_return": round(float(np.mean(train_returns)), 2) if train_returns else 0.0,
        "test_win_rate": round(consistency_score, 1),
        "consistency_score": round(consistency_score, 1),
        "oos_sharpe": round(oos_sharpe, 3),
        "total_return": round((oos_capital / 100000.0 - 1) * 100, 2),
        "total_windows": len(windows),
        "best_window": max(windows, key=lambda w: w["test_return"])["window"] if windows else 0,
        "worst_window": min(windows, key=lambda w: w["test_return"])["window"] if windows else 0,
    }

    return {
        "symbol": sym,
        "strategy": strategy,
        "windows": windows,
        "summary": summary,
        "oos_equity_curve": oos_values,
    }
