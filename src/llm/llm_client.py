"""
LLM 客户端工厂和管理器

提供统一的 LLM 客户端创建和管理，支持多种 Pydantic AI 模型：
- OpenAIModel: 符合 OpenAI 规范的模型（DeepSeek、OpenAI、本地部署、Cloudflare、LiteLLM、NVIDIA 等）
- GeminiModel: Google Gemini 模型
"""

import json
import threading
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Optional

from pydantic_ai import ModelSettings
from pydantic_ai.messages import TextPart
from pydantic_ai.models import Model, ModelResponse
from pydantic_ai.models.gemini import GeminiModel
from pydantic_ai.models.openai import OpenAIModel


class LLMClientType(StrEnum):
    """LLM 客户端类型枚举"""

    LANGCHAIN_OPENAI = "langchain_openai"
    LANGCHAIN_CLOUDFLARE = "langchain_cloudflare"
    LANGCHAIN_GOOGLE = "langchain_google"
    LANGCHAIN_LITELLM = "langchain_litellm"
    LANGCHAIN_NVIDIA = "langchain_nvidia"


class MockModelAdapter(Model):
    """适配单测中的 mock LLM 客户端，以便正常走完 Pydantic AI 的运行路径"""

    def __init__(self, mock_llm):
        self.mock_llm = mock_llm

    async def request(self, messages, model_settings, model_request_parameters):
        class DummyMessage:
            def __init__(self, content):
                self.content = content

        legacy_messages = []
        for msg in messages:
            if hasattr(msg, "parts"):
                for part in msg.parts:
                    content = ""
                    if hasattr(part, "content"):
                        content = part.content
                    elif hasattr(part, "text"):
                        content = part.text
                    content = content or ""
                    legacy_messages.append(DummyMessage(content))
            else:
                legacy_messages.append(DummyMessage(str(msg)))

        # 调用 mock LLM 的 invoke
        response = self.mock_llm.invoke(legacy_messages)
        content_text = response.content if hasattr(response, "content") else str(response)

        # Guard against empty/None mock responses to satisfy Pydantic AI's non-empty requirement
        if not content_text or not content_text.strip():
            is_bull = any(
                "多头" in str(part)
                for msg in messages
                if hasattr(msg, "parts")
                for part in msg.parts
            )
            is_bear = any(
                "空头" in str(part)
                for msg in messages
                if hasattr(msg, "parts")
                for part in msg.parts
            )
            if is_bull:
                content_text = "（多头分析失败）"
            elif is_bear:
                content_text = "（空头分析失败）"
            else:
                content_text = "（分析失败）"
        else:
            content_text = content_text or ""

        return ModelResponse(parts=[TextPart(content=content_text)])

    @property
    def model_name(self) -> str:
        return "mock-model"

    @property
    def system(self) -> str | None:
        return "mock-provider"


def wrap_llm_client(llm: Any) -> Model:
    """包装 mock / 传统调用客户端为 Pydantic AI Model"""
    if llm is None:
        return None
    if isinstance(llm, Model):
        return llm
    if hasattr(llm, "invoke") or type(llm).__name__ in ("MagicMock", "Mock", "AgentFakeLLM"):
        return MockModelAdapter(llm)
    return llm


@dataclass
class LLMClientConfig:
    """
    LLM 客户端配置

    统一的配置数据类，支持所有客户端类型的参数。
    """

    client_type: LLMClientType
    model: str

    # 基础参数（可选）
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    timeout: float | None = None
    max_retries: int = 2

    # 额外参数（可选，用于特定客户端的高级配置）
    extra_body: dict[str, Any] | None = None

    def __post_init__(self):
        """验证配置的完整性"""
        self.validate()

    def validate(self):
        """
        验证当前配置是否满足所选 client_type 的必需字段要求
        """
        ct = self.client_type

        if ct == LLMClientType.LANGCHAIN_OPENAI:
            if not self.openai_api_key:
                raise ValueError("openai_api_key is required when client_type is LANGCHAIN_OPENAI")
        elif ct == LLMClientType.LANGCHAIN_CLOUDFLARE:
            if not self.cloudflare_api_token or not self.cloudflare_account_id:
                raise ValueError(
                    "cloudflare_api_token and cloudflare_account_id are required when client_type is LANGCHAIN_CLOUDFLARE"
                )
        elif ct == LLMClientType.LANGCHAIN_GOOGLE:
            if not self.google_api_key:
                raise ValueError("google_api_key is required when client_type is LANGCHAIN_GOOGLE")
        elif ct == LLMClientType.LANGCHAIN_LITELLM:
            if not self.litellm_api_key:
                raise ValueError(
                    "litellm_api_key is required when client_type is LANGCHAIN_LITELLM"
                )
        elif ct == LLMClientType.LANGCHAIN_NVIDIA and not self.nvidia_api_key:
            raise ValueError("nvidia_api_key is required when client_type is LANGCHAIN_NVIDIA")

    # OpenAI / OpenAI-compatible 配置
    openai_api_base: str | None = None
    openai_api_key: str | None = None

    # Cloudflare 配置
    cloudflare_account_id: str | None = None
    cloudflare_api_token: str | None = None

    # Google 配置
    google_api_key: str | None = None

    # LiteLLM 配置
    litellm_api_base: str | None = None
    litellm_api_key: str | None = None

    # NVIDIA 配置
    nvidia_api_key: str | None = None


