"""回测执行引擎：交易成本模型 / 涨跌停过滤 / 信号执行器。

从原 app/backtest.py 拆分而来，函数签名与实现保持不变。

包含：
  - 交易成本模型（印花税/佣金/过户费）
  - 滑点与涨跌停(A股规则)辅助函数
  - SignalGenerator 抽象基类（AlphaModel 架构）
  - _execute_signals 统一信号执行器
"""
from __future__ import annotations

from typing import Any, Optional

import pandas as pd


# ==================== 交易成本模型 ====================
# A股交易成本：印花税 + 佣金 + 过户费
# 可配置，默认值参考主流券商费率

STAMP_TAX_RATE = 0.0005      # 印花税 0.05%（仅卖出）
COMMISSION_RATE = 0.00025    # 佣金 万2.5（买卖双向）
COMMISSION_MIN = 5.0         # 佣金最低5元/笔
TRANSFER_FEE_RATE = 0.00001  # 过户费 万0.1（买卖双向）


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


# ==================== 交易循环工具 ====================

def _equity_and_drawdown(records: list[dict]) -> tuple[list[dict], float, float]:
    """根据 [(date, value), ...] 记录权益曲线并计算最大回撤。"""
    # 注：本函数保留以备复用，主流程直接在循环里维护
    import math
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


# ==================== 信号-执行解耦架构（AlphaModel 重构）====================
# 设计参考 AI Hedge Fund 的 AlphaModel：策略只产出信号(BUY/SELL/HOLD)，
# 执行器统一处理滑点/仓位/涨跌停/手续费/权益曲线/交易日志。
# 所有原有 _backtest_* 函数保留作为 fallback，保证完全向后兼容。


class SignalGenerator:
    """信号生成器抽象基类（AlphaModel 架构）。

    核心思想：策略只产出信号，执行器统一处理交易。
    子类实现 generate() 返回 'BUY' / 'SELL' / 'HOLD'。
    可选重写 prepare() 预计算指标、execute() 提供自定义执行逻辑。
    """

    #: 策略名（用于信号特征记录）
    name: str = ""

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        """预计算指标，返回增强后的 df（含 dropna）。默认无操作。"""
        return df

    def generate(self, df: pd.DataFrame, i: int, position: bool) -> str:
        """返回交易信号。position=True 表示当前持仓。

        Returns:
            'BUY' | 'SELL' | 'HOLD'
        """
        raise NotImplementedError

    def execute(self, df: pd.DataFrame, capital: float, **opts) -> Optional[dict]:
        """自定义执行逻辑。返回 None 表示使用默认执行器 _execute_signals。"""
        return None

    def min_rows(self) -> int:
        """策略所需最小有效行数（prepare/dropna 后）。默认 5。"""
        return 5


def _execute_signals(
    generator: SignalGenerator,
    df: pd.DataFrame,
    capital: float,
    *,
    symbol: str = "",
    record_signals: bool = False,
    signal_log: Optional[list] = None,
    enable_cost: bool = True,
    percentage: float = 100.0,
    slippage: float = 0.001,
    apply_limit_filter: bool = True,
    **opts,
) -> dict[str, Any]:
    """统一信号执行器：遍历 K 线，按 generator 产生的信号执行交易。

    处理：
      - 滑点 (_buy_price / _sell_price)
      - 仓位管理 (percentage)
      - 涨跌停过滤 (_can_buy / _can_sell)
      - 手续费 (apply_buy_cost / apply_sell_cost)
      - 权益曲线记录
      - 交易日志（末 20 条）
      - 最大回撤
      - 期末自动平仓

    返回与 _backtest_* 同构的 dict（向后兼容）。
    """
    capital = float(capital)
    pct = max(min(float(percentage), 100.0), 0.0) / 100.0
    strat_name = getattr(generator, "name", "")

    shares = 0.0
    cash = capital
    buy_price = 0.0
    trades_log: list[dict] = []
    equity_curve: list[dict] = []
    wins = 0
    total_sells = 0
    peak_value = capital
    max_dd = 0.0

    for i in range(1, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]
        close = float(row["close"])
        date = row["date"].strftime("%Y-%m-%d")
        prev_close = float(prev["close"])

        position = shares > 0
        try:
            sig = generator.generate(df, i, position)
        except Exception:
            sig = "HOLD"

        # ---- BUY ----
        if sig == "BUY" and not position and cash > 0:
            # 涨跌停过滤（涨停无法买入）
            if apply_limit_filter and not _can_buy(row, prev_close, symbol):
                pass
            else:
                if record_signals and signal_log is not None:
                    from ..signal_features import build_signal_features
                    feat = build_signal_features(df, i, symbol, 1, strat_name)
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

        # ---- SELL ----
        elif sig == "SELL" and position:
            # 涨跌停过滤（跌停无法卖出）
            if apply_limit_filter and not _can_sell(row, prev_close, symbol):
                pass
            else:
                if record_signals and signal_log is not None:
                    from ..signal_features import build_signal_features
                    feat = build_signal_features(df, i, symbol, -1, strat_name)
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

        # ---- 权益 & 回撤 ----
        value = cash + shares * close
        equity_curve.append({"date": date, "value": round(value, 2)})
        if value > peak_value:
            peak_value = value
        dd = (peak_value - value) / peak_value * 100.0 if peak_value > 0 else 0.0
        if dd > max_dd:
            max_dd = dd

    # ---- 期末平仓 ----
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
