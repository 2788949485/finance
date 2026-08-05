"""策略回测系统：在历史K线上模拟交易策略，验证收益率。

支持策略：
  ma_cross   -- MA5/MA20金叉买入，死叉卖出
  grid       -- 网格交易（按百分比间距挂单）
  hold       -- 买入持有（基准对照）
  ai         -- AI增强策略（大模型综合多维度信号决策买卖）
"""
from __future__ import annotations

import json
from typing import Any, Optional

from .data import fetcher as datalayer


def run_backtest(
    symbol: str,
    strategy: str = "ma_cross",
    days: int = 120,
    initial_capital: float = 100000.0,
    record_signals: bool = False,
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
    signal_log: list[dict[str, Any]] = []
    if strategy == "ma_cross":
        result = _backtest_ma_cross(df, initial_capital, symbol=symbol, record_signals=record_signals, signal_log=signal_log)
    elif strategy == "grid":
        grid_pct = kwargs.get("grid_pct", 0.05)  # 5%网格间距
        result = _backtest_grid(df, initial_capital, grid_pct)
    elif strategy == "hold":
        result = _backtest_hold(df, initial_capital)
    elif strategy == "ai":
        result = _backtest_ai(df, initial_capital, sym, record_signals=record_signals, signal_log=signal_log)
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

    # 如果开启了信号记录，填充标签并保存CSV
    if record_signals and signal_log:
        from .signal_features import fill_labels, save_signals_to_csv
        signal_log = fill_labels(signal_log, df)
        csv_path = save_signals_to_csv(signal_log)
        result["signal_log_count"] = len(signal_log)
        result["signal_csv_path"] = csv_path
        result["signal_sample"] = signal_log[:3]  # 返回前3条样本预览
    else:
        result["signal_log_count"] = 0

    return result


def _backtest_ma_cross(df, capital: float, symbol: str = "", record_signals: bool = False, signal_log: list = None) -> dict[str, Any]:
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

        # 金叉买入
        if prev["ma5"] <= prev["ma20"] and row["ma5"] > row["ma20"] and shares == 0:
            # 记录ML特征快照
            if record_signals and signal_log is not None:
                from .signal_features import build_signal_features
                feat = build_signal_features(df, i, symbol, 1, "ma_cross")
                if feat:
                    signal_log.append(feat)

            buy_shares = cash // price
            if buy_shares > 0:
                shares = buy_shares
                cash -= shares * price
                buy_price = price
                trades_log.append({"date": date, "action": "BUY", "price": price, "shares": int(shares)})

        # 死叉卖出
        elif prev["ma5"] >= prev["ma20"] and row["ma5"] < row["ma20"] and shares > 0:
            # 记录ML特征快照（做空信号也记）
            if record_signals and signal_log is not None:
                from .signal_features import build_signal_features
                feat = build_signal_features(df, i, symbol, -1, "ma_cross")
                if feat:
                    signal_log.append(feat)

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


# ==================== AI增强策略 ====================


def _build_market_context(df, i: int, lookback: int = 5) -> dict[str, Any]:
    """构建给LLM的市场环境上下文。"""
    if i < lookback:
        lookback = i
    recent = df.iloc[max(0, i - lookback) : i + 1]
    closes = recent["close"].tolist()
    vols = recent["volume"].tolist()

    row = df.iloc[i]
    # 计算技术指标
    ret_5d = (closes[-1] / closes[0] - 1) * 100 if len(closes) > 1 and closes[0] > 0 else 0
    vol_change = (vols[-1] / (sum(vols[:-1]) / max(len(vols) - 1, 1)) - 1) * 100 if len(vols) > 1 and sum(vols[:-1]) > 0 else 0
    # 波动率（近5日收益率标准差）
    if len(closes) >= 3:
        rets = [(closes[j] / closes[j - 1] - 1) for j in range(1, len(closes)) if closes[j - 1] > 0]
        volatility = (sum(r * r for r in rets) / max(len(rets), 1)) ** 0.5 * 100
    else:
        volatility = 0

    # RSI（简化14日）
    if i >= 14:
        delta = df["close"].iloc[i - 14 : i + 1].diff()
        gain = delta.clip(lower=0).mean()
        loss = (-delta.clip(upper=0)).mean()
        rsi = 100 - 100 / (1 + gain / loss) if loss > 0 else 100
    else:
        rsi = 50

    return {
        "date": row["date"].strftime("%Y-%m-%d"),
        "price": float(row["close"]),
        "ma5": round(float(row["ma5"]), 2) if row["ma5"] == row["ma5"] else None,  # NaN检查
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
        # 解析JSON
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
        # 找JSON
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start != -1 and end != -1:
            d = json.loads(cleaned[start : end + 1])
            action = d.get("action", "HOLD").upper()
            if action not in ("BUY", "SELL", "HOLD"):
                action = "HOLD"
            reason = d.get("reason", "")[:100]
            return action, reason
    except Exception:
        pass
    return "HOLD", ""


def _backtest_ai(df, capital: float, symbol: str = "", record_signals: bool = False, signal_log: list = None) -> dict[str, Any]:
    """AI增强策略：大模型每隔3个交易日决策一次，综合技术指标做买卖。

    交易频率：每3个交易日调一次LLM（平衡速度和响应度）。
    每次决策买入时用30%资金（分批建仓），卖出时清仓。
    """
    shares = 0.0
    cash = capital
    avg_cost = 0.0
    trades_log: list[dict] = []
    equity_curve: list[dict] = []
    wins = 0
    total_sells = 0
    peak_value = capital
    max_dd = 0.0
    # AI决策频率：每3天一次
    decision_interval = 3
    last_decision_day = -decision_interval  # 确保第一天就决策

    for i in range(len(df)):
        row = df.iloc[i]
        price = float(row["close"])
        date = row["date"].strftime("%Y-%m-%d")

        # 每N个交易日调LLM决策
        if i - last_decision_day >= decision_interval:
            last_decision_day = i
            context = _build_market_context(df, i)
            # 持仓信息
            position_info = {
                "shares": int(shares),
                "avg_cost": round(avg_cost, 2) if shares > 0 else 0,
                "current_pnl_pct": round((price / avg_cost - 1) * 100, 1) if shares > 0 and avg_cost > 0 else 0,
                "cash": round(cash, 2),
            }

            action, reason = _ai_decision(context, position_info)

            if action == "BUY" and cash > price * 100:
                # 记录ML特征快照
                if record_signals and signal_log is not None:
                    from .signal_features import build_signal_features
                    feat = build_signal_features(df, i, symbol, 1, "ai")
                    if feat:
                        signal_log.append(feat)

                # 用30%剩余资金买入（分批建仓）
                buy_amount = cash * 0.3
                buy_shares = int(buy_amount // price)
                if buy_shares > 0:
                    if shares > 0:
                        avg_cost = (shares * avg_cost + buy_shares * price) / (shares + buy_shares)
                    else:
                        avg_cost = price
                    shares += buy_shares
                    cash -= buy_shares * price
                    trades_log.append({"date": date, "action": "BUY", "price": price, "shares": buy_shares, "reason": reason})

            elif action == "SELL" and shares > 0:
                total_sells += 1
                if price > avg_cost:
                    wins += 1
                cash += shares * price
                trades_log.append({"date": date, "action": "SELL", "price": price, "shares": int(shares), "reason": reason})
                shares = 0
                avg_cost = 0

        # 记录权益
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
        "win_rate": round(wins / total_sells * 100, 1) if total_sells > 0 else 0,
        "trades_log": trades_log[-20:],
        "equity_curve": equity_curve,
    }
