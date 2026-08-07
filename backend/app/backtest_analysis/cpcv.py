"""回测深度分析 - cpcv模块"""
from __future__ import annotations
from typing import Any
import pandas as pd
import numpy as np
from .full_analysis import _run_strategy_on_df
from .. import backtest as bt
from ..data import fetcher as datalayer


def _cpcv_split_groups(n_rows: int, n_groups: int) -> list[tuple[int, int]]:
    """把连续的 n_rows 行均分为 n_groups 组，返回 [(start, end), ...] 半开区间。

    不足整除时余数依次分配到前若干组，保证各组连续且覆盖全部行。
    """
    base = n_rows // n_groups
    rem = n_rows % n_groups
    bounds = []
    start = 0
    for i in range(n_groups):
        size = base + (1 if i < rem else 0)
        bounds.append((start, start + size))
        start += size
    return bounds




def _apply_embargo(
    train_idx: list[int],
    test_idx: list[int],
    n_rows: int,
    embargo_pct: float,
) -> list[int]:
    """从 train 索引中删除与 test 紧邻的 embargo 带，防止信息泄漏。

    embargo 带长度 = round(n_rows * embargo_pct)，从 train 侧剥离与 test
    边界相邻的若干行。
    """
    if embargo_pct <= 0 or not train_idx or not test_idx:
        return train_idx
    embargo_len = max(1, int(round(n_rows * embargo_pct)))
    test_set = set(test_idx)
    # 标记需要 embargo 的行：train 中与任意 test 行距离 < embargo_len 的行
    embargo_set = set()
    for t in test_idx:
        for d in range(1, embargo_len + 1):
            if t - d >= 0:
                embargo_set.add(t - d)
            if t + d < n_rows:
                embargo_set.add(t + d)
    embargo_set &= set(train_idx)  # 只清 train 内的
    return [i for i in train_idx if i not in embargo_set]




