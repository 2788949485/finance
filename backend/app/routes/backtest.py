"""路由模块: backtest"""
from __future__ import annotations
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..auth import get_profile
from ..config import get_config, save_config, PROVIDER_PRESETS
from ..deps import get_current_user, require_admin

router = APIRouter()

from .. import backtest as bt
from ..backtest_analysis import run_full_analysis, run_walk_forward, run_cpcv, run_pbo
from ..ic_evaluator import evaluate_signal_ic
from ..data import fetcher as datalayer


@router.get("/api/ic-evaluate/{symbol}")
def ic_evaluate_api(symbol: str, forward_days: int = 5) -> dict[str, Any]:
    """IC/Rank IC信号评估：评估技术指标对未来收益的预测力。"""
    from .ic_evaluator import evaluate_strategy_signals
    sym = datalayer._norm_symbol(symbol)
    hist = datalayer.get_history(sym, days=250)
    if hist is None or len(hist) < 60:
        return {"error": "数据不足"}
    df = hist.copy()
    # 构建常见指标
    df["ma_signal"] = (df["close"].rolling(5).mean() - df["close"].rolling(20).mean()) / df["close"]
    df["rsi"] = _calc_rsi(df["close"], 14)
    df["macd_hist"] = _calc_macd_hist(df["close"])
    df["volume_ratio"] = df["volume"] / df["volume"].rolling(20).mean()
    df["boll_position"] = (df["close"] - df["close"].rolling(20).mean()) / (df["close"].rolling(20).std() + 0.001)
    df = df.dropna()
    result = evaluate_strategy_signals(df, forward_days=forward_days)
    return result


def _calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
    rs = gain / (loss + 0.001)
    return 100 - (100 / (1 + rs))


def _calc_macd_hist(close: pd.Series) -> pd.Series:
    ema12 = close.ewm(span=12).mean()
    ema26 = close.ewm(span=26).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9).mean()
    return dif - dea



@router.get("/api/backtest/analysis/{symbol}")
def backtest_analysis_api(
    symbol: str,
    strategy: str = "ma_cross",
    days: int = 120,
    analysis_type: str = "full",
) -> dict[str, Any]:
    """回测深度分析：PF/RF/综合评分 + 蒙特卡洛 + 分层测试 + 参数敏感度。

    analysis_type: full / monte_carlo / layered / sensitivity / score
    """
    from . import backtest_analysis as ba
    sym = datalayer._norm_symbol(symbol)

    if analysis_type == "monte_carlo":
        return ba.run_monte_carlo(sym, strategy=strategy, days=days)
    elif analysis_type == "layered":
        return ba.run_layered_test(sym, days=days)
    elif analysis_type == "sensitivity":
        return ba.run_parameter_sensitivity(sym, strategy=strategy, days=days)
    elif analysis_type == "score":
        r = backtest.run_backtest(sym, strategy=strategy, days=days)
        if not r:
            return {"error": "数据不足"}
        pf = ba.calc_profit_factor(r["trades_log"])
        rf = ba.calc_recovery_factor(r["final_value"] - 100000, r["max_drawdown"])
        score = ba.calc_comprehensive_score(r["total_return"], r["max_drawdown"], pf, rf, r["trades"])
        return {"profit_factor": pf, "recovery_factor": rf, "score": score}
    else:
        return ba.run_full_analysis(sym, strategy=strategy, days=days)



