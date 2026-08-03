"""LLM 客户端：OpenAI 兼容协议，支持任意 provider。

用户配置的 base_url/api_key/model 从这里生效。无 api_key 时
降级为本地模拟输出（用于开发调试和演示）。
"""
from __future__ import annotations

import json
from typing import Any

from openai import OpenAI

from .config import get_config


class LLMClient:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or get_config()

    @property
    def _client(self) -> OpenAI | None:
        api_key = (self.config.get("api_key") or "").strip()
        if not api_key:
            return None
        base_url = (self.config.get("base_url") or "").strip()
        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        return OpenAI(**kwargs)

    def chat(
        self,
        system: str,
        user: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """调用 LLM 返回文本；无 api_key 时返回模拟输出。"""
        client = self._client
        if client is None:
            return self._mock(system, user)
        try:
            resp = client.chat.completions.create(
                model=self.config.get("model", "deepseek-chat"),
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=temperature if temperature is not None else float(self.config.get("temperature", 0.3)),
                max_tokens=max_tokens or int(self.config.get("max_tokens", 4096)),
            )
            return resp.choices[0].message.content or ""
        except Exception as e:  # 网络/密钥错误时给友好提示
            return f"[LLM调用失败: {e}]"

    def chat_json(self, system: str, user: str) -> dict[str, Any]:
        """调用 LLM 并解析 JSON 输出。"""
        text = self.chat(system, user)
        return self._parse_json(text)

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any]:
        """容错解析：剥离 markdown 代码块后解析 JSON。"""
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
        cleaned = cleaned.strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            # 尝试提取第一个 {...} 块
            start, end = cleaned.find("{"), cleaned.rfind("}")
            if start != -1 and end != -1:
                try:
                    return json.loads(cleaned[start : end + 1])
                except json.JSONDecodeError:
                    pass
            return {"error": "无法解析JSON", "raw": text[:500]}

    def _mock(self, system: str, user: str) -> str:
        """无 API key 时的模拟输出，保证流水线可跑通。"""
        return json.dumps(
            {
                "summary": "（模拟输出：未配置 API Key，此为占位结论）",
                "score": 0,
                "evidence": ["未配置 LLM API Key，请在前端设置页填写"],
                "risk_points": ["演示模式无实际分析"],
            },
            ensure_ascii=False,
        )
