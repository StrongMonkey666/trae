"""LLM 客户端抽象基类。

具体实现只需实现 chat() 方法即可，SelectorService 通过 LLMClient 接口调用。
"""
from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class LLMMessage:
    role: str          # system / user / assistant
    content: str


@dataclass
class LLMResponse:
    content: str
    model: str = ""
    usage: dict | None = None
    raw: dict | None = None


class LLMClient(abc.ABC):
    """LLM 客户端统一接口。"""

    name: str = "base"

    def __init__(self, model: str = "", timeout: int = 60, **kwargs) -> None:
        self.model = model
        self.timeout = timeout
        self._kwargs = kwargs

    @abc.abstractmethod
    def chat(
        self,
        messages: List[LLMMessage],
        temperature: float = 0.1,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        """与 LLM 对话，返回纯文本响应。"""