def run_cpcv(
    symbol: str,
    strategy: str = "ma_cross",
    days: int = 500,
    n_groups: int = 6,
    n_test_groups: int = 2,
    embargo_pct: float = 0.01,
    **kwargs,
) -> dict[str, Any]:
    """组合式清洗交叉验证 (Combinatorial Purged Cross-Validation)。

    防止金融时序数据信息泄漏的交叉验证：
    1. 把数据按时间均分成 n_groups 组
    2. 遍历 C(n_groups, n_test_groups) 种组合，每次取 n_test_groups 组作为样本外(OOS)
    3. 在样本内(IS)上做参数网格搜索找最优参数，在样本外(OOS)上用该参数测试
    4. 用 embargo 在 train/test 边界加隔离带防止泄漏
    5. 汇总所有组合的 IS/OOS 表现，评估样本外一致性

    参数:
        symbol: 股票代码
        strategy: 策略名（仅对支持 fast_period/slow_period 的 ma_cross/dual_ma 生效）
        days: 数据拉取范围（交易日）
        n_groups: 分组数（建议 6~10）
        n_test_groups: 每次作为样本外的组数（< n_groups）
        embargo_pct: 隔离带占比（占数据总行数的比例，默认 1%）
        **kwargs: 透传 enable_cost/percentage/slippage；param_grid 覆盖默认网格

    返回:
        {
            combinations: [{combo_idx, test_groups, best_params,
                             train_return, test_return, train_sharpe, test_sharpe}, ...],
            summary: {n_combinations, avg_oos_return, oos_win_rate,
                      oos_sharpe_mean, oos_sharpe_std, consistency},
            oos_equity_curve: [{combo, value}, ...],  # 各组合 OOS 收益复利链接
        }
    """
    from itertools import combinations

    sym = datalayer._norm_symbol(symbol)
    param_grid = kwargs.pop("param_grid", None) or _CPCV_PARAM_GRID
    enable_cost = kwargs.pop("enable_cost", True)
    percentage = kwargs.pop("percentage", 100.0)
    slippage = kwargs.pop("slippage", 0.001)
    capital = 100000.0

    if n_groups < 3 or n_test_groups < 1 or n_test_groups >= n_groups:
        return {"error": f"参数无效：需 2 ≤ n_test_groups < n_groups（got n_groups={n_groups}, n_test_groups={n_test_groups}）"}

    fetch_days = min(max(days, 90), 1000)
    hist = datalayer.get_history(sym, days=fetch_days)
    if hist is None or len(hist) < 60:
        return {"error": "历史数据不足（需 ≥60 行）"}

    df = hist.copy().reset_index(drop=True)
    n = len(df)

    # 分组
    bounds = _cpcv_split_groups(n, n_groups)

    combos = list(combinations(range(n_groups), n_test_groups))
    results: list[dict] = []
    oos_values: list[dict] = []
    oos_capital = capital

    for ci, test_groups in enumerate(combos):
        test_idx = []
        train_idx = []
        for gi, (gs, ge) in enumerate(bounds):
            block = list(range(gs, ge))
            if gi in test_groups:
                test_idx.extend(block)
            else:
                train_idx.extend(block)
        if not test_idx or not train_idx:
            continue

        # embargo 清洗 train
        train_idx_purged = _apply_embargo(train_idx, test_idx, n, embargo_pct)
        if len(train_idx_purged) < 30:
            continue

        train_df = df.iloc[sorted(train_idx_purged)].reset_index(drop=True)
        # test 需要一定 warm-up；用 train 末尾的指标预热段 + test 段拼接，
        # 仅取 test 区间权益。这里简化：用 train_purged 的最后一段做 warm-up。
        # 为避免顺序混乱（embargo 后 train 不连续），直接对 test 区间独立回测，
        # 同时附上 test 区间前的原始序列（最多 60 行）做指标预热。
        test_sorted = sorted(test_idx)
        test_start_row = test_sorted[0]
        test_end_row = test_sorted[-1] + 1
        warmup_start = max(0, test_start_row - 60)
        # test 区间若不连续（多组拼接），取覆盖范围 [min, max] 的连续段，
        # 内部空隙用全部行（含部分 train）作为上下文，回测只取 test 权益段
        full_test_df = df.iloc[warmup_start:test_end_row].reset_index(drop=True)

        if len(train_df) < 30 or len(full_test_df) < 30:
            continue

        # ---- 训练：参数网格搜索 ----
        best_params, train_return = _optimize_params_on_train(
            train_df, strategy, capital, param_grid,
        )
        if best_params is None:
            continue

        train_res = _run_strategy_on_df(
            train_df, strategy, capital,
            enable_cost=enable_cost, percentage=percentage, slippage=slippage,
            **best_params,
        )
        train_sharpe = _sharpe_from_equity(train_res.get("equity_curve", [])) if train_res else 0.0

        # ---- 测试：用最优参数在 warmup+test 上回测，取 test 段权益 ----
        test_res = _run_strategy_on_df(
            full_test_df, strategy, oos_capital,
            enable_cost=enable_cost, percentage=percentage, slippage=slippage,
            **best_params,
        )
        test_return = 0.0
        test_sharpe = 0.0
        if test_res and test_res.get("equity_curve"):
            eq = test_res["equity_curve"]
            # test 段对应原始 test 行数（连续区间内属于 test 的行数）
            test_klines_in_range = sum(1 for i in range(warmup_start, test_end_row) if i in set(test_sorted))
            seg_start = max(len(eq) - max(test_klines_in_range, 5), 1)
            test_return = _equity_curve_period_return(eq, seg_start)
            test_seg = _equity_curve_segment(eq, seg_start)
            test_sharpe = _sharpe_from_equity(test_seg) if len(test_seg) >= 3 else 0.0
            oos_capital *= (1.0 + test_return / 100.0)

        oos_values.append({
            "combo": ci,
            "value": round(oos_capital, 2),
        })

        results.append({
            "combo_idx": ci,
            "test_groups": list(test_groups),
            "best_params": best_params,
            "train_return": round(train_return, 2),
            "test_return": round(test_return, 2),
            "train_sharpe": round(train_sharpe, 3),
            "test_sharpe": round(test_sharpe, 3),
        })

    if not results:
        return {"error": "无有效组合结果（数据或参数不足）"}

    oos_returns = [r["test_return"] for r in results]
    oos_sharpes = [r["test_sharpe"] for r in results]
    avg_oos_return = float(np.mean(oos_returns))
    oos_win_rate = float(np.mean([1 if r > 0 else 0 for r in oos_returns]) * 100.0)
    oos_sharpe_mean = float(np.mean(oos_sharpes))
    oos_sharpe_std = float(np.std(oos_sharpes))
    # 一致性：OOS 收益为正且 Sharpe 标准差小 → 高一致性
    consistency = round(
        oos_win_rate * (1.0 - min(oos_sharpe_std / 2.0, 0.5)) if oos_sharpe_std > 0 else oos_win_rate,
        1,
    )

    summary = {
        "n_combinations": len(results),
        "avg_oos_return": round(avg_oos_return, 2),
        "oos_win_rate": round(oos_win_rate, 1),
        "oos_sharpe_mean": round(oos_sharpe_mean, 3),
        "oos_sharpe_std": round(oos_sharpe_std, 3),
        "consistency": round(consistency, 1),
        "total_oos_return": round((oos_capital / capital - 1) * 100, 2),
    }

    return {
        "symbol": sym,
        "strategy": strategy,
        "n_groups": n_groups,
        "n_test_groups": n_test_groups,
        "embargo_pct": embargo_pct,
        "combinations": results,
        "summary": summary,
        "oos_equity_curve": oos_values,
    }


# ==================== 8. PBO 回测过拟合概率 ====================

