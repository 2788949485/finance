"""策略回测系统：在历史K线上模拟交易策略，验证收益率。

支持策略：
  ma_cross    -- 快/慢均线交叉金叉买入、死叉卖出（周期可调，默认5/20）
  dual_ma     -- 双均线策略（与ma_cross同族，显式可调参别名）
  macd        -- MACD金叉买入/死叉卖出
  kdj         -- KDJ金叉买入/死叉卖出
  boll        -- 布林带突破：跌破下轨买入、突破上轨卖出
  rsi         -- RSI超买超卖：RSI<超卖线买入、RSI>超卖线卖出
  grid        -- 网格交易（按百分比间距挂单）
  hold        -- 买入持有（基准对照）
  ai          -- AI增强策略（大模型综合多维度信号决策买卖）

风险/收益指标（在 _calc_metrics 中统一计算）：
  sharpe_ratio / sortino_ratio / calmar_ratio
  annual_return / annual_volatility / max_consecutive_losses

本包从原单文件 app/backtest.py 拆分而来，所有公共接口通过 __init__.py
重导出，保持 `from app.backtest import run_backtest` 等现有导入不变。
"""
from __future__ import annotations

from typing import Any, Optional

from ..data import fetcher as datalayer

# ---- 技术指标 ----
from .indicators import (
    _calc_macd,
    _calc_kdj,
    _calc_boll,
    _calc_rsi,
)

# ---- 统计指标 ----
from .metrics import (
    RISK_FREE_RATE,
    TRADING_DAYS_PER_YEAR,
    _calc_metrics,
    _calc_max_dd_duration,
    _max_consecutive_losses,
)

# ---- 执行引擎（成本模型/滑点/涨跌停/执行器）----
from .engine import (
    STAMP_TAX_RATE,
    COMMISSION_RATE,
    COMMISSION_MIN,
    TRANSFER_FEE_RATE,
    calc_trade_cost,
    apply_buy_cost,
    apply_sell_cost,
    _buy_price,
    _sell_price,
    _is_limit_up,
    _is_limit_down,
    _can_buy,
    _can_sell,
    _equity_and_drawdown,
    SignalGenerator,
    _execute_signals,
)

# ---- 策略信号生成器与 _backtest_* 函数 ----
from .strategies import (
    MACrossSignal,
    DualMASignal,
    MACDSignal,
    KDJSignal,
    BOLLSignal,
    RSISignal,
    GridSignal,
    HoldSignal,
    _build_signal_generator,
    _backtest_ma_cross,
    _backtest_dual_ma,
    _backtest_macd,
    _backtest_kdj,
    _backtest_boll,
    _backtest_rsi,
    _empty_result,
    _backtest_grid,
    _backtest_hold,
    _build_market_context,
    _ai_decision,
    _backtest_ai,
)


__all__ = [
    # 主入口
    "run_backtest",
    # 技术指标
    "_calc_macd",
    "_calc_kdj",
    "_calc_boll",
    "_calc_rsi",
    # 统计指标
    "RISK_FREE_RATE",
    "TRADING_DAYS_PER_YEAR",
    "_calc_metrics",
    "_calc_max_dd_duration",
    "_max_consecutive_losses",
    # 成本/滑点/涨跌停
    "STAMP_TAX_RATE",
    "COMMISSION_RATE",
    "COMMISSION_MIN",
    "TRANSFER_FEE_RATE",
    "calc_trade_cost",
    "apply_buy_cost",
    "apply_sell_cost",
    "_buy_price",
    "_sell_price",
    "_is_limit_up",
    "_is_limit_down",
    "_can_buy",
    "_can_sell",
    "_equity_and_drawdown",
    # 执行引擎
    "SignalGenerator",
    "_execute_signals",
    # 策略信号生成器
    "MACrossSignal",
    "DualMASignal",
    "MACDSignal",
    "KDJSignal",
    "BOLLSignal",
    "RSISignal",
    "GridSignal",
    "HoldSignal",
    "_build_signal_generator",
    # _backtest_* 函数
    "_backtest_ma_cross",
    "_backtest_dual_ma",
    "_backtest_macd",
    "_backtest_kdj",
    "_backtest_boll",
    "_backtest_rsi",
    "_empty_result",
    "_backtest_grid",
    "_backtest_hold",
    # AI 策略
    "_build_market_context",
    "_ai_decision",
    "_backtest_ai",
]


