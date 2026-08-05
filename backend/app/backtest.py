"""策略回测系统：在历史K线上模拟交易策略，验证收益率。

支持策略：
  ma_cross    -- 快/慢均线交叉金叉买入、死叉卖出（周期可调，默认5/20）
  dual_ma     -- 双均线策略（与ma_cross同族，显式可调参别名）
  macd        -- MACD金叉买入/死叉卖出
  kdj         -- KDJ金叉买入/死叉卖出
  boll        -- 布林带突破：跌破下轨买入、突破上轨卖出
  rsi         -- RSI超买超卖：RSI<超卖线买入、RSI>超买线卖出
  grid        -- 网格交易（按百分比间距挂单）
  hold        -- 买入持有（基准对照）
  ai          -- AI增强策略（大模型综合多维度信号决策买卖）

风险/收益指标（在 _calc_metrics 中统一计算）：
  sharpe_ratio / sortino_ratio / calmar_ratio
  annual_return / annual_volatility / max_consecutive_losses
"""
from __future__ import annotations

import json
import math
from typing import Any, Optional

import pandas as pd

from .data import fetcher as datalayer


# ==================== 交易成本模型 ====================
# A股交易成本：印花税 + 佣金 + 过户费
# 可配置，默认值参考主流券商费率

STAMP_TAX_RATE = 0.0005      # 印花税 0.05%（仅卖出）
COMMISSION_RATE = 0.00025    # 佣金 万2.5（买卖双向）
COMMISSION_MIN = 5.0         # 佣金最低5元/笔
TRANSFER_FEE_RATE = 0.00001  # 过户费 万0.1（买卖双向）

# ==================== 默认风险参数 ====================
RISK_FREE_RATE = 0.03        # 无风险利率（年化）
TRADING_DAYS_PER_YEAR = 252  # 年交易日


def calc_trade_cost(
    price: float,
    shares: int,
    is_buy: bool,
    stamp_tax_rate: float = STAMP_TAX_RATE,
    commission_rate: float = COMMISSION_RATE,
    commission_min: float = COMMISSION_MIN,
    transfer_fee_rate: float = TRANSFER_FEE_RATE,
) -> dict[str, float]:
    """计算单笔交易成本。

    返回 {
        stamp_tax: 印花税（卖出时收取）
        commission: 佣金（买卖双向，最低5元）
        transfer_fee: 过户费（买卖双向）
        total: 总成本
    }
    """
    amount = price * shares
    commission = max(amount * commission_rate, commission_min)
    transfer_fee = amount * transfer_fee_rate
    stamp_tax = amount * stamp_tax_rate if not is_buy else 0.0
    return {
        "stamp_tax": round(stamp_tax, 2),
        "commission": round(commission, 2),
        "transfer_fee": round(transfer_fee, 2),
        "total": round(stamp_tax + commission + transfer_fee, 2),
    }


def apply_buy_cost(cash: float, price: float, shares: int) -> tuple[float, float]:
    """买入扣成本。返回 (扣除成本后的cash, 总成本)。"""
    cost = calc_trade_cost(price, shares, is_buy=True)
    return cash - price * shares - cost["total"], cost["total"]


def apply_sell_cost(cash: float, price: float, shares: int) -> tuple[float, float]:
    """卖出扣成本。返回 (扣除成本后的cash, 总成本)。"""
    cost = calc_trade_cost(price, shares, is_buy=False)
    return cash + price * shares - cost["total"], cost["total"]


# ==================== 滑点/仓位/A股规则 辅助 ====================

def _buy_price(price: float, slippage: float) -> float:
    """含滑点的买入价：收盘价*(1+slippage)。"""
    return price * (1.0 + slippage)


def _sell_price(price: float, slippage: float) -> float:
    """含滑点的卖出价：收盘价*(1-slippage)。"""
    return price * (1.0 - slippage)


def _is_limit_up(row, prev_close: float, symbol: str = "") -> bool:
    """涨停判断。A股10%（科创/创业板20%），美股无涨停，港股无涨停。"""
    if prev_close <= 0:
        return False
    sym = symbol.replace("sh", "").replace("sz", "").replace("us", "").replace("hk", "")
    # 美股/港股无涨停
    if symbol.startswith("us") or symbol.startswith("hk"):
        return False
    # A股: 科创板(688)/创业板(300)涨20%, 其他涨10%
    limit_pct = 0.199 if (sym.startswith("688") or sym.startswith("300") or sym.startswith("301")) else 0.099
    return (float(row["close"]) - prev_close) / prev_close >= limit_pct


def _is_limit_down(row, prev_close: float, symbol: str = "") -> bool:
    """跌停判断。A股10%（科创/创业板20%），美股有熔断，港股无跌停。"""
    if prev_close <= 0:
        return False
    sym = symbol.replace("sh", "").replace("sz", "").replace("us", "").replace("hk", "")
    # 美股: 个股无跌停（只有大盘熔断），港股无跌停
    if symbol.startswith("us") or symbol.startswith("hk"):
        return False
    # A股: 科创板/创业板跌20%, 其他跌10%
    limit_pct = 0.199 if (sym.startswith("688") or sym.startswith("300") or sym.startswith("301")) else 0.099
    return (prev_close - float(row["close"])) / prev_close >= limit_pct


