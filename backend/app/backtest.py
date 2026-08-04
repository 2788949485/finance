"""策略回测系统：在历史K线上模拟交易策略，验证收益率。

支持策略：
  ma_cross   -- MA5/MA20金叉买入，死叉卖出
  grid       -- 网格交易（按百分比间距挂单）
  hold       -- 买入持有（基准对照）
"""
from __future__ import annotations

from typing import Any, Optional

from .data import fetcher as datalayer


def run_backtest(
    symbol: str,
    strategy: str = "ma_cross",
    days: int = 120,
    initial_capital: float = 100000.0,
    **kwargs: Any,
) -> Optional[dict[str, Any]]:
    """运行策略回测。

    返回 {
        strategy: str,
        symbol: str,
        period: str,            # 回测期间
        initial_capital: float,
        final_value: float,
        total_return: float,    # 总收益率%
        benchmark_return: float,# 同期买入持有收益率%
        excess_return: float,   # 超额收益%
        max_drawdown: float,    # 最大回撤%
        trades: int,            # 交易次数
        win_rate: float,        # 胜率%
        trades_log: list,       # 交易记录
        equity_curve: list,     # 权益曲线
    }
    """
    sym = datalayer._norm_symbol(symbol)
    hist = datalayer.get_history(sym, days=min(max(days, 30), 500))
    if hist is None or len(hist) < 30:
        return None

    df = hist.copy()
    df["ma5"] = df["close"].rolling(5).mean()
    df["ma20"] = df["close"].rolling(20).mean()
    df = df.dropna(subset=["ma5", "ma20"]).reset_index(drop=True)
    if len(df) < 10:
        return None

    # 执行策略
    if strategy == "ma_cross":
        result = _backtest_ma_cross(df, initial_capital)
    elif strategy == "grid":
        grid_pct = kwargs.get("grid_pct", 0.05)  # 5%网格间距
        result = _backtest_grid(df, initial_capital, grid_pct)
    elif strategy == "hold":
        result = _backtest_hold(df, initial_capital)
    else:
        return None

    # 基准：买入持有
    first_price = float(df.iloc[0]["close"])
    last_price = float(df.iloc[-1]["close"])
    benchmark_return = (last_price / first_price - 1) * 100

    result.update({
        "strategy": strategy,
        "symbol": sym,
        "period": f"{df.iloc[0]['date'].strftime('%Y-%m-%d')} ~ {df.iloc[-1]['date'].strftime('%Y-%m-%d')}",
        "initial_capital": initial_capital,
        "benchmark_return": round(benchmark_return, 2),
        "excess_return": round(result["total_return"] - benchmark_return, 2),
    })
    return result


def _backtest_ma_cross(df, capital: float) -> dict[str, Any]:
    """MA5/MA20均线交叉策略。"""
    shares = 0.0
    cash = capital
    trades_log: list[dict] = []
    equity_curve: list[dict] = []
    wins = 0
    total_sells = 0
    buy_price = 0.0
    peak_value = capital
    max_dd = 0.0

    for i in range(1, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]
        price = float(row["close"])
        date = row["date"].strftime("%Y-%m-%d")

        # 金叉买入（昨天MA5<=MA20，今天MA5>MA20）
        if prev["ma5"] <= prev["ma20"] and row["ma5"] > row["ma20"] and shares == 0:
            buy_shares = cash // price
            if buy_shares > 0:
                shares = buy_shares
                cash -= shares * price
                buy_price = price
                trades_log.append({"date": date, "action": "BUY", "price": price, "shares": int(shares)})

        # 死叉卖出
        elif prev["ma5"] >= prev["ma20"] and row["ma5"] < row["ma20"] and shares > 0:
            total_sells += 1
            if price > buy_price:
                wins += 1
            cash += shares * price
            trades_log.append({"date": date, "action": "SELL", "price": price, "shares": int(shares)})
            shares = 0

        # 记录权益
        value = cash + shares * price
        equity_curve.append({"date": date, "value": round(value, 2)})
        if value > peak_value:
            peak_value = value
        dd = (peak_value - value) / peak_value * 100
        if dd > max_dd:
            max_dd = dd

    # 如果还持仓，用最后价格结算
    final_price = float(df.iloc[-1]["close"])
    final_value = cash + shares * final_price

    return {
        "final_value": round(final_value, 2),
        "total_return": round((final_value / capital - 1) * 100, 2),
        "max_drawdown": round(max_dd, 2),
        "trades": len(trades_log),
        "win_rate": round(wins / total_sells * 100, 1) if total_sells > 0 else 0,
        "trades_log": trades_log[-20:],  # 最近20笔
        "equity_curve": equity_curve,
    }