@router.get("/api/backtest/{symbol}")
def backtest_api(
    symbol: str,
    strategy: str = "ma_cross",
    days: int = 120,
    record_signals: int = 0,
    enable_cost: int = 1,
    fast_period: int = 0,
    slow_period: int = 0,
    grid_pct: float = 0,
    boll_period: int = 0,
    rsi_period: int = 0,
    rsi_oversold: int = 0,
    rsi_overbought: int = 0,
    slippage: float = 0,
    position_pct: float = 0,
) -> dict[str, Any]:
    """策略回测：在历史K线上模拟交易策略。
    strategy: ma_cross / dual_ma / macd / kdj / boll / rsi / grid / hold / ai
    可选参数: fast_period, slow_period, grid_pct, boll_period, rsi_period, rsi_oversold, rsi_overbought, slippage, position_pct
    record_signals: 1=记录ML信号特征快照
    enable_cost: 1=含A股交易成本
    """
    sym = datalayer._norm_symbol(symbol)
    # 构建策略参数（只传非零/非默认值）
    kwargs: dict[str, Any] = {}
    if fast_period > 0: kwargs["fast_period"] = fast_period
    if slow_period > 0: kwargs["slow_period"] = slow_period
    if grid_pct > 0: kwargs["grid_pct"] = grid_pct / 100  # 前端传5表示5%
    if boll_period > 0: kwargs["boll_period"] = boll_period
    if rsi_period > 0: kwargs["rsi_period"] = rsi_period
    if rsi_oversold > 0: kwargs["rsi_oversold"] = rsi_oversold
    if rsi_overbought > 0: kwargs["rsi_overbought"] = rsi_overbought
    if slippage > 0: kwargs["slippage"] = slippage / 1000  # 前端传1表示0.1%
    if position_pct > 0: kwargs["percentage"] = position_pct  # 前端传100表示满仓

    result = backtest.run_backtest(sym, strategy=strategy, days=days,
                                   record_signals=bool(record_signals), enable_cost=bool(enable_cost), **kwargs)
    return result or {"error": "回测数据不足（需要至少30个交易日）"}



@router.get("/api/backtest/walk-forward/{symbol}")
def walk_forward_api(
    symbol: str,
    strategy: str = "ma_cross",
    total_days: int = 500,
    train_window: int = 60,
    test_window: int = 20,
) -> dict[str, Any]:
    """Walk-Forward 滚动测试：用过去 train_window 天优化参数 → 交易未来 test_window 天 → 平移。

    评估策略防过拟合能力与样本外稳定性。
    返回每个窗口的 train/test 收益与 Sharpe，以及样本外累计权益曲线。
    """
    from . import backtest_analysis as ba
    sym = datalayer._norm_symbol(symbol)
    try:
        return ba.run_walk_forward(
            sym, strategy=strategy,
            total_days=total_days,
            train_window=train_window,
            test_window=test_window,
        )
    except Exception as e:
        return {"error": f"Walk-Forward 测试失败：{e}"}



@router.get("/api/backtest/cpcv/{symbol}")
def backtest_cpcv_api(
    symbol: str,
    strategy: str = "ma_cross",
    days: int = 500,
    n_groups: int = 6,
    n_test_groups: int = 2,
    embargo_pct: float = 0.01,
) -> dict[str, Any]:
    """组合式清洗交叉验证 (CPCV)：防信息泄漏的组合式 OOS 测试。

    把数据按时间分成 n_groups 组，遍历 C(n_groups, n_test_groups) 种组合，
    每次在样本内做参数搜索、样本外测试，并用 embargo 隔离带防止泄漏。
    返回各组合 IS/OOS 表现、汇总统计与样本外累计权益曲线。
    """
    from . import backtest_analysis as ba
    sym = datalayer._norm_symbol(symbol)
    try:
        return ba.run_cpcv(
            sym, strategy=strategy, days=days,
            n_groups=n_groups, n_test_groups=n_test_groups,
            embargo_pct=embargo_pct,
        )
    except Exception as e:
        return {"error": f"CPCV 测试失败：{e}"}



@router.get("/api/backtest/pbo/{symbol}")
def backtest_pbo_api(
    symbol: str,
    strategy: str = "ma_cross",
    days: int = 500,
    n_groups: int = 8,
    n_test_groups: int = 2,
) -> dict[str, Any]:
    """回测过拟合概率 (PBO)：Bailey & López de Prado 2017 方法。

    在 IS 上找最优策略，检查其在 OOS 的排名是否低于中位数。
    PBO = 低于中位数的组合比例（<0.5 为良好）。
    返回 PBO、logit 直方图、IS 排名频率与评级。
    """
    from . import backtest_analysis as ba
    sym = datalayer._norm_symbol(symbol)
    try:
        return ba.run_pbo(
            sym, strategy=strategy, days=days,
            n_groups=n_groups, n_test_groups=n_test_groups,
        )
    except Exception as e:
        return {"error": f"PBO 测试失败：{e}"}


# ---------- 多LLM对比 ----------

