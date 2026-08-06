"""Agentic 分析师：LangChain 原生工具调用机制。

用 model.bind_tools() + tool_calls 循环替代手写 "TOOL:" 文本解析。
更稳定、更智能 -- 模型自动决定何时调工具、调哪个、传什么参数。

继承关系（MRO）：AgenticXxx(AgenticAnalyst, XxxAnalyst)
- role/title/system_prompt 取自具体 XxxAnalyst
- analyze() 取自 AgenticAnalyst（覆盖 XxxAnalyst.analyze）
"""
from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from .agent_tools import TOOL_REGISTRY, build_tool_descriptions, get_tools_for_role
from .base import Agent
from ..models import AnalystView

MAX_TOOL_CALLS = 6  # 每个分析师最多调6次工具，支持多轮探索


class AgenticAnalyst(Agent):
    """可自主调工具的分析师（LangChain 原生 function calling）。

    流程：
    1. 用 model.bind_tools() 把工具列表绑定到模型
    2. LLM 返回 tool_calls（结构化，不是文本解析）
    3. 执行工具，结果通过 ToolMessage 喂回
    4. 重复直到 LLM 不再调工具，直接给出结论
    """

    def analyze(self, context: dict[str, Any]) -> AnalystView:
        ticker = context.get("ticker", "")
        on_tool = context.get("_on_tool_call")  # 回调: (tool_name, result) -> None

        # 获取该角色可用工具
        tool_names = list(ANALYST_TOOLS.keys()) if False else None  # 不用
        from .agent_tools import ANALYST_TOOLS as _AT
        allowed = _AT.get(self.role, ["get_quote"])
        tool_fns = [TOOL_REGISTRY[n] for n in allowed if n in TOOL_REGISTRY]

        model = self.llm._build_model()
        if model is None:
            # 无API key，用mock模式走文本循环
            return self._fallback_analyze(ticker, context)

        # 用LangChain @tool装饰器包装工具（bind_tools需要）
        from langchain_core.tools import tool as lc_tool

        @lc_tool
        def _wrapped(tool_name: str, _ticker: str) -> str:
            """调用指定工具获取数据。tool_name可选值见工具列表，_ticker固定传股票代码。"""
            fn = TOOL_REGISTRY.get(tool_name)
            if fn:
                return fn(_ticker)
            return f"工具 {tool_name} 不存在"

        try:
            model_with_tools = model.bind_tools([_wrapped])
        except Exception:
            # 模型不支持bind_tools，回退到手写循环
            return self._fallback_analyze(ticker, context)

        # 构建系统prompt（含工具说明）
        tool_desc = build_tool_descriptions(self.role)
        system = (
            self.system_prompt
            + "\n\n你可以使用以下工具获取数据：\n"
            f"{tool_desc}\n\n"
            "工作流程：\n"
            "1. 先调用需要的工具获取数据\n"
            "2. 根据数据给出分析结论\n\n"
            "调用工具时传入 tool_name（工具名）和 _ticker（股票代码）。"
            "可以一次调多个工具。\n\n"
            "拿到足够数据后，直接给出最终分析结论，包含评分(-10到+10)、摘要、证据、风险点。"
        )

        # LangChain原生工具调用循环
        messages: list[Any] = [
            SystemMessage(content=system),
            HumanMessage(content=f"请分析标的: {ticker}"),
        ]
        tool_call_count = 0
        tool_results_text: list[str] = []

        for _ in range(MAX_TOOL_CALLS):
            resp = model_with_tools.invoke(messages)

            # 检查是否有tool_calls
            if hasattr(resp, "tool_calls") and resp.tool_calls:
                messages.append(resp)
                for tc in resp.tool_calls:
                    tool_name = tc.get("args", {}).get("tool_name", "")
                    if tool_name in allowed:
                        fn = TOOL_REGISTRY.get(tool_name)
                        if fn:
                            try:
                                # web_search参数是query不是ticker
                                if tool_name == "web_search":
                                    search_query = tc.get("args", {}).get("query", ticker)
                                    result = fn(search_query)
                                else:
                                    result = fn(ticker)
                                tool_results_text.append(f"[{tool_name}]\n{result}")
                                if on_tool:
                                    try:
                                        on_tool(tool_name, result[:100])
                                    except Exception:
                                        pass
                            except Exception as e:
                                result = f"[{tool_name}] 调用失败: {e}"
                                tool_results_text.append(result)
                        else:
                            result = f"[{tool_name}] 工具不存在"
                            tool_results_text.append(result)
                    else:
                        result = f"工具 {tool_name} 不在允许列表"
                    messages.append(ToolMessage(content=result, tool_call_id=tc.get("id", "")))
                tool_call_count += len(resp.tool_calls)
                continue
            else:
                # LLM给出最终结论
                final_text = resp.content if hasattr(resp, "content") else str(resp)
                return self._parse_final(ticker, final_text, tool_results_text)

        # 达到上限，强制要结论
        messages.append(HumanMessage(content="请基于已获取的数据直接给出最终结论，不要再调用工具。"))
        resp = model.invoke(messages)
        final_text = resp.content if hasattr(resp, "content") else str(resp)
        return self._parse_final(ticker, final_text, tool_results_text)

    def _fallback_analyze(self, ticker: str, context: dict[str, Any]) -> AnalystView:
        """无API key或模型不支持bind_tools时的手写文本循环回退。"""
        tool_desc = build_tool_descriptions(self.role)
        from .agent_tools import ANALYST_TOOLS as _AT
        allowed = _AT.get(self.role, ["get_quote"])

        system = (
            self.system_prompt
            + "\n\n可用工具：\n"
            f"{tool_desc}\n\n"
            "调用格式: TOOL: 工具名\n"
            '最终结论: {"score": 0, "summary": "...", "evidence": [], "risk_points": []}'
        )
        tool_results: list[str] = []
        response = ""
        for i in range(MAX_TOOL_CALLS):
            user_msg = f"标的: {ticker}"
            if tool_results:
                user_msg += "\n\n已获取的数据：\n" + "\n\n".join(tool_results)
            response = self.llm.chat(system, user_msg)
            tool_lines = [l.strip() for l in response.split("\n") if "TOOL:" in l]
            if tool_lines:
                for tl in tool_lines:
                    tn = tl.split("TOOL:")[1].strip().split()[0] if len(tl.split("TOOL:")[1].strip().split()) > 0 else ""
                    fn = TOOL_REGISTRY.get(tn)
                    if fn:
                        try:
                            if tn == "web_search":
                                # web_search参数从TOOL:行提取query
                                q = tl.split("TOOL:")[1].strip()
                                # 去掉工具名，剩下的是query
                                q_parts = q.split(None, 1)
                                query = q_parts[1] if len(q_parts) > 1 else ticker
                                r = fn(query)
                            else:
                                r = fn(ticker)
                            tool_results.append(f"[{tn}]\n{r}")
                        except Exception as e:
                            tool_results.append(f"[{tn}] 失败: {e}")
                continue
            else:
                break
        return self._parse_final(ticker, response, tool_results)

    def _parse_final(self, ticker: str, response: str, tool_results: list[str]) -> AnalystView:
        """解析LLM最终输出为AnalystView。"""
        data: dict[str, Any]
        try:
            start = response.find("{")
            end = response.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(response[start:end])
            else:
                data = {"score": 0, "summary": response[:200], "evidence": [], "risk_points": []}
        except Exception:
            summary = response[:200] if response else "分析失败"
            data = {"score": 0, "summary": summary, "evidence": [], "risk_points": []}

        # 把工具结果作为evidence补充
        if not data.get("evidence") and tool_results:
            data["evidence"] = [r[:80] for r in tool_results[:3]]

        return self._view(self.role, self.title, data)
