"""统计/风险收益指标计算：夏普 / 最大回撤 / 胜率 / 盈亏比等。

从原 app/backtest.py 拆分而来，函数签名与实现保持不变。
"""
from __future__ import annotations

import math
from typing import Optional

import pandas as pd


# ==================== 默认风险参数 ====================
RISK_FREE_RATE = 0.03        # 无风险利率（年化）
TRADING_DAYS_PER_YEAR = 252  # 年交易日


def _calc_metrics(
    equity_curve: list[dict],
    trades_log: list[dict],
    total_return: float,
    max_drawdown: float,
    initial_capital: float,
) -> dict[str, float]:
    """根据权益曲线和交易记录统一计算风险/收益指标。

    计算口径：
      日收益率: daily_returns = equity_curve.pct_change()
      年化收益率: (1+total_return/100)^(252/trading_days) - 1
      年化波动率: daily_returns.std() * sqrt(252)
      夏普比率: (annual_return - risk_free) / annual_volatility
      Sortino比率: (annual_return - risk_free) / downside_deviation * sqrt(252)
      Calmar比率: annual_return / max_drawdown
      最大连续亏损: 连续负收益交易笔数（按已完成交易的盈亏）
    """
    risk_free = RISK_FREE_RATE

    # ---- 基于权益曲线的指标 ----
    values = [pt["value"] for pt in equity_curve] if equity_curve else [initial_capital]
    s = pd.Series(values, dtype="float64")
    daily_returns = s.pct_change().dropna()

    # 年化收益率 — 用真实回测区间天数（含warm-up期）
    # 修正：之前用len(values)是策略生效后天数，MA策略dropna丢了前20天导致年化高估
    trading_days = len(daily_returns)
    if trading_days > 0 and (1.0 + total_return / 100.0) > 0:
        annual_return = (1.0 + total_return / 100.0) ** (TRADING_DAYS_PER_YEAR / trading_days) - 1.0
    else:
        annual_return = 0.0

    # 年化波动率（日收益率标准差 * sqrt(252)）
    if len(daily_returns) > 1:
        annual_volatility = float(daily_returns.std() * math.sqrt(TRADING_DAYS_PER_YEAR))
    else:
        annual_volatility = 0.0

    # 无风险利率降频：年化3% -> 日级
    daily_rf = (1.0 + risk_free) ** (1.0 / TRADING_DAYS_PER_YEAR) - 1.0

    # 下行偏差（Sortino标准算法）：sqrt(mean(min(r-target,0)^2))
    # 修正：之前用downside.std()只对负收益取std，系统性高估Sortino
    downside_diff = daily_returns.apply(lambda r: min(r - daily_rf, 0.0))
    downside_deviation = math.sqrt(float((downside_diff ** 2).mean())) if len(daily_returns) > 0 else 0.0

    # 夏普比率（rf从年化降到日级再年化）
    if annual_volatility > 0:
        sharpe_ratio = (annual_return - risk_free) / annual_volatility
    else:
        sharpe_ratio = 0.0

    # EWMA夏普比率（指数加权，近期权重更高，识别策略失效）
    if len(daily_returns) > 30 and annual_volatility > 0:
        ewm_returns = daily_returns.ewm(halflife=20).mean()
        ewm_std = daily_returns.ewm(halflife=20).std()
        if ewm_std.iloc[-1] > 0:
            ewm_sharpe = (ewm_returns.iloc[-1] * TRADING_DAYS_PER_YEAR - risk_free) / (ewm_std.iloc[-1] * math.sqrt(TRADING_DAYS_PER_YEAR))
        else:
            ewm_sharpe = 0.0
    else:
        ewm_sharpe = 0.0

    # Sortino比率（用标准下行偏差年化）
    annual_downside = downside_deviation * math.sqrt(TRADING_DAYS_PER_YEAR)
    if annual_downside > 0:
        sortino_ratio = (annual_return - risk_free) / annual_downside
    else:
        sortino_ratio = 0.0 if annual_return <= risk_free else 10.0

    # Calmar比率（max_drawdown 传入单位是百分比）
    if max_drawdown > 0:
        calmar_ratio = annual_return / (max_drawdown / 100.0)
    else:
        calmar_ratio = 0.0

    # ---- 基于交易记录的最大连续亏损次数 ----
    max_consecutive_losses = _max_consecutive_losses(trades_log)

    # ---- CVaR(95%) 条件风险价值：最差5%日子的平均损失 ----
    if len(daily_returns) > 20:
        sorted_returns = daily_returns.sort_values()
        var_5_pct_idx = max(1, int(len(sorted_returns) * 0.05))
        worst_5_pct = sorted_returns.iloc[:var_5_pct_idx]
        cvar_95 = float(worst_5_pct.mean()) * 100  # 转百分比
    else:
        cvar_95 = 0.0

    # ---- 收益偏度/峰度 ----
    if len(daily_returns) > 10:
        skewness = float(daily_returns.skew())
        kurtosis = float(daily_returns.kurtosis())
    else:
        skewness = 0.0
        kurtosis = 0.0

    # ---- 最大回撤恢复时间 ----
    # 从权益曲线计算：峰值到恢复（回到峰值）的最大天数
    max_dd_duration = _calc_max_dd_duration(values)

    return {
        "annual_return": round(annual_return * 100, 2),          # 百分比展示
        "annual_volatility": round(annual_volatility * 100, 2),  # 百分比展示
        "sharpe_ratio": round(sharpe_ratio, 3),
        "sortino_ratio": round(sortino_ratio, 3),
        "calmar_ratio": round(calmar_ratio, 3),
        "ewm_sharpe": round(ewm_sharpe, 3),
        "max_consecutive_losses": max_consecutive_losses,
        "cvar_95": round(cvar_95, 2),            # CVaR(95%) 百分比
        "skewness": round(skewness, 3),          # 偏度
        "kurtosis": round(kurtosis, 3),          # 峰度
        "max_dd_duration": max_dd_duration,       # 最大回撤恢复天数
    }


