"""分析师智能体：宏观、基本面、技术面、情绪面、资金面。

每个角色从 context 提取自己关心的数据，基于 LangChain 结构化输出
产出带评分的独立观点（score: -10 看空 ~ +10 看多）。
"""
from __future__ import annotations

from typing import Any

from ..models import AnalystView
from .base import Agent

SCORE_HINT = """评分规则：
- score 为 -10（强烈看空）到 +10（强烈看多）之间的整数/小数
- 0 表示中性/看不清方向
- evidence 列出 2-4 条支撑结论的关键数据
- risk_points 列出 1-3 条风险点
只输出 JSON，不要输出其他文字。"""


class MacroAnalyst(Agent):
    """宏观分析师：市场环境、流动性、政策面。"""
    role = "macro"
    title = "宏观分析师"
    system_prompt = (
        "你是资深宏观分析师，擅长A股市场环境研判：货币政策、财政政策、"
        "市场流动性、风险偏好、外围市场影响。"
        "基于给定的市场数据给出对当前A股整体环境的判断。"
        + SCORE_HINT
    )

    def analyze(self, context: dict[str, Any]) -> AnalystView:
        brief = context.get("brief") or {}
        data_block = (
            f"标的: {brief.get('name', context.get('ticker'))} ({context.get('ticker')})\n"
            f"行业: {brief.get('industry', '未知')}\n"
            f"当前价: {brief.get('price', 'N/A')}  涨跌幅: {brief.get('change_pct', 'N/A')}%\n"
            f"总市值: {brief.get('market_cap', 'N/A')}\n"
            f"换手率: {brief.get('turnover', 'N/A')}%\n"
            "注：宏观数据源暂缺时，基于标的所处行业的景气度做合理推断。"
        )
        return self._call_structured(
            "请分析以下标的当前所处的市场环境（宏观与行业层面）：\n" + data_block
        )


class FundamentalAnalyst(Agent):
    """基本面分析师：财务质量、估值。"""
    role = "fundamental"
    title = "基本面分析师"
    system_prompt = (
        "你是资深基本面分析师，擅长财务分析与估值判断：营收与利润增速、"
        "盈利能力(ROE/毛利率)、财务健康度(负债率)、估值水平(PE/PB)。"
        "基于财务数据给出对该标的投资价值的判断。"
        + SCORE_HINT
    )

    def analyze(self, context: dict[str, Any]) -> AnalystView:
        brief = context.get("brief") or {}
        fin = context.get("financials") or {}
        data_block = (
            f"标的: {brief.get('name', context.get('ticker'))} ({context.get('ticker')})\n"
            f"最新价: {brief.get('price', 'N/A')}  PE(动): {brief.get('pe', 'N/A')}  PB: {brief.get('pb', 'N/A')}\n"
            f"报告期: {fin.get('period', 'N/A')}\n"
            f"营收: {fin.get('revenue', 'N/A')}  营收同比: {fin.get('revenue_yoy', 'N/A')}%\n"
            f"净利润: {fin.get('net_profit', 'N/A')}  净利同比: {fin.get('net_profit_yoy', 'N/A')}%\n"
            f"ROE: {fin.get('roe', 'N/A')}  毛利率: {fin.get('gross_margin', 'N/A')}  负债率: {fin.get('debt_ratio', 'N/A')}%\n"
        )
        return self._call_structured(
            "请分析以下标的的基本面与估值：\n" + data_block
        )


class TechnicalAnalyst(Agent):
    """技术面分析师：趋势、均线、量价、RSI。"""
    role = "technical"
    title = "技术面分析师"
    system_prompt = (
        "你是资深技术面分析师，擅长趋势研判：均线系统(MA5/20/60)、"
        "动量指标(RSI)、量价关系、支撑压力位。"
        "基于技术指标给出对该标的技术形态的判断。"
        + SCORE_HINT
    )

    def analyze(self, context: dict[str, Any]) -> AnalystView:
        tech = context.get("tech") or {}
        data_block = (
            f"标的: {context.get('ticker')}\n"
            f"现价: {tech.get('price', 'N/A')}  MA5: {tech.get('ma5', 'N/A')}  "
            f"MA20: {tech.get('ma20', 'N/A')}  MA60: {tech.get('ma60', 'N/A')}\n"
            f"近5日: {tech.get('ret_5d', 'N/A')}%  近20日: {tech.get('ret_20d', 'N/A')}%  "
            f"近60日: {tech.get('ret_60d', 'N/A')}%\n"
            f"RSI14: {tech.get('rsi14', 'N/A')}  量比: {tech.get('volume_ratio', 'N/A')}\n"
            f"60日高点: {tech.get('high_60d', 'N/A')}  60日低点: {tech.get('low_60d', 'N/A')}\n"
        )
        return self._call_structured(
            "请分析以下标的的技术形态：\n" + data_block
        )


class SentimentAnalyst(Agent):
    """情绪面分析师：新闻舆情、市场情绪。"""
    role = "sentiment"
    title = "情绪面分析师"
    system_prompt = (
        "你是市场情绪分析师，擅长舆情与新闻解读：消息面利好利空、"
        "市场关注度、情绪温度。基于新闻标题和资金数据判断市场对该标的的情绪。"
        + SCORE_HINT
    )

    def analyze(self, context: dict[str, Any]) -> AnalystView:
        news = context.get("news") or []
        news_block = "\n".join(
            f"- [{n.get('time', '')}] {n.get('title', '')}" for n in news[:8]
        ) or "（暂无新闻数据）"
        data_block = (
            f"标的: {context.get('ticker')}\n最近新闻：\n{news_block}\n"
        )
        return self._call_structured(
            "请基于以下新闻信息判断市场情绪：\n" + data_block
        )


class CapitalAnalyst(Agent):
    """资金面分析师：龙虎榜、主力资金、换手。"""
    role = "capital"
    title = "资金面分析师"
    system_prompt = (
        "你是资金面分析师，擅长资金行为分析：龙虎榜席位（游资/机构/北向）、"
        "买卖力量对比、换手活跃度。基于资金数据判断主力动向。"
        + SCORE_HINT
    )

    def analyze(self, context: dict[str, Any]) -> AnalystView:
        lhb = context.get("lhb")
        brief = context.get("brief") or {}
        if lhb:
            data_block = (
                f"标的: {context.get('ticker')}\n"
                f"最近上榜日: {lhb.get('date', 'N/A')}\n"
                f"上榜原因: {lhb.get('reason', 'N/A')}\n"
                f"龙虎榜净买额: {lhb.get('net_buy', 'N/A')}元\n"
                f"买入额: {lhb.get('buy_total', 'N/A')}  卖出额: {lhb.get('sell_total', 'N/A')}\n"
                f"当日换手率: {brief.get('turnover', 'N/A')}%\n"
            )
        else:
            data_block = (
                f"标的: {context.get('ticker')}\n"
                f"（近30日无龙虎榜记录，以换手率与市值特征推断资金活跃度）\n"
                f"换手率: {brief.get('turnover', 'N/A')}%  总市值: {brief.get('market_cap', 'N/A')}\n"
            )
        return self._call_structured(
            "请分析以下标的的资金面动向：\n" + data_block
        )


ALL_ANALYSTS = [
    MacroAnalyst,
    FundamentalAnalyst,
    TechnicalAnalyst,
    SentimentAnalyst,
    CapitalAnalyst,
]
