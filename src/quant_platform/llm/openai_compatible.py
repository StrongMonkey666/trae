"""OpenAI 兼容协议的 LLM 客户端。

覆盖：OpenAI、DeepSeek、通义千问（DashScope 兼容模式）、Ollama 等。
只需配置 base_url / api_key / model 即可切换。
"""
from __future__ import annotations

import json
from typing import List

from ..utils.exceptions import DataSourceError
from ..utils.logger import get_logger
from .base import LLMClient, LLMMessage, LLMResponse

logger = get_logger(__name__)


class OpenAICompatibleClient(LLMClient):
    name = "openai_compatible"

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-4o-mini",
        timeout: int = 60,
        **kwargs,
    ) -> None:
        super().__init__(model=model, timeout=timeout, **kwargs)
        if not api_key:
            raise ValueError("api_key 不能为空")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        # 延迟导入
        import httpx  # noqa

        self._client = httpx.Client(timeout=timeout)

    def chat(
        self,
        messages: List[LLMMessage],
        temperature: float = 0.1,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            r = self._client.post(url, json=payload, headers=headers)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            raise DataSourceError(f"LLM 调用失败: {e}") from e

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as e:
            raise DataSourceError(f"LLM 返回结构异常: {data}") from e

        return LLMResponse(
            content=content,
            model=data.get("model", self.model),
            usage=data.get("usage"),
            raw=data,
        )

    def close(self) -> None:
        self._client.close()
