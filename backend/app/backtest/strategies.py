"""策略信号生成器与回测函数。

从原 app/backtest.py 拆分而来，函数签名与实现保持不变。

包含：
  - SignalGenerator 各策略子类（MACrossSignal / DualMASignal / MACDSignal / KDJSignal /
    BOLLSignal / RSISignal / GridSignal / HoldSignal）
  - _build_signal_generator 工厂函数
  - 各策略 _backtest_* 回测函数（fallback，完全向后兼容）
  - AI 增强策略（_build_market_context / _ai_decision / _backtest_ai）
"""
from __future__ import annotations

import json
from typing import Any, Optional

import pandas as pd

from .indicators import _calc_boll, _calc_kdj, _calc_macd, _calc_rsi
from .engine import (
    SignalGenerator,
    _buy_price,
    _sell_price,
    _can_buy,
    _can_sell,
    _execute_signals,
    apply_buy_cost,
    apply_sell_cost,
    calc_trade_cost,
)


# ==================== 信号生成器：各策略 ====================


class MACrossSignal(SignalGenerator):
    """快/慢均线交叉：金叉买入，死叉卖出。"""

    name = "ma_cross"

    def __init__(self, fast_period: int = 5, slow_period: int = 20):
        self.fast_period = max(int(fast_period), 2)
        self.slow_period = max(int(slow_period), self.fast_period + 1)

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["ma_fast"] = df["close"].rolling(self.fast_period).mean()
        df["ma_slow"] = df["close"].rolling(self.slow_period).mean()
        df = df.dropna(subset=["ma_fast", "ma_slow"]).reset_index(drop=True)
        return df

    def generate(self, df: pd.DataFrame, i: int, position: bool) -> str:
        if i < 1:
            return "HOLD"
        prev, row = df.iloc[i - 1], df.iloc[i]
        golden = prev["ma_fast"] <= prev["ma_slow"] and row["ma_fast"] > row["ma_slow"]
        death = prev["ma_fast"] >= prev["ma_slow"] and row["ma_fast"] < row["ma_slow"]
        if golden and not position:
            return "BUY"
        if death and position:
            return "SELL"
        return "HOLD"


class DualMASignal(MACrossSignal):
    """双均线策略（ma_cross 别名，独立类名以满足架构约束）。"""

    name = "dual_ma"


class MACDSignal(SignalGenerator):
    """MACD 金叉买入/死叉卖出。"""

    name = "macd"

    def __init__(self, fastperiod: int = 12, slowperiod: int = 26, signalperiod: int = 9):
        self.fastperiod = int(fastperiod)
        self.slowperiod = int(slowperiod)
        self.signalperiod = int(signalperiod)

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        dif, dea, _ = _calc_macd(df["close"], self.fastperiod, self.slowperiod, self.signalperiod)
        df["dif"], df["dea"] = dif, dea
        df = df.dropna(subset=["dif", "dea"]).reset_index(drop=True)
        return df

    def generate(self, df: pd.DataFrame, i: int, position: bool) -> str:
        if i < 1:
            return "HOLD"
        prev, row = df.iloc[i - 1], df.iloc[i]
        golden = prev["dif"] <= prev["dea"] and row["dif"] > row["dea"]
        death = prev["dif"] >= prev["dea"] and row["dif"] < row["dea"]
        if golden and not position:
            return "BUY"
        if death and position:
            return "SELL"
        return "HOLD"


class KDJSignal(SignalGenerator):
    """KDJ 金叉买入/死叉卖出。"""

    name = "kdj"

    def __init__(self, k_period: int = 9, d_period: int = 3):
        self.k_period = int(k_period)
        self.d_period = int(d_period)

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        k, d, _ = _calc_kdj(df, k_period=self.k_period, d_period=self.d_period)
        df["k"], df["d"] = k, d
        df = df.dropna(subset=["k", "d"]).reset_index(drop=True)
        return df

    def generate(self, df: pd.DataFrame, i: int, position: bool) -> str:
        if i < 1:
            return "HOLD"
        prev, row = df.iloc[i - 1], df.iloc[i]
        golden = prev["k"] <= prev["d"] and row["k"] > row["d"]
        death = prev["k"] >= prev["d"] and row["k"] < row["d"]
        if golden and not position:
            return "BUY"
        if death and position:
            return "SELL"
        return "HOLD"


