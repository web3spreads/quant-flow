"""
LLM 配置和工厂函数

提供统一的 LLM 初始化接口，支持：
- 普通 LLM
- JSON Mode LLM
- Structured Output LLM
"""

from typing import Optional, Type, TypeVar
from dataclasses import dataclass
from langchain_openai import ChatOpenAI
from pydantic import BaseModel


T = TypeVar('T', bound=BaseModel)


@dataclass
class LLMConfig:
    """
    LLM 配置类

    统一管理 LLM 配置参数，便于在不同 Agent 间共享。
    """
    api_base: str
    api_key: str
    model: str = "deepseek-chat"
    temperature: float = 0.1
    max_tokens: Optional[int] = None
    timeout: Optional[float] = None
    max_retries: int = 2

    def to_dict(self) -> dict:
        """转换为字典格式"""
        result = {
            "base_url": self.api_base,
            "api_key": self.api_key,
            "model": self.model,
            "temperature": self.temperature,
            "max_retries": self.max_retries,
        }
        if self.max_tokens:
            result["max_tokens"] = self.max_tokens
        if self.timeout:
            result["timeout"] = self.timeout
        return result


def create_llm(
    config: LLMConfig,
    temperature: Optional[float] = None,
) -> ChatOpenAI:
    """
    创建普通 LLM 实例

    Args:
        config: LLM 配置
        temperature: 可选的温度覆盖

    Returns:
        ChatOpenAI 实例
    """
    params = config.to_dict()
    if temperature is not None:
        params["temperature"] = temperature
    return ChatOpenAI(**params)


def create_json_llm(
    config: LLMConfig,
    temperature: Optional[float] = None,
) -> ChatOpenAI:
    """
    创建 JSON Mode LLM 实例

    启用 JSON Mode 以确保 LLM 返回纯 JSON 格式，
    提高 structured output 的成功率。

    Args:
        config: LLM 配置
        temperature: 可选的温度覆盖（建议使用 0 以确保确定性）

    Returns:
        启用 JSON Mode 的 ChatOpenAI 实例
    """
    params = config.to_dict()
    if temperature is not None:
        params["temperature"] = temperature
    params["model_kwargs"] = {"response_format": {"type": "json_object"}}
    return ChatOpenAI(**params)


def create_structured_llm(
    config: LLMConfig,
    output_schema: Type[T],
    temperature: Optional[float] = None,
) -> ChatOpenAI:
    """
    创建支持 Structured Output 的 LLM 实例

    使用 LangChain 的 with_structured_output 机制，
    自动将 LLM 输出解析为指定的 Pydantic 模型。

    Args:
        config: LLM 配置
        output_schema: 输出的 Pydantic 模型类
        temperature: 可选的温度覆盖

    Returns:
        支持 structured output 的 LLM 实例
    """
    llm = create_json_llm(config, temperature)
    return llm.with_structured_output(output_schema)


class LLMFactory:
    """
    LLM 工厂类

    提供便捷的 LLM 创建方法，支持缓存和复用。
    """

    def __init__(self, config: LLMConfig):
        """
        初始化工厂

        Args:
            config: LLM 配置
        """
        self.config = config
        self._cache = {}

    def get_llm(
        self,
        json_mode: bool = False,
        temperature: Optional[float] = None,
    ) -> ChatOpenAI:
        """
        获取 LLM 实例

        Args:
            json_mode: 是否启用 JSON Mode
            temperature: 温度参数

        Returns:
            ChatOpenAI 实例
        """
        cache_key = (json_mode, temperature)
        if cache_key not in self._cache:
            if json_mode:
                self._cache[cache_key] = create_json_llm(self.config, temperature)
            else:
                self._cache[cache_key] = create_llm(self.config, temperature)
        return self._cache[cache_key]

    def get_structured_llm(
        self,
        output_schema: Type[T],
        temperature: Optional[float] = None,
    ) -> ChatOpenAI:
        """
        获取 Structured Output LLM 实例

        Args:
            output_schema: 输出的 Pydantic 模型类
            temperature: 温度参数

        Returns:
            支持 structured output 的 LLM 实例
        """
        # Structured LLM 不缓存，因为 schema 可能不同
        return create_structured_llm(self.config, output_schema, temperature)

    def clear_cache(self):
        """清除缓存的 LLM 实例"""
        self._cache.clear()
