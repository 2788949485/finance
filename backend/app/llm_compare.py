"""多LLM模型对比：同一问题并行调用多个模型，对比回答。

用户在配置页保存多个LLM配置，对比功能用同样的prompt并行调用，
返回每个模型的回答+耗时+token数（如可用）。
"""
from __future__ import annotations

import time
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI


def compare_models(
    prompt: str,
    models: list[dict[str, str]],
    system: str = "你是金融分析助手，请专业、简洁地回答问题。",
) -> list[dict[str, Any]]:
    """并行调用多个LLM对比回答。

    models: [{"name": "DeepSeek", "base_url": "...", "api_key": "...", "model": "..."}, ...]
    返回: [{"name", "model", "response", "latency_ms", "error"}, ...]
    """
    results: list[dict[str, Any]] = []
    for m in models:
        name = m.get("name", m.get("model", "unknown"))
        api_key = (m.get("api_key") or "").strip()
        base_url = (m.get("base_url") or "").strip()
        model_name = m.get("model", "")

        if not api_key or not model_name:
            results.append({
                "name": name, "model": model_name,
                "response": "", "latency_ms": 0,
                "error": "未配置API Key或模型名",
            })
            continue

        kwargs: dict[str, Any] = {
            "model": model_name, "api_key": api_key,
            "temperature": 0.3, "max_tokens": 2048, "timeout": 60,
        }
        if base_url:
            kwargs["base_url"] = base_url

        start = time.time()
        try:
            llm = ChatOpenAI(**kwargs)
            resp = llm.invoke([SystemMessage(content=system), HumanMessage(content=prompt)])
            latency = int((time.time() - start) * 1000)
            results.append({
                "name": name, "model": model_name,
                "response": resp.content or "",
                "latency_ms": latency, "error": "",
            })
        except Exception as e:
            latency = int((time.time() - start) * 1000)
            results.append({
                "name": name, "model": model_name,
                "response": "", "latency_ms": latency,
                "error": str(e)[:200],
            })

    return results
