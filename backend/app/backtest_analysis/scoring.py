"""回测深度分析 - scoring模块"""
from __future__ import annotations
from typing import Any
import math


def calc_profit_factor(trades_log: list[dict]) -> float:
    """计算Profit Factor = 总盈利 / 总亏损绝对值。

    PF < 1: 长期亏损
    1.0~1.2: 优势很弱
    1.2~1.5: 有一定优势
    1.5~2.0: 较好
    > 2: 很好（但需警惕过拟合）
    """
    gross_profit = 0.0
    gross_loss = 0.0
    shares = 0
    buy_price = 0.0

    for trade in trades_log:
        if trade["action"] == "BUY":
            shares = trade["shares"]
            buy_price = trade["price"]
        elif trade["action"] == "SELL" and shares > 0:
            pnl = (trade["price"] - buy_price) * shares
            if pnl > 0:
                gross_profit += pnl
            else:
                gross_loss += abs(pnl)
            shares = 0

    return round(gross_profit / gross_loss, 4) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)




def calc_recovery_factor(net_profit: float, max_drawdown: float) -> float:
    """计算Recovery Factor = 净利润 / 最大回撤金额。

    < 1: 收益不足以覆盖风险
    1~2: 一般
    2~3: 较好
    > 3: 优秀
    """
    if max_drawdown <= 0:
        return 999.0 if net_profit > 0 else 0.0
    return round(net_profit / max_drawdown, 4)




def calc_comprehensive_score(
    total_return: float,
    max_drawdown: float,
    pf: float,
    rf: float,
    trades: int,
    benchmark_return: float = 0.0,
) -> dict[str, Any]:
    """计算综合评分（不只看净利润）。

    Score = 0.25*R + 0.20*PF + 0.20*RF + 0.15*S - 0.20*DD
    其中各项都做0-100标准化。

    返回:
    {
        score: float,          # 综合评分 0-100
        annual_return_score: float,
        pf_score: float,
        rf_score: float,
        stability_score: float,
        drawdown_penalty: float,
        grade: str,            # A/B/C/D/F
        warnings: list[str],   # 风险提示
    }
    """
    # 年化收益得分（假设250天=1年）
    r_score = min(max(total_return, -100), 100)

    # PF得分（PF=2映射到80分，PF=1映射到40分）
    pf_score = min(pf * 40, 100) if pf > 0 else 0

    # RF得分（RF=3映射到75分）
    rf_score = min(rf * 25, 100) if rf > 0 else 0

    # 稳定性得分（交易次数越多越稳定，但不是线性）
    if trades >= 100:
        s_score = 100
    elif trades >= 30:
        s_score = 60 + (trades - 30) * 40 / 70
    elif trades >= 10:
        s_score = 30 + (trades - 10) * 30 / 20
    else:
        s_score = max(0, trades * 3)

    # 回撤惩罚（回撤>30%严重扣分）
    dd_penalty = min(max_drawdown * 2, 100)

    # 综合分
    score = 0.25 * r_score + 0.20 * pf_score + 0.20 * rf_score + 0.15 * s_score - 0.20 * dd_penalty
    score = max(0, min(100, round(score, 1)))

    # 评级
    if score >= 75:
        grade = "A"
    elif score >= 60:
        grade = "B"
    elif score >= 45:
        grade = "C"
    elif score >= 30:
        grade = "D"
    else:
        grade = "F"

    # 风险提示
    warnings = []
    if pf < 1.0:
        warnings.append("Profit Factor < 1，策略长期亏损")
    if max_drawdown > 30:
        warnings.append(f"最大回撤{max_drawdown:.1f}%超过30%，高风险")
    if trades < 10:
        warnings.append(f"仅{trades}次交易，统计意义不足")
    if rf < 1:
        warnings.append("Recovery Factor < 1，收益不足以覆盖风险")
    if pf > 3 and trades < 50:
        warnings.append("PF很高但交易次数少，警惕过拟合")
    if total_return > 0 and benchmark_return > 0 and total_return < benchmark_return:
        warnings.append("策略收益跑输基准")

    return {
        "score": score,
        "grade": grade,
        "annual_return_score": round(r_score, 1),
        "pf_score": round(pf_score, 1),
        "rf_score": round(rf_score, 1),
        "stability_score": round(s_score, 1),
        "drawdown_penalty": round(dd_penalty, 1),
        "warnings": warnings,
    }


# ==================== 2. 蒙特卡洛压力测试 ====================