def _backtest_grid(df, capital: float, grid_pct: float) -> dict[str, Any]:
    """简易网格策略：价格每跌grid_pct买入一份，每涨grid_pct卖出一份。"""
    shares = 0.0
    cash = capital
    base_price = float(df.iloc[0]["close"])
    position_value = capital * 0.5  # 首次用50%资金建仓
    shares = position_value // base_price
    cash -= shares * base_price
    last_grid_price = base_price
    trades_log = []
    equity_curve = []
    peak_value = capital
    max_dd = 0.0

    for _, row in df.iterrows():
        price = float(row["close"])
        date = row["date"].strftime("%Y-%m-%d")

        # 跌了grid_pct → 买入
        if price <= last_grid_price * (1 - grid_pct) and cash > price * 100:
            buy_shares = (capital * 0.1) // price  # 每次用10%资金
            if buy_shares > 0:
                shares += buy_shares
                cash -= buy_shares * price
                last_grid_price = price
                trades_log.append({"date": date, "action": "BUY", "price": price, "shares": int(buy_shares)})

        # 涨了grid_pct → 卖出
        elif price >= last_grid_price * (1 + grid_pct) and shares > 10:
            sell_shares = min(shares, capital * 0.1 // price)
            if sell_shares > 0:
                shares -= sell_shares
                cash += sell_shares * price
                last_grid_price = price
                trades_log.append({"date": date, "action": "SELL", "price": price, "shares": int(sell_shares)})

        value = cash + shares * price
        equity_curve.append({"date": date, "value": round(value, 2)})
        if value > peak_value:
            peak_value = value
        dd = (peak_value - value) / peak_value * 100
        if dd > max_dd:
            max_dd = dd

    final_price = float(df.iloc[-1]["close"])
    final_value = cash + shares * final_price

    return {
        "final_value": round(final_value, 2),
        "total_return": round((final_value / capital - 1) * 100, 2),
        "max_drawdown": round(max_dd, 2),
        "trades": len(trades_log),
        "win_rate": 0,
        "trades_log": trades_log[-20:],
        "equity_curve": equity_curve,
    }


def _backtest_hold(df, capital: float) -> dict[str, Any]:
    """买入持有策略（基准）。"""
    first_price = float(df.iloc[0]["close"])
    shares = capital // first_price
    cash = capital - shares * first_price
    equity_curve = []
    peak_value = capital
    max_dd = 0.0

    for _, row in df.iterrows():
        price = float(row["close"])
        date = row["date"].strftime("%Y-%m-%d")
        value = cash + shares * price
        equity_curve.append({"date": date, "value": round(value, 2)})
        if value > peak_value:
            peak_value = value
        dd = (peak_value - value) / peak_value * 100
        if dd > max_dd:
            max_dd = dd

    last_price = float(df.iloc[-1]["close"])
    final_value = cash + shares * last_price

    return {
        "final_value": round(final_value, 2),
        "total_return": round((final_value / capital - 1) * 100, 2),
        "max_drawdown": round(max_dd, 2),
        "trades": 1,
        "win_rate": 100 if final_value > capital else 0,
        "trades_log": [{"date": df.iloc[0]["date"].strftime("%Y-%m-%d"), "action": "BUY", "price": first_price, "shares": int(shares)}],
        "equity_curve": equity_curve,
    }
