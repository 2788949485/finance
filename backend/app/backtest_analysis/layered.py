"""回测深度分析 - layered模块"""
from __future__ import annotations
from typing import Any
import pandas as pd


def run_layered_test(
    symbol: str,
    days: int = 120,
    initial_capital: float = 100000.0,
) -> dict[str, Any]:
    """分层测试：逐个加入过滤器，对比每个模块的贡献度。

    测试顺序：
    1. 基础入场（双K线/MA交叉）
    2. +EMA过滤
    3. +ADX过滤
    4. +布林带过滤
    5. +成交量过滤

    每加入一层，对比交易次数、收益率、最大回撤、PF的变化。
    """
    sym = datalayer._norm_symbol(symbol)
    hist = datalayer.get_history(sym, days=min(max(days, 30), 500))
    if hist is None or len(hist) < 30:
        return {"error": "数据不足"}

    df = hist.copy()
    df["ma5"] = df["close"].rolling(5).mean()
    df["ma20"] = df["close"].rolling(20).mean()
    df = df.dropna(subset=["ma5", "ma20"]).reset_index(drop=True)
    if len(df) < 10:
        return {"error": "有效数据不足"}

    # 计算各层指标
    df["ema_trend"] = df["ma5"] > df["ma20"]  # 趋势方向
    # ADX简化：用ATR/价格比代理
    df["atr_pct"] = df["close"].pct_change().abs().rolling(14).mean() * 100
    df["adx_proxy"] = df["atr_pct"] * 10  # 粗略ADX代理
    # 布林带位置
    df["bb_mid"] = df["ma20"]
    df["bb_std"] = df["close"].rolling(20).std()
    df["bb_pos"] = (df["close"] - (df["bb_mid"] - 2 * df["bb_std"])) / (4 * df["bb_std"])

    layers = [
        {"name": "基础MA交叉", "key": "base"},
        {"name": "+ EMA趋势过滤", "key": "ema"},
        {"name": "+ ADX强度过滤", "key": "adx"},
        {"name": "+ 布林带过滤", "key": "bb"},
    ]

    results = []
    prev_stats = None

    for layer in layers:
        trades = _simulate_layered(df, layer["key"], initial_capital)
        stats = _calc_layer_stats(trades, initial_capital, df)
        stats["name"] = layer["name"]
        if prev_stats:
            stats["contribution"] = {
                "trades_delta": stats["trades"] - prev_stats["trades"],
                "return_delta": round(stats["total_return"] - prev_stats["total_return"], 2),
                "dd_delta": round(stats["max_drawdown"] - prev_stats["max_drawdown"], 2),
            }
        else:
            stats["contribution"] = None
        results.append(stats)
        prev_stats = stats

    return {"layers": results, "symbol": sym}




def _simulate_layered(df: pd.DataFrame, layer: str, capital: float) -> list[dict]:
    """按指定过滤层级模拟交易。返回交易记录。"""
    trades = []
    shares = 0
    cash = capital
    buy_price = 0.0

    for i in range(1, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]
        price = float(row["close"])

        # MA金叉
        golden_cross = prev["ma5"] <= prev["ma20"] and row["ma5"] > row["ma20"]
        # MA死叉
        death_cross = prev["ma5"] >= prev["ma20"] and row["ma5"] < row["ma20"]

        if not golden_cross and not death_cross:
            continue

        direction = 1 if golden_cross else -1

        # 逐层过滤
        if layer in ("ema", "adx", "bb"):
            # EMA过滤：多头只做多，空头只做空
            if direction > 0 and not row["ema_trend"]:
                continue
            # 做空时不做EMA过滤（A股不卖空，只跳过）

        if layer in ("adx", "bb"):
            # ADX过滤：趋势强度不够不做
            adx_val = row.get("adx_proxy", 25)
            if adx_val < 20:
                continue

        if layer == "bb":
            # 布林带过滤：不在极端位置入场
            bb_pos = row.get("bb_pos", 0.5)
            if pd.isna(bb_pos):
                bb_pos = 0.5
            if direction > 0 and bb_pos > 0.8:
                continue  # 接近上轨不追高

        # 模拟交易
        if direction > 0 and shares == 0:
            buy_shares = cash // price
            if buy_shares > 0:
                shares = buy_shares
                cash -= shares * price
                buy_price = price
                trades.append({"action": "BUY", "price": price, "shares": int(shares), "date": i})

        elif direction < 0 and shares > 0:
            cash += shares * price
            trades.append({"action": "SELL", "price": price, "shares": int(shares), "date": i})
            shares = 0

    return trades




def _calc_layer_stats(trades: list[dict], capital: float, df: pd.DataFrame) -> dict[str, Any]:
    """计算某层回测的统计指标。"""
    shares = 0
    buy_price = 0.0
    cash = capital
    equity_curve = []
    peak = capital
    max_dd = 0.0
    trades_count = 0
    wins = 0
    total_sells = 0
    gross_profit = 0.0
    gross_loss = 0.0

    trade_dict = {t["date"]: t for t in trades}

    for i in range(len(df)):
        price = float(df.iloc[i]["close"])
        if i in trade_dict:
            t = trade_dict[i]
            if t["action"] == "BUY":
                shares = t["shares"]
                buy_price = t["price"]
                cash -= shares * price
                trades_count += 1
            elif t["action"] == "SELL":
                pnl = (price - buy_price) * shares if shares > 0 else 0
                if pnl > 0:
                    wins += 1
                    gross_profit += pnl
                else:
                    gross_loss += abs(pnl)
                total_sells += 1
                cash += shares * price
                shares = 0

        value = cash + shares * price
        equity_curve.append(value)
        if value > peak:
            peak = value
        dd = (peak - value) / peak * 100
        if dd > max_dd:
            max_dd = dd

    final_value = equity_curve[-1] if equity_curve else capital
    total_return = (final_value / capital - 1) * 100
    pf = gross_profit / gross_loss if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)

    return {
        "name": "",
        "trades": trades_count,
        "total_return": round(total_return, 2),
        "max_drawdown": round(max_dd, 2),
        "win_rate": round(wins / total_sells * 100, 1) if total_sells > 0 else 0,
        "profit_factor": round(pf, 2),
    }


# ==================== 4. 参数敏感性分析 ====================