def _calc_max_dd_duration(equity_values: list[float]) -> int:
    """计算最大回撤恢复时间（从峰值跌到回新高的最大天数）。"""
    if len(equity_values) < 2:
        return 0
    peak = equity_values[0]
    dd_start = -1
    max_duration = 0
    for i, v in enumerate(equity_values):
        if v >= peak:
            if dd_start >= 0:
                max_duration = max(max_duration, i - dd_start)
            peak = v
            dd_start = -1
        else:
            if dd_start < 0:
                dd_start = i
    # 如果还在回撤中（没恢复）
    if dd_start >= 0:
        max_duration = max(max_duration, len(equity_values) - dd_start)
    return max_duration


def _max_consecutive_losses(trades_log: list[dict]) -> int:
    """统计已完成交易中，按"买入->紧随的卖出"配对的最大连续亏损笔数。"""
    if not trades_log:
        return 0

    # 按 date 排序，找 BUY->SELL 配对
    pairs: list[tuple[float, float]] = []  # (buy_price, sell_price)
    pending_buy: Optional[float] = None
    for t in trades_log:
        action = str(t.get("action", "")).upper()
        price = float(t.get("price", 0.0))
        if action == "BUY":
            pending_buy = price
        elif action == "SELL":
            if pending_buy is not None:
                pairs.append((pending_buy, price))
            pending_buy = None  # 一个BUY最多配一个SELL；多买合并视为一次

    max_streak = 0
    cur_streak = 0
    for buy_p, sell_p in pairs:
        # 扣除极简成本近似：净盈亏 = 卖出价 - 买入价（>0 视为盈利）
        if sell_p - buy_p < 0:
            cur_streak += 1
            if cur_streak > max_streak:
                max_streak = cur_streak
        else:
            cur_streak = 0
    return max_streak