class LLMClientFactory:
    """
    LLM 客户端工厂

    根据客户端类型创建对应的 Pydantic AI Model 实例。
    """

    @classmethod
    def create_client(cls, config: LLMClientConfig, **kwargs) -> Model:
        """
        创建 Pydantic AI Model 实例

        Args:
            config: LLM 客户端配置
            **kwargs: 额外的参数覆盖

        Returns:
            Pydantic AI Model 实例
        """
        client_type = config.client_type

        # Override config parameters with kwargs
        temperature = kwargs.get("temperature", config.temperature)
        top_p = kwargs.get("top_p", config.top_p)
        max_tokens = kwargs.get("max_tokens", config.max_tokens)
        timeout = kwargs.get("timeout", config.timeout)
        extra_body = kwargs.get("extra_body", config.extra_body)

        # Build settings via ModelSettings
        settings = ModelSettings()
        if temperature is not None:
            settings["temperature"] = temperature
        if top_p is not None:
            settings["top_p"] = top_p
        if max_tokens is not None:
            settings["max_tokens"] = max_tokens
        if timeout is not None:
            settings["timeout"] = timeout
        if extra_body is not None:
            settings["extra_body"] = extra_body

        if client_type == LLMClientType.LANGCHAIN_OPENAI:
            if not config.openai_api_key:
                raise ValueError("langchain_openai 客户端需要 OPENAI_API_KEY")
            from pydantic_ai.providers.openai import OpenAIProvider

            provider = OpenAIProvider(
                base_url=config.openai_api_base,
                api_key=config.openai_api_key,
            )
            return OpenAIModel(
                model_name=config.model,
                provider=provider,
                settings=settings,
            )
        elif client_type == LLMClientType.LANGCHAIN_CLOUDFLARE:
            if not config.cloudflare_account_id or not config.cloudflare_api_token:
                raise ValueError(
                    "langchain_cloudflare 客户端需要 CLOUDFLARE_ACCOUNT_ID 和 CLOUDFLARE_API_TOKEN"
                )
            from pydantic_ai.providers.openai import OpenAIProvider

            base_url = f"https://api.cloudflare.com/client/v4/accounts/{config.cloudflare_account_id}/ai/v1"
            provider = OpenAIProvider(
                base_url=base_url,
                api_key=config.cloudflare_api_token,
            )
            return OpenAIModel(
                model_name=config.model,
                provider=provider,
                settings=settings,
            )
        elif client_type == LLMClientType.LANGCHAIN_GOOGLE:
            if not config.google_api_key:
                raise ValueError("langchain_google 客户端需要 GOOGLE_API_KEY")
            from pydantic_ai.providers.google_gla import GoogleGLAProvider

            provider = GoogleGLAProvider(
                api_key=config.google_api_key,
            )
            return GeminiModel(
                model_name=config.model,
                provider=provider,
                settings=settings,
            )
        elif client_type == LLMClientType.LANGCHAIN_LITELLM:
            from pydantic_ai.providers.openai import OpenAIProvider

            provider = OpenAIProvider(
                base_url=config.litellm_api_base or "http://localhost:4000",
                api_key=config.litellm_api_key or "placeholder",
            )
            return OpenAIModel(
                model_name=config.model,
                provider=provider,
                settings=settings,
            )
        elif client_type == LLMClientType.LANGCHAIN_NVIDIA:
            if not config.nvidia_api_key:
                raise ValueError("langchain_nvidia 客户端需要 NVIDIA_API_KEY")
            from pydantic_ai.providers.openai import OpenAIProvider

            provider = OpenAIProvider(
                base_url="https://integrate.api.nvidia.com/v1",
                api_key=config.nvidia_api_key,
            )
            return OpenAIModel(
                model_name=config.model,
                provider=provider,
                settings=settings,
            )
        else:
            raise ValueError(f"不支持的客户端类型: {client_type}")


class LLMClientManager:
    """
    LLM 客户端管理器（单例）

    全局单例管理器，缓存和复用 Model 实例。
    """

    _instance: Optional["LLMClientManager"] = None
    _config: LLMClientConfig | None = None
    _cache: dict[str, Any] = {}
    _lock: threading.Lock = threading.Lock()

    def __init__(self, config: LLMClientConfig):
        """
        初始化管理器

        Args:
            config: LLM 客户端配置
        """
        self._config = config
        type(self)._cache.clear()

    @classmethod
    def get_instance(cls, config: LLMClientConfig | None = None) -> "LLMClientManager":
        """
        获取单例实例（线程安全）
        """
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    if config is None:
                        raise ValueError("首次调用 get_instance 时必须提供 config 参数")
                    cls._instance = cls(config)
        return cls._instance

    @classmethod
    def reset_instance(cls):
        """重置单例实例（主要用于测试）"""
        cls._instance = None
        cls._config = None
        cls._cache = {}

    def get_client(
        self, json_mode: bool = False, temperature: float | None = None, **kwargs
    ) -> Model:
        """
        获取 Pydantic AI Model 实例
        """
        # 生成缓存键
        cache_key = self._generate_cache_key(json_mode, temperature, **kwargs)

        with self._lock:
            if cache_key in self._cache:
                return self._cache[cache_key]

            client_kwargs = kwargs.copy()
            if temperature is not None:
                client_kwargs["temperature"] = temperature
            client = LLMClientFactory.create_client(self._config, **client_kwargs)

            # 包装 mock 客户端
            client = wrap_llm_client(client)

            # 缓存客户端
            self._cache[cache_key] = client
            return client

    def _generate_cache_key(self, json_mode: bool, temperature: float | None, **kwargs) -> str:
        """生成缓存键"""
        key_parts = [
            f"json_mode={json_mode}",
            f"temperature={temperature}",
        ]

        for k in sorted(kwargs.keys()):
            v = kwargs[k]
            try:
                v_str = json.dumps(v, sort_keys=True)
            except (TypeError, ValueError):
                v_str = repr(v)
            key_parts.append(f"{k}={v_str}")

        return "|".join(key_parts)

    def clear_cache(self):
        """清除缓存的客户端实例"""
        self._cache.clear()
