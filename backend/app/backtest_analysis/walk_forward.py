"""回测深度分析 - walk_forward模块"""
from __future__ import annotations
from typing import Any
import pandas as pd
from .full_analysis import _run_strategy_on_df
from .. import backtest as bt
from ..data import fetcher as datalayer


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




def _equity_curve_period_return(
    equity_curve: list[dict],
    start_idx: int,
) -> float:
    """从权益曲线提取从 start_idx 到末尾的区间收益率（百分比）。

    用于把 train+test 合并回测的 equity_curve 切出样本外（test）段收益。
    """
    if not equity_curve or start_idx >= len(equity_curve) - 1:
        return 0.0
    v0 = float(equity_curve[start_idx]["value"])
    v1 = float(equity_curve[-1]["value"])
    if v0 <= 0:
        return 0.0
    return round((v1 / v0 - 1.0) * 100.0, 2)




def _equity_curve_segment(
    equity_curve: list[dict],
    start_idx: int,
) -> list[dict]:
    """返回 equity_curve 从 start_idx 到末尾的切片（归一化起点为 100000）。"""
    if not equity_curve or start_idx >= len(equity_curve):
        return []
    base = float(equity_curve[start_idx]["value"])
    if base <= 0:
        base = 100000.0
    seg = []
    for pt in equity_curve[start_idx:]:
        seg.append({
            "date": pt["date"],
            "value": round(float(pt["value"]) / base * 100000.0, 2),
        })
    return seg




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

    实现要点：
      - 训练段（60天）做参数网格搜索找最优参数
      - 测试段用最优参数回测；为避免指标 warm-up 不足，测试段的指标
        在 train+test 合并窗口上预热，仅取 test 区间的权益曲线计算收益
      - 滚动平移 test_window 天，逐窗口记录样本内/外表现
      - 样本外累计权益曲线由各窗口 test 收益复利链接

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
        # 合并 train+test 用于回测（指标可从 train 段 warm-up 到 test 段）
        full_window_df = df.iloc[start: start + train_window + test_window]
        # test 段的原始行数（dropna 前的 K 线数）
        test_klines = test_window

        if len(train_df) < 30 or len(full_window_df) < train_window + 5:
            break

        train_start = str(train_df.iloc[0]["date"].date()) if hasattr(train_df.iloc[0]["date"], "date") else str(train_df.iloc[0]["date"])
        train_end = str(train_df.iloc[-1]["date"].date()) if hasattr(train_df.iloc[-1]["date"], "date") else str(train_df.iloc[-1]["date"])
        test_df_raw = df.iloc[start + train_window: start + train_window + test_window]
        test_start = str(test_df_raw.iloc[0]["date"].date()) if hasattr(test_df_raw.iloc[0]["date"], "date") else str(test_df_raw.iloc[0]["date"])
        test_end = str(test_df_raw.iloc[-1]["date"].date()) if hasattr(test_df_raw.iloc[-1]["date"], "date") else str(test_df_raw.iloc[-1]["date"])

        # ---- 训练段：网格搜索最优参数 ----
        best_params, train_return = _optimize_params_on_train(
            train_df, strategy, 100000.0, param_grid,
        )

        # 训练段 Sharpe（用最优参数重算）
        train_sharpe = 0.0
        if best_params:
            train_res = _run_strategy_on_df(
                train_df, strategy, 100000.0,
                enable_cost=enable_cost, percentage=percentage, slippage=slippage,
                **best_params,
            )
            if train_res:
                train_sharpe = _sharpe_from_equity(train_res.get("equity_curve", []))

        # ---- 测试段：在 train+test 合并窗口上回测，取 test 区间权益 ----
        # 这样指标从 train 段预热，test 段真正反映样本外表现。
        run_params = best_params or {}
        window_res = _run_strategy_on_df(
            full_window_df, strategy, oos_capital,
            enable_cost=enable_cost, percentage=percentage, slippage=slippage,
            **run_params,
        )

        if window_res and window_res.get("equity_curve"):
            eq = window_res["equity_curve"]
            # test 段起点：eq 末尾 test_klines 行（dropna 后近似对齐）
            # 用倒数 test_klines 作为样本外起点（最多取到 len-1）
            seg_start = max(len(eq) - test_klines, 1)
            test_return = _equity_curve_period_return(eq, seg_start)
            test_seg = _equity_curve_segment(eq, seg_start)
            test_sharpe = _sharpe_from_equity(test_seg) if len(test_seg) >= 3 else 0.0
            # 链接样本外累计权益
            oos_capital *= (1.0 + test_return / 100.0)
        else:
            test_return = 0.0
            test_sharpe = 0.0

        # 记录该窗口末尾的样本外累计权益（每窗口一点，构成简洁 OOS 曲线）
        last_date = test_df_raw.iloc[-1]["date"]
        oos_values.append({
            "window": window_idx,
            "date": str(last_date.date()) if hasattr(last_date, "date") else str(last_date),
            "value": round(oos_capital, 2),
        })

        windows.append({
            "window": window_idx,
            "train_start": train_start,
            "train_end": train_end,
            "test_start": test_start,
            "test_end": test_end,
            "best_params": best_params,
            "train_return": round(train_return, 2),
            "test_return": test_return,
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
    positive_test = sum(1 for r in test_returns if r > 0)

    avg_test_return = float(np.mean(test_returns)) if test_returns else 0.0
    consistency_score = (positive_test / len(test_returns) * 100.0) if test_returns else 0.0
    oos_sharpe = _sharpe_from_equity(oos_values) if len(oos_values) >= 3 else 0.0

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


# ==================== 7. CPCV 组合式清洗交叉验证 ====================

# CPCV/PBO 参数搜索网格（满足约束：fast_period ∈ [3,20] × slow_period ∈ [10,60]，约15组）
_CPCV_PARAM_GRID = [
    {"fast_period": 3, "slow_period": 10},
    {"fast_period": 5, "slow_period": 15},
    {"fast_period": 5, "slow_period": 20},
    {"fast_period": 5, "slow_period": 30},
    {"fast_period": 7, "slow_period": 20},
    {"fast_period": 7, "slow_period": 30},
    {"fast_period": 10, "slow_period": 20},
    {"fast_period": 10, "slow_period": 30},
    {"fast_period": 10, "slow_period": 40},
    {"fast_period": 12, "slow_period": 26},
    {"fast_period": 15, "slow_period": 30},
    {"fast_period": 15, "slow_period": 45},
    {"fast_period": 18, "slow_period": 40},
    {"fast_period": 20, "slow_period": 50},
    {"fast_period": 20, "slow_period": 60},
]


