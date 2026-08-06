"""Agentic 分析师：手写 ReAct 工具调用循环。

跟 TradingAgents 的 LangGraph 条件边 + ToolNode 思路一致，但更轻量：
while 循环：LLM 思考 -> 选工具 -> 执行 -> 把结果喂回 -> 再思考 -> ... -> 最终输出 AnalystView

继承关系（MRO）：AgenticXxx(AgenticAnalyst, XxxAnalyst)
- role/title/system_prompt 取自具体 XxxAnalyst
- analyze() 取自 AgenticAnalyst（覆盖 XxxAnalyst.analyze）
"""
from __future__ import annotations

from typing import Any

from .agent_tools import TOOL_REGISTRY, build_tool_descriptions, get_tools_for_role
from .base import Agent
from ..models import AnalystView

MAX_TOOL_CALLS = 4  # 每个分析师最多调 4 次工具，防止无限循环


class AgenticAnalyst(Agent):
    """可自主调工具的分析师。

    流程：
    1. 告诉 LLM 有哪些工具可用
    2. LLM 选择调用某个工具，或直接给结论
    3. 如果调工具：执行工具，把结果喂回 LLM
    4. 重复直到 LLM 给出最终 AnalystView 或达到 MAX_TOOL_CALLS
    """

    def analyze(self, context: dict[str, Any]) -> AnalystView:
        ticker = context.get("ticker", "")
        # 触发工具集构建（同时校验该角色有可用工具；无则退化为只取 quote）
        get_tools_for_role(self.role)
        tool_desc = build_tool_descriptions(self.role)

        system = (
            self.system_prompt
            + "\n\n你可以使用以下工具获取数据（每个工具接受 ticker 参数）：\n"
            f"{tool_desc}\n\n"
            "工作流程：\n"
            "1. 先调用需要的工具获取数据\n"
            "2. 根据数据给出分析结论\n"
            "3. 如果已有足够数据，直接输出最终结论\n\n"
            "输出格式（二选一，只输出其中一种）：\n"
            "A. 调用工具: TOOL: 工具名\n"
            'B. 最终结论: {"score": 数字, "summary": "摘要", "evidence": ["证据1", "证据2"], "risk_points": ["风险1"]}'
        )

        tool_results: list[str] = []
        response = ""

        for i in range(MAX_TOOL_CALLS):
            user_msg = f"标的: {ticker}"
            if tool_results:
                user_msg += "\n\n已获取的数据：\n" + "\n\n".join(tool_results)
            if i == MAX_TOOL_CALLS - 1:
                user_msg += "\n\n（已达工具调用上限，请基于现有数据给出最终结论）"

            response = self.llm.chat(system, user_msg)

            if "TOOL:" in response:
                tool_name = response.split("TOOL:")[1].strip().split("\n")[0].strip()
                fn = TOOL_REGISTRY.get(tool_name)
                if fn:
                    try:
                        result = fn(ticker)
                        tool_results.append(f"[{tool_name}]\n{result}")
                    except Exception as e:  # 工具执行异常不致命，记录后继续
                        tool_results.append(f"[{tool_name}] 调用失败: {e}")
                else:
                    tool_results.append(f"[{tool_name}] 工具不存在")
                continue
            else:
                # LLM 给出最终结论，跳出循环解析
                break
        else:
            # 循环耗尽仍未给出最终结论：强制再问一次让其收尾
            response = self.llm.chat(
                self.system_prompt,
                f"标的: {ticker}\n\n基于以下数据给出最终结论：\n" + "\n\n".join(tool_results),
            )

        return self._parse_response(response)

    def _parse_response(self, response: str) -> AnalystView:
        """解析 LLM 输出为 AnalystView。失败兜底为 score=0。"""
        import json

        data: dict[str, Any]
        try:
            start = response.find("{")
            end = response.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(response[start:end])
            else:
                data = {"score": 0, "summary": response[:200], "evidence": [], "risk_points": []}
        except Exception:
            data = {
                "score": 0,
                "summary": response[:200] if response else "分析失败",
                "evidence": [],
                "risk_points": [],
            }
        return self._view(self.role, self.title, data)