class BOLLSignal(SignalGenerator):
    """布林带：跌破下轨买入，突破上轨卖出。"""

    name = "boll"

    def __init__(self, boll_period: int = 20, boll_std: float = 2.0):
        self.boll_period = int(boll_period)
        self.boll_std = float(boll_std)

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        upper, _, lower = _calc_boll(df["close"], period=self.boll_period, std=self.boll_std)
        df["boll_upper"], df["boll_lower"] = upper, lower
        df = df.dropna(subset=["boll_upper", "boll_lower"]).reset_index(drop=True)
        return df

    def generate(self, df: pd.DataFrame, i: int, position: bool) -> str:
        row = df.iloc[i]
        close = float(row["close"])
        if close <= row["boll_lower"] and not position:
            return "BUY"
        if close >= row["boll_upper"] and position:
            return "SELL"
        return "HOLD"


class RSISignal(SignalGenerator):
    """RSI 超买超卖：RSI<超卖线买入，RSI>超买线卖出。"""

    name = "rsi"

    def __init__(self, rsi_period: int = 14, rsi_oversold: float = 30.0, rsi_overbought: float = 70.0):
        self.rsi_period = int(rsi_period)
        self.rsi_oversold = float(rsi_oversold)
        self.rsi_overbought = float(rsi_overbought)

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["rsi"] = _calc_rsi(df["close"], period=self.rsi_period)
        df = df.dropna(subset=["rsi"]).reset_index(drop=True)
        return df

    def generate(self, df: pd.DataFrame, i: int, position: bool) -> str:
        rsi_val = float(df.iloc[i]["rsi"])
        if rsi_val < self.rsi_oversold and not position:
            return "BUY"
        if rsi_val > self.rsi_overbought and position:
            return "SELL"
        return "HOLD"


class GridSignal(SignalGenerator):
    """网格交易信号生成器。

    网格是多仓位、部分买卖策略，与单仓位执行器不兼容，
    因此 execute() 委托给保留的 _backtest_grid（精确向后兼容）。
    """

    name = "grid"

    def __init__(self, grid_pct: float = 0.05):
        self.grid_pct = float(grid_pct)

    def execute(self, df: pd.DataFrame, capital: float, **opts) -> Optional[dict]:
        return _backtest_grid(
            df, capital, self.grid_pct,
            enable_cost=opts.get("enable_cost", True),
            percentage=opts.get("percentage", 100.0),
            slippage=opts.get("slippage", 0.001),
        )


class HoldSignal(SignalGenerator):
    """买入持有（基准）：首日建仓，末日平仓。"""

    name = "hold"

    def generate(self, df: pd.DataFrame, i: int, position: bool) -> str:
        if i == 1 and not position:
            return "BUY"
        if i == len(df) - 1 and position:
            return "SELL"
        return "HOLD"


def _build_signal_generator(strategy: str, **kwargs) -> Optional[SignalGenerator]:
    """根据策略名构建信号生成器。

    返回 None 表示该策略无信号生成器（如 ai 策略），应走原有 _backtest_* fallback。
    """
    s = strategy.lower()
    if s == "ma_cross":
        return MACrossSignal(
            fast_period=kwargs.get("fast_period", 5),
            slow_period=kwargs.get("slow_period", 20),
        )
    if s == "dual_ma":
        return DualMASignal(
            fast_period=kwargs.get("fast_period", 5),
            slow_period=kwargs.get("slow_period", 20),
        )
    if s == "macd":
        return MACDSignal(
            fastperiod=kwargs.get("fastperiod", 12),
            slowperiod=kwargs.get("slowperiod", 26),
            signalperiod=kwargs.get("signalperiod", 9),
        )
    if s == "kdj":
        return KDJSignal(
            k_period=kwargs.get("k_period", 9),
            d_period=kwargs.get("d_period", 3),
        )
    if s == "boll":
        return BOLLSignal(
            boll_period=kwargs.get("boll_period", 20),
            boll_std=kwargs.get("boll_std", 2.0),
        )
    if s == "rsi":
        return RSISignal(
            rsi_period=kwargs.get("rsi_period", 14),
            rsi_oversold=kwargs.get("rsi_oversold", 30.0),
            rsi_overbought=kwargs.get("rsi_overbought", 70.0),
        )
    if s == "grid":
        return GridSignal(grid_pct=kwargs.get("grid_pct", 0.05))
    if s == "hold":
        return HoldSignal()
    return None  # ai 策略 & 未知策略走 fallback


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
                from ..signal_features import build_signal_features
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
                from ..signal_features import build_signal_features
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
                from ..signal_features import build_signal_features
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
                from ..signal_features import build_signal_features
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
                from ..signal_features import build_signal_features
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
                from ..signal_features import build_signal_features
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
                from ..signal_features import build_signal_features
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
                from ..signal_features import build_signal_features
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
                from ..signal_features import build_signal_features
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
                from ..signal_features import build_signal_features
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
    from ..llm import LLMClient

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
                    from ..signal_features import build_signal_features
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
                    from ..signal_features import build_signal_features
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