# ==================== 主入口 ====================

def run_backtest(
    symbol: str,
    strategy: str = "ma_cross",
    days: int = 120,
    initial_capital: float = 100000.0,
    record_signals: bool = False,
    enable_cost: bool = True,
    *,
    fast_period: int = 5,
    slow_period: int = 20,
    percentage: float = 100.0,
    slippage: float = 0.001,
    **kwargs: Any,
) -> Optional[dict[str, Any]]:
    """运行策略回测。

    通用可选参数（向后兼容，全部有默认值）：
        fast_period: 快均线周期（ma_cross/dual_ma 使用，默认5）
        slow_period: 慢均线周期（ma_cross/dual_ma 使用，默认20）
        percentage:  每次买入使用可用资金的百分比（默认100，即满仓）
        slippage:    滑点率（默认0.001）。买入价=收盘价*(1+slippage)，
                     卖出价=收盘价*(1-slippage)

    策略特定参数（通过 kwargs 透传）：
        grid:   grid_pct (默认0.05)
        macd:   fastperiod/slowperiod/signalperiod (默认12/26/9)
        kdj:    k_period/d_period (默认9/3/3)
        boll:   boll_period/boll_std (默认20/2)
        rsi:    rsi_period/rsi_oversold/rsi_overbought (默认14/30/70)

    返回 dict 包含原有 key（strategy/symbol/period/initial_capital/final_value/
    total_return/benchmark_return/excess_return/max_drawdown/trades/win_rate/
    trades_log/equity_curve）以及新增 key（annual_return/annual_volatility/
    sharpe_ratio/sortino_ratio/calmar_ratio/max_consecutive_losses）。
    """
    sym = datalayer._norm_symbol(symbol)
    hist = datalayer.get_history(sym, days=min(max(days, 30), 500))
    if hist is None or len(hist) < 30:
        return None

    df = hist.copy()
    # 计算 ma5/ma20（兼容老逻辑与AI策略）
    df["ma5"] = df["close"].rolling(5).mean()
    df["ma20"] = df["close"].rolling(20).mean()

    # 不同策略需要不同的最小可用长度；统一以 ma20 是否就绪做下限
    df = df.dropna(subset=["ma5", "ma20"]).reset_index(drop=True)
    if len(df) < 10:
        return None

    # 统一 kwargs
    common = {
        "enable_cost": enable_cost,
        "percentage": percentage,
        "slippage": slippage,
    }

    signal_log: list[dict[str, Any]] = []
    result: Optional[dict[str, Any]] = None

    # ---- 优先走信号-执行解耦架构（AlphaModel 重构）----
    # 所有支持信号生成器的策略走统一执行器 _execute_signals；
    # 信号生成器构建失败（如 ai 策略）则 fallback 到原 _backtest_* 函数。
    gen_kwargs = dict(kwargs)
    gen_kwargs["fast_period"] = fast_period
    gen_kwargs["slow_period"] = slow_period

    generator = _build_signal_generator(strategy, **gen_kwargs)

    if generator is not None:
        try:
            # 预计算指标（dropna）—— 对齐原 _backtest_* 各自的 dropna 行为
            df_prepared = generator.prepare(df)
            if len(df_prepared) < max(generator.min_rows(), 5):
                result = _empty_result()
            else:
                # 自定义执行（如 grid 多仓位策略）
                custom = generator.execute(
                    df_prepared, initial_capital,
                    symbol=symbol,
                    record_signals=record_signals,
                    signal_log=signal_log,
                    enable_cost=enable_cost,
                    percentage=percentage,
                    slippage=slippage,
                )
                if custom is not None:
                    result = custom
                else:
                    # 统一执行器
                    result = _execute_signals(
                        generator, df_prepared, initial_capital,
                        symbol=symbol,
                        record_signals=record_signals,
                        signal_log=signal_log,
                        enable_cost=enable_cost,
                        percentage=percentage,
                        slippage=slippage,
                    )
        except Exception:
            # 任何异常 → 回退到原 _backtest_* 保证向后兼容
            result = None

    # ---- Fallback：原 _backtest_* 函数（完全向后兼容）----
    if result is None:
        if strategy in ("ma_cross", "dual_ma"):
            result = _backtest_ma_cross(
                df, initial_capital,
                symbol=symbol,
                record_signals=record_signals,
                signal_log=signal_log,
                fast_period=fast_period,
                slow_period=slow_period,
                **common,
            )
        elif strategy == "macd":
            result = _backtest_macd(
                df, initial_capital,
                symbol=symbol,
                record_signals=record_signals,
                signal_log=signal_log,
                fastperiod=kwargs.get("fastperiod", 12),
                slowperiod=kwargs.get("slowperiod", 26),
                signalperiod=kwargs.get("signalperiod", 9),
                **common,
            )
        elif strategy == "kdj":
            result = _backtest_kdj(
                df, initial_capital,
                symbol=symbol,
                record_signals=record_signals,
                signal_log=signal_log,
                k_period=kwargs.get("k_period", 9),
                d_period=kwargs.get("d_period", 3),
                **common,
            )
        elif strategy == "boll":
            result = _backtest_boll(
                df, initial_capital,
                symbol=symbol,
                record_signals=record_signals,
                signal_log=signal_log,
                boll_period=kwargs.get("boll_period", 20),
                boll_std=kwargs.get("boll_std", 2.0),
                **common,
            )
        elif strategy == "rsi":
            result = _backtest_rsi(
                df, initial_capital,
                symbol=symbol,
                record_signals=record_signals,
                signal_log=signal_log,
                rsi_period=kwargs.get("rsi_period", 14),
                rsi_oversold=kwargs.get("rsi_oversold", 30),
                rsi_overbought=kwargs.get("rsi_overbought", 70),
                **common,
            )
        elif strategy == "grid":
            grid_pct = kwargs.get("grid_pct", 0.05)
            result = _backtest_grid(df, initial_capital, grid_pct, **common)
        elif strategy == "hold":
            result = _backtest_hold(df, initial_capital, **common)
        elif strategy == "ai":
            result = _backtest_ai(
                df, initial_capital,
                sym=sym,
                symbol=symbol,
                record_signals=record_signals,
                signal_log=signal_log,
                **common,
            )
        else:
            return None

    # ---- 基准：买入持有 ----
    first_price = float(df.iloc[0]["close"])
    last_price = float(df.iloc[-1]["close"])
    benchmark_return = (last_price / first_price - 1) * 100

    total_return = float(result.get("total_return", 0.0))
    max_drawdown = float(result.get("max_drawdown", 0.0))

    result.update({
        "strategy": strategy,
        "symbol": sym,
        "period": f"{df.iloc[0]['date'].strftime('%Y-%m-%d')} ~ {df.iloc[-1]['date'].strftime('%Y-%m-%d')}",
        "initial_capital": initial_capital,
        "benchmark_return": round(benchmark_return, 2),
        "excess_return": round(total_return - benchmark_return, 2),
    })

    # ---- 统一计算风险/收益指标 ----
    metrics = _calc_metrics(
        equity_curve=result.get("equity_curve", []),
        trades_log=result.get("trades_log", []),
        total_return=total_return,
        max_drawdown=max_drawdown,
        initial_capital=initial_capital,
    )
    result.update(metrics)

    # ---- 信号记录 ----
    if record_signals and signal_log:
        from ..signal_features import fill_labels, save_signals_to_csv
        signal_log = fill_labels(signal_log, df)
        csv_path = save_signals_to_csv(signal_log)
        result["signal_log_count"] = len(signal_log)
        result["signal_csv_path"] = csv_path
        result["signal_sample"] = signal_log[:3]
    else:
        result["signal_log_count"] = 0

    return result