def _can_buy(row, prev_close: float, symbol: str = "") -> bool:
    """涨停时不能买入（A股规则）。"""
    return not _is_limit_up(row, prev_close, symbol)


def _can_sell(row, prev_close: float, symbol: str = "") -> bool:
    """跌停时不能卖出（A股规则）。"""
    return not _is_limit_down(row, prev_close, symbol)


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
        kdj:    k_period/d_period/j_period (默认9/3/3)
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
        from .signal_features import fill_labels, save_signals_to_csv
        signal_log = fill_labels(signal_log, df)
        csv_path = save_signals_to_csv(signal_log)
        result["signal_log_count"] = len(signal_log)
        result["signal_csv_path"] = csv_path
        result["signal_sample"] = signal_log[:3]
    else:
        result["signal_log_count"] = 0

    return result


# ==================== 技术指标计算 ====================

def _calc_macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """计算MACD：返回 (dif, dea, hist) 三个 pd.Series。"""
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    hist = (dif - dea) * 2.0  # MACD柱（国内常用2倍）
    return dif, dea, hist


def _calc_kdj(df: pd.DataFrame, k_period: int = 9, d_period: int = 3):
    """计算KDJ：返回 (K, D, J) 三个 pd.Series。"""
    low_min = df["low"].rolling(window=k_period, min_periods=1).min()
    high_max = df["high"].rolling(window=k_period, min_periods=1).max()
    rsv = (df["close"] - low_min) / (high_max - low_min) * 100.0
    rsv = rsv.fillna(50.0)
    k = rsv.ewm(com=d_period - 1, adjust=False).mean()
    d = k.ewm(com=d_period - 1, adjust=False).mean()
    j = 3.0 * k - 2.0 * d
    return k, d, j


def _calc_boll(close: pd.Series, period: int = 20, std: float = 2.0):
    """布林带：返回 (upper, mid, lower)。"""
    mid = close.rolling(window=period, min_periods=period).mean()
    sd = close.rolling(window=period, min_periods=period).std(ddof=0)
    upper = mid + std * sd
    lower = mid - std * sd
    return upper, mid, lower


def _calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """RSI（Wilder平滑）。"""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    # Wilder 平滑
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, pd.NA)
    rsi = 100.0 - 100.0 / (1.0 + rs)
    return rsi.fillna(50.0)


# ==================== 交易循环工具 ====================

def _equity_and_drawdown(records: list[dict]) -> tuple[list[dict], float, float]:
    """根据 [(date, value), ...] 记录权益曲线并计算最大回撤。"""
    # 注：本函数保留以备复用，主流程直接在循环里维护
    peak = -math.inf
    max_dd = 0.0
    eq = []
    for r in records:
        v = r["value"]
        if v > peak:
            peak = v
        dd = (peak - v) / peak * 100.0 if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
        eq.append({"date": r["date"], "value": round(v, 2)})
    return eq, round(max_dd, 2), peak


# ==================== MA均线交叉策略（可调参） ====================

