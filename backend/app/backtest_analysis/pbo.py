"""回测深度分析 - pbo模块"""
from __future__ import annotations
from typing import Any
import numpy as np


def run_pbo(
    symbol: str,
    strategy: str = "ma_cross",
    days: int = 500,
    n_groups: int = 8,
    n_test_groups: int = 2,
    **kwargs,
) -> dict[str, Any]:
    """回测过拟合概率 (Probability of Backtest Overfitting)。

    方法 (Bailey & López de Prado 2017, "The Probability of Backtest Overfitting"):
    1. 对 N 组参数组合在 IS(样本内) 上跑回测并排名
    2. 用 CPCV 把数据分成 n_groups 组，遍历 C(n_groups, n_test_groups) 种组合
    3. 对每种 train/test 组合：
       - 在 train(IS) 上找出表现最优(排名第一)的策略
       - 检查该策略在 test(OOS) 上的排名是否低于所有策略的中位数
    4. PBO = 在 OOS 上排名低于中位数的组合比例（越低越好，0=无过拟合）

    参数:
        symbol: 股票代码
        strategy: 策略名（仅对支持 fast_period/slow_period 的策略生效）
        days: 数据拉取范围
        n_groups: 分组数（建议 6~10）
        n_test_groups: 每次作为样本外的组数
        **kwargs: 透传 enable_cost/percentage/slippage；param_grid 覆盖默认网格

    返回:
        {
            pbo: 过拟合概率 [0,1]（<0.5 为良好），
            logits: 对数概率分布 [{bin, count}],  # logit 直方图
            is_rank_freq: IS 排名频率 [{rank, count}],  # IS 第一名策略在 IS 上的排名分布
            verdict: 评级（良好 / 警戒 / 严重过拟合），
            n_strategies: 策略参数组数,
            n_combinations: CPCV 组合数,
            combinations: [{combo_idx, test_groups, is_best_idx, oos_rank, below_median}],
        }
    """
    from itertools import combinations

    sym = datalayer._norm_symbol(symbol)
    param_grid = kwargs.pop("param_grid", None) or _CPCV_PARAM_GRID
    enable_cost = kwargs.pop("enable_cost", True)
    percentage = kwargs.pop("percentage", 100.0)
    slippage = kwargs.pop("slippage", 0.001)
    capital = 100000.0

    n_strategies = len(param_grid)
    if n_strategies < 2:
        return {"error": "参数网格至少需要 2 组策略"}
    if n_groups < 3 or n_test_groups < 1 or n_test_groups >= n_groups:
        return {"error": "参数无效：需 2 ≤ n_test_groups < n_groups"}

    fetch_days = min(max(days, 90), 1000)
    hist = datalayer.get_history(sym, days=fetch_days)
    if hist is None or len(hist) < 60:
        return {"error": "历史数据不足（需 ≥60 行）"}

    df = hist.copy().reset_index(drop=True)
    n = len(df)
    bounds = _cpcv_split_groups(n, n_groups)
    combos = list(combinations(range(n_groups), n_test_groups))

    median_rank = n_strategies / 2.0  # 中位数排名
    is_best_rank_freq = [0] * n_strategies  # IS 排名第一的策略是哪个 idx
    below_median_count = 0
    valid_combos = 0
    combo_results: list[dict] = []
    logits: list[float] = []  # 每个 combo 的 logit(用于直方图)

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

        train_df = df.iloc[sorted(train_idx)].reset_index(drop=True)
        test_sorted = sorted(test_idx)
        test_start_row = test_sorted[0]
        test_end_row = test_sorted[-1] + 1
        warmup_start = max(0, test_start_row - 60)
        full_test_df = df.iloc[warmup_start:test_end_row].reset_index(drop=True)

        if len(train_df) < 30 or len(full_test_df) < 30:
            continue

        # ---- 对每组参数在 IS 和 OOS 上分别回测 ----
        is_returns: list[float] = []
        oos_returns: list[float] = []
        for params in param_grid:
            is_res = _run_strategy_on_df(
                train_df, strategy, capital,
                enable_cost=enable_cost, percentage=percentage, slippage=slippage,
                **params,
            )
            is_ret = is_res.get("total_return", -math.inf) if is_res and is_res.get("trades", 0) > 0 else -math.inf
            is_returns.append(is_ret)

            oos_res = _run_strategy_on_df(
                full_test_df, strategy, capital,
                enable_cost=enable_cost, percentage=percentage, slippage=slippage,
                **params,
            )
            if oos_res and oos_res.get("equity_curve"):
                eq = oos_res["equity_curve"]
                test_klines_in_range = sum(1 for i in range(warmup_start, test_end_row) if i in set(test_sorted))
                seg_start = max(len(eq) - max(test_klines_in_range, 5), 1)
                oos_ret = _equity_curve_period_return(eq, seg_start)
            else:
                oos_ret = -math.inf
            oos_returns.append(oos_ret)

        # 过滤掉全 -inf 的无效结果
        valid_mask = [i for i, v in enumerate(is_returns) if v > -math.inf]
        if len(valid_mask) < 2:
            continue
        # 对齐 IS / OOS 的有效策略
        is_arr = [is_returns[i] for i in valid_mask]
        oos_arr = [oos_returns[i] for i in valid_mask]
        idx_map = valid_mask  # 原始 param_grid idx

        # IS 排名（降序，最优=排名1）
        is_order = sorted(range(len(is_arr)), key=lambda k: is_arr[k], reverse=True)
        is_best_local = is_order[0]
        is_best_idx = idx_map[is_best_local]
        is_best_rank_freq[is_best_idx] += 1

        # OOS 排名（降序）
        oos_order = sorted(range(len(oos_arr)), key=lambda k: oos_arr[k], reverse=True)
        oos_rank_of = {local: r + 1 for r, local in enumerate(oos_order)}
        is_best_oos_rank = oos_rank_of[is_best_local]

        # effective median rank（基于有效策略数）
        eff_median = len(oos_arr) / 2.0
        below_median = is_best_oos_rank > eff_median
        if below_median:
            below_median_count += 1

        # logit: ln( (1-pbo_sample) / pbo_sample ), pbo_sample 用 0/1 平滑
        p_sample = 0.25 if below_median else 0.75  # 平滑避免 log(0)
        logit = math.log(p_sample / (1.0 - p_sample))
        logits.append(logit)

        combo_results.append({
            "combo_idx": ci,
            "test_groups": list(test_groups),
            "is_best_idx": is_best_idx,
            "is_best_return": round(is_arr[is_best_local], 2),
            "oos_rank": is_best_oos_rank,
            "oos_return_of_best": round(oos_arr[is_best_local], 2),
            "below_median": bool(below_median),
        })
        valid_combos += 1

    if valid_combos == 0:
        return {"error": "无有效组合结果（数据或参数不足）"}

    pbo = below_median_count / valid_combos

    # 评级
    if pbo < 0.25:
        verdict = "良好：过拟合概率低，样本外表现稳健"
    elif pbo < 0.5:
        verdict = "警戒：存在一定过拟合风险，建议缩减参数空间或增加样本"
    else:
        verdict = "严重过拟合：IS 最优策略在 OOS 多数落后，参数空间高度拟合噪声"

    # logit 直方图（10 bins）
    if logits:
        arr = np.array(logits)
        counts, edges = np.histogram(arr, bins=min(10, max(2, len(arr))))
        logit_hist = [
            {
                "bin_start": round(float(edges[i]), 3),
                "bin_end": round(float(edges[i + 1]), 3),
                "count": int(counts[i]),
            }
            for i in range(len(counts))
        ]
    else:
        logit_hist = []

    # IS 排名频率（哪个策略最常被选为 IS 最优）
    is_rank_freq = [
        {"param_idx": i, "count": is_best_rank_freq[i], "params": param_grid[i]}
        for i in range(n_strategies)
    ]

    return {
        "pbo": round(pbo, 4),
        "verdict": verdict,
        "n_strategies": n_strategies,
        "n_combinations": valid_combos,
        "below_median_count": below_median_count,
        "logits": logit_hist,
        "is_rank_freq": is_rank_freq,
        "combinations": combo_results,
    }

