"""回测深度分析 - full_analysis模块"""
from __future__ import annotations
from typing import Any
import pandas as pd
from .scoring import calc_profit_factor, calc_recovery_factor, calc_comprehensive_score
from .. import backtest as bt
from ..data import fetcher as datalayer
from .monte_carlo import run_monte_carlo
from .layered import run_layered_test
from .sensitivity import run_parameter_sensitivity


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