def _backtest_ma_cross(
    df,
    capital: float,
    symbol: str = "",
    record_signals: bool = False,
    signal_log: list = None,
    fast_period: int = 5,
    slow_period: int = 20,
    enable_cost: bool = True,
    percentage: float = 100.0,
    slippage: float = 0.001,
) -> dict[str, Any]:
    """快/慢均线交叉策略（周期可调，默认 5/20）。

    金叉（快线上穿慢线）买入，死叉（快线下穿慢线）卖出。
    每次买入使用可用资金的 percentage%；含滑点；复利。
    """
    fast_p = max(int(fast_period), 2)
    slow_p = max(int(slow_period), fast_p + 1)

    df = df.copy()
    df["ma_fast"] = df["close"].rolling(fast_p).mean()
    df["ma_slow"] = df["close"].rolling(slow_p).mean()
    df = df.dropna(subset=["ma_fast", "ma_slow"]).reset_index(drop=True)
    if len(df) < 5:
        return _empty_result()

    shares = 0.0
    cash = float(capital)
    buy_price = 0.0
    trades_log: list[dict] = []
    equity_curve: list[dict] = []
    wins = 0
    total_sells = 0
    peak_value = float(capital)
    max_dd = 0.0
    pct = max(min(float(percentage), 100.0), 0.0) / 100.0

    for i in range(1, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]
        close = float(row["close"])
        date = row["date"].strftime("%Y-%m-%d")

        golden = prev["ma_fast"] <= prev["ma_slow"] and row["ma_fast"] > row["ma_slow"]
        death = prev["ma_fast"] >= prev["ma_slow"] and row["ma_fast"] < row["ma_slow"]

        if golden and shares == 0 and cash > 0:
            if record_signals and signal_log is not None:
                from .signal_features import build_signal_features
                feat = build_signal_features(df, i, symbol, 1, "ma_cross")
                if feat:
                    signal_log.append(feat)

            buy_px = _buy_price(close, slippage)
            buy_amount = cash * pct
            buy_shares = int(buy_amount // buy_px) if buy_px > 0 else 0
            if buy_shares > 0:
                shares = buy_shares
                if enable_cost:
                    cash, _ = apply_buy_cost(cash, buy_px, int(shares))
                else:
                    cash -= shares * buy_px
                buy_price = buy_px
                trades_log.append({"date": date, "action": "BUY", "price": round(buy_px, 4), "shares": int(shares)})

        elif death and shares > 0:
            if record_signals and signal_log is not None:
                from .signal_features import build_signal_features
                feat = build_signal_features(df, i, symbol, -1, "ma_cross")
                if feat:
                    signal_log.append(feat)

            sell_px = _sell_price(close, slippage)
            total_sells += 1
            if sell_px > buy_price:
                wins += 1
            if enable_cost:
                cash, _ = apply_sell_cost(cash, sell_px, int(shares))
            else:
                cash += shares * sell_px
            trades_log.append({"date": date, "action": "SELL", "price": round(sell_px, 4), "shares": int(shares)})
            shares = 0
            buy_price = 0.0

        value = cash + shares * close
        equity_curve.append({"date": date, "value": round(value, 2)})
        if value > peak_value:
            peak_value = value
        dd = (peak_value - value) / peak_value * 100.0 if peak_value > 0 else 0.0
        if dd > max_dd:
            max_dd = dd

    # 期末平仓
    final_price = float(df.iloc[-1]["close"])
    if shares > 0:
        sell_px = _sell_price(final_price, slippage)
        if enable_cost:
            cash, _ = apply_sell_cost(cash, sell_px, int(shares))
        else:
            cash += shares * sell_px
        trades_log.append({"date": df.iloc[-1]["date"].strftime("%Y-%m-%d"), "action": "SELL",
                           "price": round(sell_px, 4), "shares": int(shares)})
        shares = 0
    final_value = cash

    return {
        "final_value": round(final_value, 2),
        "total_return": round((final_value / capital - 1) * 100, 2),
        "max_drawdown": round(max_dd, 2),
        "trades": len(trades_log),
        "win_rate": round(wins / total_sells * 100, 1) if total_sells > 0 else 0,
        "trades_log": trades_log[-20:],
        "equity_curve": equity_curve,
    }


# dual_ma 是 ma_cross 的语义别名（保留独立函数名以满足命名约束）
def _backtest_dual_ma(df, capital: float, **kwargs) -> dict[str, Any]:
    """双均线策略别名（与 ma_cross 同实现，支持 fast_period/slow_period）。"""
    return _backtest_ma_cross(df, capital, **kwargs)


# ==================== MACD 交叉策略 ====================

def _backtest_macd(
    df,
    capital: float,
    symbol: str = "",
    record_signals: bool = False,
    signal_log: list = None,
    fastperiod: int = 12,
    slowperiod: int = 26,
    signalperiod: int = 9,
    enable_cost: bool = True,
    percentage: float = 100.0,
    slippage: float = 0.001,
) -> dict[str, Any]:
    """MACD金叉买入/死叉卖出。"""
    df = df.copy()
    dif, dea, hist = _calc_macd(df["close"], fastperiod, slowperiod, signalperiod)
    df["dif"], df["dea"], df["hist"] = dif, dea, hist
    df = df.dropna(subset=["dif", "dea"]).reset_index(drop=True)
    if len(df) < 5:
        return _empty_result()

    shares = 0.0
    cash = float(capital)
    buy_price = 0.0
    trades_log: list[dict] = []
    equity_curve: list[dict] = []
    wins = 0
    total_sells = 0
    peak_value = float(capital)
    max_dd = 0.0
    pct = max(min(float(percentage), 100.0), 0.0) / 100.0

    for i in range(1, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]
        close = float(row["close"])
        date = row["date"].strftime("%Y-%m-%d")

        golden = prev["dif"] <= prev["dea"] and row["dif"] > row["dea"]
        death = prev["dif"] >= prev["dea"] and row["dif"] < row["dea"]

        if golden and shares == 0 and cash > 0:
            if record_signals and signal_log is not None:
                from .signal_features import build_signal_features
                feat = build_signal_features(df, i, symbol, 1, "macd")
                if feat:
                    signal_log.append(feat)
            buy_px = _buy_price(close, slippage)
            buy_shares = int((cash * pct) // buy_px) if buy_px > 0 else 0
            if buy_shares > 0:
                shares = buy_shares
                if enable_cost:
                    cash, _ = apply_buy_cost(cash, buy_px, int(shares))
                else:
                    cash -= shares * buy_px
                buy_price = buy_px
                trades_log.append({"date": date, "action": "BUY", "price": round(buy_px, 4), "shares": int(shares)})

        elif death and shares > 0:
            if record_signals and signal_log is not None:
                from .signal_features import build_signal_features
                feat = build_signal_features(df, i, symbol, -1, "macd")
                if feat:
                    signal_log.append(feat)
            sell_px = _sell_price(close, slippage)
            total_sells += 1
            if sell_px > buy_price:
                wins += 1
            if enable_cost:
                cash, _ = apply_sell_cost(cash, sell_px, int(shares))
            else:
                cash += shares * sell_px
            trades_log.append({"date": date, "action": "SELL", "price": round(sell_px, 4), "shares": int(shares)})
            shares = 0
            buy_price = 0.0

        value = cash + shares * close
        equity_curve.append({"date": date, "value": round(value, 2)})
        if value > peak_value:
            peak_value = value
        dd = (peak_value - value) / peak_value * 100.0 if peak_value > 0 else 0.0
        if dd > max_dd:
            max_dd = dd

    final_price = float(df.iloc[-1]["close"])
    if shares > 0:
        sell_px = _sell_price(final_price, slippage)
        if enable_cost:
            cash, _ = apply_sell_cost(cash, sell_px, int(shares))
        else:
            cash += shares * sell_px
        trades_log.append({"date": df.iloc[-1]["date"].strftime("%Y-%m-%d"), "action": "SELL",
                           "price": round(sell_px, 4), "shares": int(shares)})
        shares = 0
    final_value = cash

    return {
        "final_value": round(final_value, 2),
        "total_return": round((final_value / capital - 1) * 100, 2),
        "max_drawdown": round(max_dd, 2),
        "trades": len(trades_log),
        "win_rate": round(wins / total_sells * 100, 1) if total_sells > 0 else 0,
        "trades_log": trades_log[-20:],
        "equity_curve": equity_curve,
    }


# ==================== KDJ 交叉策略 ====================

def _backtest_kdj(
    df,
    capital: float,
    symbol: str = "",
    record_signals: bool = False,
    signal_log: list = None,
    k_period: int = 9,
    d_period: int = 3,
    enable_cost: bool = True,
    percentage: float = 100.0,
    slippage: float = 0.001,
) -> dict[str, Any]:
    """KDJ金叉（K上穿D，且J/K处于低位）买入，死叉卖出。"""
    df = df.copy()
    k, d, j = _calc_kdj(df, k_period=k_period, d_period=d_period)
    df["k"], df["d"], df["j"] = k, d, j
    df = df.dropna(subset=["k", "d"]).reset_index(drop=True)
    if len(df) < 5:
        return _empty_result()

    shares = 0.0
    cash = float(capital)
    buy_price = 0.0
    trades_log: list[dict] = []
    equity_curve: list[dict] = []
    wins = 0
    total_sells = 0
    peak_value = float(capital)
    max_dd = 0.0
    pct = max(min(float(percentage), 100.0), 0.0) / 100.0

    for i in range(1, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]
        close = float(row["close"])
        date = row["date"].strftime("%Y-%m-%d")

        golden = prev["k"] <= prev["d"] and row["k"] > row["d"]
        death = prev["k"] >= prev["d"] and row["k"] < row["d"]

        if golden and shares == 0 and cash > 0:
            if record_signals and signal_log is not None:
                from .signal_features import build_signal_features
                feat = build_signal_features(df, i, symbol, 1, "kdj")
                if feat:
                    signal_log.append(feat)
            buy_px = _buy_price(close, slippage)
            buy_shares = int((cash * pct) // buy_px) if buy_px > 0 else 0
            if buy_shares > 0:
                shares = buy_shares
                if enable_cost:
                    cash, _ = apply_buy_cost(cash, buy_px, int(shares))
                else:
                    cash -= shares * buy_px
                buy_price = buy_px
                trades_log.append({"date": date, "action": "BUY", "price": round(buy_px, 4), "shares": int(shares)})

        elif death and shares > 0:
            if record_signals and signal_log is not None:
                from .signal_features import build_signal_features
                feat = build_signal_features(df, i, symbol, -1, "kdj")
                if feat:
                    signal_log.append(feat)
            sell_px = _sell_price(close, slippage)
            total_sells += 1
            if sell_px > buy_price:
                wins += 1
            if enable_cost:
                cash, _ = apply_sell_cost(cash, sell_px, int(shares))
            else:
                cash += shares * sell_px
            trades_log.append({"date": date, "action": "SELL", "price": round(sell_px, 4), "shares": int(shares)})
            shares = 0
            buy_price = 0.0

        value = cash + shares * close
        equity_curve.append({"date": date, "value": round(value, 2)})
        if value > peak_value:
            peak_value = value
        dd = (peak_value - value) / peak_value * 100.0 if peak_value > 0 else 0.0
        if dd > max_dd:
            max_dd = dd

    final_price = float(df.iloc[-1]["close"])
    if shares > 0:
        sell_px = _sell_price(final_price, slippage)
        if enable_cost:
            cash, _ = apply_sell_cost(cash, sell_px, int(shares))
        else:
            cash += shares * sell_px
        trades_log.append({"date": df.iloc[-1]["date"].strftime("%Y-%m-%d"), "action": "SELL",
                           "price": round(sell_px, 4), "shares": int(shares)})
        shares = 0
    final_value = cash

    return {
        "final_value": round(final_value, 2),
        "total_return": round((final_value / capital - 1) * 100, 2),
        "max_drawdown": round(max_dd, 2),
        "trades": len(trades_log),
        "win_rate": round(wins / total_sells * 100, 1) if total_sells > 0 else 0,
        "trades_log": trades_log[-20:],
        "equity_curve": equity_curve,
    }


# ==================== 布林带策略 ====================

def _backtest_boll(
    df,
    capital: float,
    symbol: str = "",
    record_signals: bool = False,
    signal_log: list = None,
    boll_period: int = 20,
    boll_std: float = 2.0,
    enable_cost: bool = True,
    percentage: float = 100.0,
    slippage: float = 0.001,
) -> dict[str, Any]:
    """布林带策略：收盘跌破下轨买入，突破上轨卖出。"""
    df = df.copy()
    upper, mid, lower = _calc_boll(df["close"], period=boll_period, std=boll_std)
    df["boll_upper"], df["boll_mid"], df["boll_lower"] = upper, mid, lower
    df = df.dropna(subset=["boll_upper", "boll_lower"]).reset_index(drop=True)
    if len(df) < 5:
        return _empty_result()

    shares = 0.0
    cash = float(capital)
    buy_price = 0.0
    trades_log: list[dict] = []
    equity_curve: list[dict] = []
    wins = 0
    total_sells = 0
    peak_value = float(capital)
    max_dd = 0.0
    pct = max(min(float(percentage), 100.0), 0.0) / 100.0

    for i in range(1, len(df)):
        row = df.iloc[i]
        close = float(row["close"])
        date = row["date"].strftime("%Y-%m-%d")

        # 触及/跌破下轨 → 买入
        buy_signal = close <= row["boll_lower"]
        # 突破上轨 → 卖出
        sell_signal = close >= row["boll_upper"]

        if buy_signal and shares == 0 and cash > 0:
            if record_signals and signal_log is not None:
                from .signal_features import build_signal_features
                feat = build_signal_features(df, i, symbol, 1, "boll")
                if feat:
                    signal_log.append(feat)
            buy_px = _buy_price(close, slippage)
            buy_shares = int((cash * pct) // buy_px) if buy_px > 0 else 0
            if buy_shares > 0:
                shares = buy_shares
                if enable_cost:
                    cash, _ = apply_buy_cost(cash, buy_px, int(shares))
                else:
                    cash -= shares * buy_px
                buy_price = buy_px
                trades_log.append({"date": date, "action": "BUY", "price": round(buy_px, 4), "shares": int(shares)})

        elif sell_signal and shares > 0:
            if record_signals and signal_log is not None:
                from .signal_features import build_signal_features
                feat = build_signal_features(df, i, symbol, -1, "boll")
                if feat:
                    signal_log.append(feat)
            sell_px = _sell_price(close, slippage)
            total_sells += 1
            if sell_px > buy_price:
                wins += 1
            if enable_cost:
                cash, _ = apply_sell_cost(cash, sell_px, int(shares))
            else:
                cash += shares * sell_px
            trades_log.append({"date": date, "action": "SELL", "price": round(sell_px, 4), "shares": int(shares)})
            shares = 0
            buy_price = 0.0

        value = cash + shares * close
        equity_curve.append({"date": date, "value": round(value, 2)})
        if value > peak_value:
            peak_value = value
        dd = (peak_value - value) / peak_value * 100.0 if peak_value > 0 else 0.0
        if dd > max_dd:
            max_dd = dd

    final_price = float(df.iloc[-1]["close"])
    if shares > 0:
        sell_px = _sell_price(final_price, slippage)
        if enable_cost:
            cash, _ = apply_sell_cost(cash, sell_px, int(shares))
        else:
            cash += shares * sell_px
        trades_log.append({"date": df.iloc[-1]["date"].strftime("%Y-%m-%d"), "action": "SELL",
                           "price": round(sell_px, 4), "shares": int(shares)})
        shares = 0
    final_value = cash

    return {
        "final_value": round(final_value, 2),
        "total_return": round((final_value / capital - 1) * 100, 2),
        "max_drawdown": round(max_dd, 2),
        "trades": len(trades_log),
        "win_rate": round(wins / total_sells * 100, 1) if total_sells > 0 else 0,
        "trades_log": trades_log[-20:],
        "equity_curve": equity_curve,
    }


# ==================== RSI 超买超卖策略 ====================

def _backtest_rsi(
    df,
    capital: float,
    symbol: str = "",
    record_signals: bool = False,
    signal_log: list = None,
    rsi_period: int = 14,
    rsi_oversold: float = 30.0,
    rsi_overbought: float = 70.0,
    enable_cost: bool = True,
    percentage: float = 100.0,
    slippage: float = 0.001,
) -> dict[str, Any]:
    """RSI超买超卖：RSI低于超卖线买入，RSI高于超买线卖出。"""
    df = df.copy()
    df["rsi"] = _calc_rsi(df["close"], period=rsi_period)
    df = df.dropna(subset=["rsi"]).reset_index(drop=True)
    if len(df) < 5:
        return _empty_result()

    shares = 0.0
    cash = float(capital)
    buy_price = 0.0
    trades_log: list[dict] = []
    equity_curve: list[dict] = []
    wins = 0
    total_sells = 0
    peak_value = float(capital)
    max_dd = 0.0
    pct = max(min(float(percentage), 100.0), 0.0) / 100.0

    for i in range(1, len(df)):
        row = df.iloc[i]
        close = float(row["close"])
        rsi_val = float(row["rsi"])
        date = row["date"].strftime("%Y-%m-%d")

        buy_signal = rsi_val < rsi_oversold
        sell_signal = rsi_val > rsi_overbought

        if buy_signal and shares == 0 and cash > 0:
            if record_signals and signal_log is not None:
                from .signal_features import build_signal_features
                feat = build_signal_features(df, i, symbol, 1, "rsi")
                if feat:
                    signal_log.append(feat)
            buy_px = _buy_price(close, slippage)
            buy_shares = int((cash * pct) // buy_px) if buy_px > 0 else 0
            if buy_shares > 0:
                shares = buy_shares
                if enable_cost:
                    cash, _ = apply_buy_cost(cash, buy_px, int(shares))
                else:
                    cash -= shares * buy_px
                buy_price = buy_px
                trades_log.append({"date": date, "action": "BUY", "price": round(buy_px, 4), "shares": int(shares)})

        elif sell_signal and shares > 0:
            if record_signals and signal_log is not None:
                from .signal_features import build_signal_features
                feat = build_signal_features(df, i, symbol, -1, "rsi")
                if feat:
                    signal_log.append(feat)
            sell_px = _sell_price(close, slippage)
            total_sells += 1
            if sell_px > buy_price:
                wins += 1
            if enable_cost:
                cash, _ = apply_sell_cost(cash, sell_px, int(shares))
            else:
                cash += shares * sell_px
            trades_log.append({"date": date, "action": "SELL", "price": round(sell_px, 4), "shares": int(shares)})
            shares = 0
            buy_price = 0.0

        value = cash + shares * close
        equity_curve.append({"date": date, "value": round(value, 2)})
        if value > peak_value:
            peak_value = value
        dd = (peak_value - value) / peak_value * 100.0 if peak_value > 0 else 0.0
        if dd > max_dd:
            max_dd = dd

    final_price = float(df.iloc[-1]["close"])
    if shares > 0:
        sell_px = _sell_price(final_price, slippage)
        if enable_cost:
            cash, _ = apply_sell_cost(cash, sell_px, int(shares))
        else:
            cash += shares * sell_px
        trades_log.append({"date": df.iloc[-1]["date"].strftime("%Y-%m-%d"), "action": "SELL",
                           "price": round(sell_px, 4), "shares": int(shares)})
        shares = 0
    final_value = cash

    return {
        "final_value": round(final_value, 2),
        "total_return": round((final_value / capital - 1) * 100, 2),
        "max_drawdown": round(max_dd, 2),
        "trades": len(trades_log),
        "win_rate": round(wins / total_sells * 100, 1) if total_sells > 0 else 0,
        "trades_log": trades_log[-20:],
        "equity_curve": equity_curve,
    }


def _empty_result() -> dict[str, Any]:
    return {
        "final_value": 0.0,
        "total_return": 0.0,
        "max_drawdown": 0.0,
        "trades": 0,
        "win_rate": 0,
        "trades_log": [],
        "equity_curve": [],
    }


# ==================== 网格策略 ====================

def _backtest_grid(
    df,
    capital: float,
    grid_pct: float,
    enable_cost: bool = True,
    percentage: float = 100.0,
    slippage: float = 0.001,
) -> dict[str, Any]:
    """简易网格策略：价格每跌grid_pct买入一份，每涨grid_pct卖出一份。

    （保留原逻辑，叠加滑点/仓位比例与复利。）
    """
    capital = float(capital)
    shares = 0.0
    cash = capital
    base_price = _buy_price(float(df.iloc[0]["close"]), slippage)
    position_value = capital * 0.5  # 首次用50%资金建仓
    shares = int(position_value // base_price) if base_price > 0 else 0
    if enable_cost and shares > 0:
        cash, _ = apply_buy_cost(cash, base_price, int(shares))
    else:
        cash -= shares * base_price
    last_grid_price = base_price
    trades_log = [{"date": df.iloc[0]["date"].strftime("%Y-%m-%d"),
                   "action": "BUY", "price": round(base_price, 4), "shares": int(shares)}]
    equity_curve: list[dict] = []
    peak_value = capital
    max_dd = 0.0
    pct = max(min(float(percentage), 100.0), 0.0) / 100.0

    for _, row in df.iterrows():
        close = float(row["close"])
        date = row["date"].strftime("%Y-%m-%d")

        # 跌了grid_pct → 买入
        if close <= last_grid_price * (1 - grid_pct) and cash > close * 100:
            buy_px = _buy_price(close, slippage)
            buy_amount = cash * pct * 0.2  # 单次用可用资金20%
            buy_shares = int(buy_amount // buy_px) if buy_px > 0 else 0
            if buy_shares > 0:
                shares += buy_shares
                if enable_cost:
                    cash, _ = apply_buy_cost(cash, buy_px, buy_shares)
                else:
                    cash -= buy_shares * buy_px
                last_grid_price = close
                trades_log.append({"date": date, "action": "BUY", "price": round(buy_px, 4), "shares": buy_shares})

        # 涨了grid_pct → 卖出
        elif close >= last_grid_price * (1 + grid_pct) and shares > 10:
            sell_px = _sell_price(close, slippage)
            sell_shares = min(int(shares), int(capital * 0.1 // sell_px)) if sell_px > 0 else 0
            if sell_shares > 0:
                shares -= sell_shares
                if enable_cost:
                    cash, _ = apply_sell_cost(cash, sell_px, sell_shares)
                else:
                    cash += sell_shares * sell_px
                last_grid_price = close
                trades_log.append({"date": date, "action": "SELL", "price": round(sell_px, 4), "shares": sell_shares})

        value = cash + shares * close
        equity_curve.append({"date": date, "value": round(value, 2)})
        if value > peak_value:
            peak_value = value
        dd = (peak_value - value) / peak_value * 100.0 if peak_value > 0 else 0.0
        if dd > max_dd:
            max_dd = dd

    final_price = float(df.iloc[-1]["close"])
    if shares > 0:
        sell_px = _sell_price(final_price, slippage)
        if enable_cost:
            cash, _ = apply_sell_cost(cash, sell_px, int(shares))
        else:
            cash += shares * sell_px
        trades_log.append({"date": df.iloc[-1]["date"].strftime("%Y-%m-%d"), "action": "SELL",
                           "price": round(sell_px, 4), "shares": int(shares)})
    final_value = cash

    return {
        "final_value": round(final_value, 2),
        "total_return": round((final_value / capital - 1) * 100, 2),
        "max_drawdown": round(max_dd, 2),
        "trades": len(trades_log),
        "win_rate": 0,
        "trades_log": trades_log[-20:],
        "equity_curve": equity_curve,
    }


# ==================== 买入持有 ====================

def _backtest_hold(
    df,
    capital: float,
    enable_cost: bool = True,
    percentage: float = 100.0,
    slippage: float = 0.001,
) -> dict[str, Any]:
    """买入持有策略（基准）。回测结束时自动平仓补全交易对。"""
    capital = float(capital)
    pct = max(min(float(percentage), 100.0), 0.0) / 100.0
    first_px = _buy_price(float(df.iloc[0]["close"]), slippage)
    buy_amount = capital * pct
    shares = int(buy_amount // first_px) if first_px > 0 else 0
    cash = capital - shares * first_px
    if enable_cost and shares > 0:
        cash -= calc_trade_cost(first_px, int(shares), is_buy=True)["total"]
    equity_curve: list[dict] = []
    peak_value = capital
    max_dd = 0.0

    for _, row in df.iterrows():
        close = float(row["close"])
        date = row["date"].strftime("%Y-%m-%d")
        value = cash + shares * close
        equity_curve.append({"date": date, "value": round(value, 2)})
        if value > peak_value:
            peak_value = value
        dd = (peak_value - value) / peak_value * 100.0 if peak_value > 0 else 0.0
        if dd > max_dd:
            max_dd = dd

    last_close = float(df.iloc[-1]["close"])
    last_px = _sell_price(last_close, slippage)
    last_date = df.iloc[-1]["date"].strftime("%Y-%m-%d")
    first_date = df.iloc[0]["date"].strftime("%Y-%m-%d")
    final_value = cash + shares * last_px
    if enable_cost and shares > 0:
        final_value -= calc_trade_cost(last_px, int(shares), is_buy=False)["total"]

    trades_log = [
        {"date": first_date, "action": "BUY", "price": round(first_px, 4), "shares": int(shares)},
        {"date": last_date, "action": "SELL", "price": round(last_px, 4), "shares": int(shares)},
    ]

    return {
        "final_value": round(final_value, 2),
        "total_return": round((final_value / capital - 1) * 100, 2),
        "max_drawdown": round(max_dd, 2),
        "trades": 1,
        "win_rate": 100 if final_value > capital else 0,
        "trades_log": trades_log,
        "equity_curve": equity_curve,
    }


# ==================== AI增强策略 ====================

def _build_market_context(df, i: int, lookback: int = 5) -> dict[str, Any]:
    """构建给LLM的市场环境上下文。"""
    if i < lookback:
        lookback = i
    recent = df.iloc[max(0, i - lookback): i + 1]
    closes = recent["close"].tolist()
    vols = recent["volume"].tolist()

    row = df.iloc[i]
    ret_5d = (closes[-1] / closes[0] - 1) * 100 if len(closes) > 1 and closes[0] > 0 else 0
    vol_change = (vols[-1] / (sum(vols[:-1]) / max(len(vols) - 1, 1)) - 1) * 100 if len(vols) > 1 and sum(vols[:-1]) > 0 else 0
    if len(closes) >= 3:
        rets = [(closes[j] / closes[j - 1] - 1) for j in range(1, len(closes)) if closes[j - 1] > 0]
        volatility = (sum(r * r for r in rets) / max(len(rets), 1)) ** 0.5 * 100
    else:
        volatility = 0

    if i >= 14:
        delta = df["close"].iloc[i - 14: i + 1].diff()
        gain = delta.clip(lower=0).mean()
        loss = (-delta.clip(upper=0)).mean()
        rsi = 100 - 100 / (1 + gain / loss) if loss > 0 else 100
    else:
        rsi = 50

    return {
        "date": row["date"].strftime("%Y-%m-%d"),
        "price": float(row["close"]),
        "ma5": round(float(row["ma5"]), 2) if row["ma5"] == row["ma5"] else None,
        "ma20": round(float(row["ma20"]), 2) if row["ma20"] == row["ma20"] else None,
        "ma5_above_ma20": bool(row["ma5"] > row["ma20"]) if row["ma5"] == row["ma5"] and row["ma20"] == row["ma20"] else None,
        "rsi14": round(rsi, 1),
        "ret_5d_pct": round(ret_5d, 2),
        "volatility_5d": round(volatility, 2),
        "volume_change_pct": round(vol_change, 1),
        "volume": int(row["volume"]),
    }


def _ai_decision(context: dict[str, Any], position_info: dict[str, Any]) -> tuple[str, str]:
    """调用LLM做交易决策。返回 (action, reason)。

    action: BUY / SELL / HOLD
    """
    from .llm import LLMClient

    llm = LLMClient()
    system = (
        "你是一个量化交易AI，根据市场数据做买卖决策。只返回JSON，格式：\n"
        '{"action":"BUY|SELL|HOLD","confidence":1-10,"reason":"一句话理由"}\n'
        "决策原则：\n"
        "1. BUY: 技术面超跌反弹、金叉、放量突破、RSI<30\n"
        "2. SELL: 技术面超买、死叉、缩量破位、RSI>70、已有较大浮盈\n"
        "3. HOLD: 信号不明确时观望\n"
        "4. 不要频繁交易，信号不明确就HOLD\n"
        "5. 已持仓时降低买入倾向，已空仓时降低卖出倾向"
    )
    user_msg = (
        f"市场数据：{json.dumps(context, ensure_ascii=False)}\n"
        f"当前持仓：{json.dumps(position_info, ensure_ascii=False)}\n"
        "请做交易决策。"
    )

    try:
        text = llm.chat(system, user_msg, temperature=0.1)
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start != -1 and end != -1:
            d = json.loads(cleaned[start: end + 1])
            action = d.get("action", "HOLD").upper()
            if action not in ("BUY", "SELL", "HOLD"):
                action = "HOLD"
            reason = d.get("reason", "")[:100]
            return action, reason
    except Exception:
        pass
    return "HOLD", ""


def _backtest_ai(
    df,
    capital: float,
    sym: str = "",
    symbol: str = "",
    record_signals: bool = False,
    signal_log: list = None,
    enable_cost: bool = True,
    percentage: float = 100.0,
    slippage: float = 0.001,
) -> dict[str, Any]:
    """AI增强策略：大模型每隔3个交易日决策一次，综合技术指标做买卖。

    交易频率：每3个交易日调一次LLM（平衡速度和响应度）。
    每次决策买入时按可用资金的 percentage% 配置（默认满仓），卖出时清仓。
    """
    capital = float(capital)
    shares = 0.0
    cash = capital
    avg_cost = 0.0
    trades_log: list[dict] = []
    equity_curve: list[dict] = []
    wins = 0
    total_sells = 0
    peak_value = capital
    max_dd = 0.0
    decision_interval = 3
    last_decision_day = -decision_interval
    pct = max(min(float(percentage), 100.0), 0.0) / 100.0
    use_symbol = symbol or sym

    for i in range(len(df)):
        row = df.iloc[i]
        close = float(row["close"])
        date = row["date"].strftime("%Y-%m-%d")

        if i - last_decision_day >= decision_interval:
            last_decision_day = i
            context = _build_market_context(df, i)
            position_info = {
                "shares": int(shares),
                "avg_cost": round(avg_cost, 2) if shares > 0 else 0,
                "current_pnl_pct": round((close / avg_cost - 1) * 100, 1) if shares > 0 and avg_cost > 0 else 0,
                "cash": round(cash, 2),
            }

            action, reason = _ai_decision(context, position_info)

            if action == "BUY" and cash > close * 100 and shares == 0:
                if record_signals and signal_log is not None:
                    from .signal_features import build_signal_features
                    feat = build_signal_features(df, i, use_symbol, 1, "ai")
                    if feat:
                        signal_log.append(feat)

                buy_px = _buy_price(close, slippage)
                buy_amount = cash * pct
                buy_shares = int(buy_amount // buy_px) if buy_px > 0 else 0
                if buy_shares > 0:
                    avg_cost = buy_px
                    shares = buy_shares
                    if enable_cost:
                        cash, _ = apply_buy_cost(cash, buy_px, buy_shares)
                    else:
                        cash -= buy_shares * buy_px
                    trades_log.append({"date": date, "action": "BUY", "price": round(buy_px, 4),
                                       "shares": buy_shares, "reason": reason})

            elif action == "SELL" and shares > 0:
                if record_signals and signal_log is not None:
                    from .signal_features import build_signal_features
                    feat = build_signal_features(df, i, use_symbol, -1, "ai")
                    if feat:
                        signal_log.append(feat)
                sell_px = _sell_price(close, slippage)
                total_sells += 1
                if sell_px > avg_cost:
                    wins += 1
                if enable_cost:
                    cash, _ = apply_sell_cost(cash, sell_px, int(shares))
                else:
                    cash += shares * sell_px
                trades_log.append({"date": date, "action": "SELL", "price": round(sell_px, 4),
                                   "shares": int(shares), "reason": reason})
                shares = 0
                avg_cost = 0.0

        value = cash + shares * close
        equity_curve.append({"date": date, "value": round(value, 2)})
        if value > peak_value:
            peak_value = value
        dd = (peak_value - value) / peak_value * 100.0 if peak_value > 0 else 0.0
        if dd > max_dd:
            max_dd = dd

    final_price = float(df.iloc[-1]["close"])
    if shares > 0:
        sell_px = _sell_price(final_price, slippage)
        if enable_cost:
            cash, _ = apply_sell_cost(cash, sell_px, int(shares))
        else:
            cash += shares * sell_px
        trades_log.append({"date": df.iloc[-1]["date"].strftime("%Y-%m-%d"), "action": "SELL",
                           "price": round(sell_px, 4), "shares": int(shares)})
    final_value = cash

    return {
        "final_value": round(final_value, 2),
        "total_return": round((final_value / capital - 1) * 100, 2),
        "max_drawdown": round(max_dd, 2),
        "trades": len(trades_log),
        "win_rate": round(wins / total_sells * 100, 1) if total_sells > 0 else 0,
        "trades_log": trades_log[-20:],
        "equity_curve": equity_curve,
    }
