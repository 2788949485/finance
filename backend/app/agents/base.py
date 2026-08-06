"""角色基类：基于 LangChain 的智能体协议。

每个角色 = 系统提示词 + 输入数据 + ChatOpenAI 结构化输出，
输出直接解析为 AnalystView。结构化输出失败时自动回退 JSON 解析。
"""
from __future__ import annotations

import json
from typing import Any

from langchain_core.prompts import ChatPromptTemplate

from ..llm import LLMClient
from ..models import AnalystView


class Agent:
    role: str = "agent"
    title: str = "智能体"
    system_prompt: str = ""

    def __init__(self, llm: LLMClient | None = None):
        self.llm = llm or LLMClient()

    def analyze(self, context: dict[str, Any]) -> AnalystView:
        """子类实现：从 context 提取数据，调用 LLM，返回结构化结论。"""
        raise NotImplementedError

    def _memory_block(self, context: dict[str, Any]) -> str:
        """从 context 提取用户记忆，拼成 prompt 片段。"""
        memories = context.get("user_memories") or []
        if not memories:
            return ""
        items = "\n".join(f"- {m}" for m in memories[:10])
        return f"\n\n【用户画像与偏好】\n以下是该用户的历史偏好记录，分析时请适当参考（如用户偏保守则更看重风险，偏激进则更看重弹性）：\n{items}\n"

    def _call_structured(self, user_prompt: str, context: dict[str, Any] | None = None) -> AnalystView:
        """LangChain 结构化输出（with_structured_output），失败回退 JSON 解析。"""
        # 注入用户记忆到 prompt 尾部
        if context:
            user_prompt += self._memory_block(context)
            # 注入历史决策反思记忆（过往判断的复盘经验）
            reflection = context.get("reflection_memory") or ""
            if reflection:
                user_prompt += f"\n\n{reflection}"
        model = self.llm._build_model()
        if model is None:
            data = json.loads(self.llm._mock(self.system_prompt, user_prompt))
            return self._view(self.role, self.title, data)
        prompt = ChatPromptTemplate.from_messages(
            [("system", self.system_prompt), ("human", "{question}")]
        )
        try:
            chain = prompt | model.with_structured_output(AnalystView)
            view = chain.invoke({"question": user_prompt})
            if isinstance(view, AnalystView):
                return view
            raise ValueError("非 AnalystView 输出")
        except Exception:
            return self._fallback(user_prompt)

    def _fallback(self, user_prompt: str) -> AnalystView:
        """回退：普通 chat + JSON 解析。"""
        data = self.llm.chat_json(self.system_prompt, user_prompt)
        return self._view(self.role, self.title, data)

    @staticmethod
    def _view(role: str, title: str, data: dict[str, Any]) -> AnalystView:
        score = data.get("score")
        try:
            score = float(score) if score is not None else 0.0
        except (TypeError, ValueError):
            score = 0.0
        score = max(-10.0, min(10.0, score))
        evidence = [str(e) for e in data.get("evidence", [])][:6]
        summary = str(data.get("summary", "")).strip()
        # 兜底：模型省略 summary 时用首条证据或提示语填充
        if not summary:
            summary = "核心依据：" + evidence[0] if evidence else "（该角色未给出摘要）"
        return AnalystView(
            role=role,
            title=title,
            summary=summary,
            score=score,
            evidence=evidence,
            risk_points=[str(r) for r in data.get("risk_points", [])][:4],
        )
