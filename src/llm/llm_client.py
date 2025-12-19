"""
LLM 客户端工厂和管理器

提供统一的 LLM 客户端创建和管理，支持多种 LangChain 客户端类型：
- langchain_openai: 符合 OpenAI 规范的供应商（DeepSeek、OpenAI、本地部署等）
- langchain_litellm: 更宽泛的多模型支持（通过 LiteLLM 代理）
- langchain_cloudflare: Cloudflare AI Gateway 转发的模型
- langchain_google: Google Gemini 模型
- langchain_nvidia: NVIDIA AI Endpoints
"""

import os
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, Type
from pydantic import BaseModel


class LLMClientType(str, Enum):
    """LLM 客户端类型枚举"""
    LANGCHAIN_OPENAI = "langchain_openai"
    LANGCHAIN_CLOUDFLARE = "langchain_cloudflare"
    LANGCHAIN_GOOGLE = "langchain_google"
    LANGCHAIN_LITELLM = "langchain_litellm"
    LANGCHAIN_NVIDIA = "langchain_nvidia"


@dataclass
class LLMClientConfig:
    """
    LLM 客户端配置
    
    统一的配置数据类，支持所有客户端类型的参数。
    """
    client_type: LLMClientType
    model: str
    
    # 基础参数（可选）
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_tokens: Optional[int] = None
    timeout: Optional[float] = None
    max_retries: int = 2
    
    # 额外参数（可选，用于特定客户端的高级配置）
    extra_body: Optional[Dict[str, Any]] = None
    
    # OpenAI / OpenAI-compatible 配置
    openai_api_base: Optional[str] = None
    openai_api_key: Optional[str] = None
    
    # Cloudflare 配置
    cloudflare_account_id: Optional[str] = None
    cloudflare_api_token: Optional[str] = None
    
    # Google 配置
    google_api_key: Optional[str] = None
    
    # LiteLLM 配置
    litellm_api_base: Optional[str] = None
    litellm_api_key: Optional[str] = None
    
    # NVIDIA 配置
    nvidia_api_key: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式，仅包含非 None 的值"""
        result = {
            "model": self.model,
            "max_retries": self.max_retries,
        }
        
        # 添加可选参数
        if self.temperature is not None:
            result["temperature"] = self.temperature
        if self.top_p is not None:
            result["top_p"] = self.top_p
        if self.max_tokens is not None:
            result["max_tokens"] = self.max_tokens
        if self.timeout is not None:
            result["timeout"] = self.timeout
            
        return result


class LLMClientFactory:
    """
    LLM 客户端工厂
    
    根据客户端类型创建对应的 LangChain 客户端实例。
    """
    
    @staticmethod
    def create_client(config: LLMClientConfig, **kwargs):
        """
        创建 LLM 客户端
        
        Args:
            config: LLM 客户端配置
            **kwargs: 额外的参数覆盖
            
        Returns:
            LangChain 客户端实例
            
        Raises:
            ValueError: 如果客户端类型不支持或配置不完整
        """
        client_type = config.client_type
        
        if client_type == LLMClientType.LANGCHAIN_OPENAI:
            return LLMClientFactory._create_openai_client(config, **kwargs)
        elif client_type == LLMClientType.LANGCHAIN_CLOUDFLARE:
            return LLMClientFactory._create_cloudflare_client(config, **kwargs)
        elif client_type == LLMClientType.LANGCHAIN_GOOGLE:
            return LLMClientFactory._create_google_client(config, **kwargs)
        elif client_type == LLMClientType.LANGCHAIN_LITELLM:
            return LLMClientFactory._create_litellm_client(config, **kwargs)
        elif client_type == LLMClientType.LANGCHAIN_NVIDIA:
            return LLMClientFactory._create_nvidia_client(config, **kwargs)
        else:
            raise ValueError(f"不支持的客户端类型: {client_type}")
    
    @staticmethod
    def _create_openai_client(config: LLMClientConfig, **kwargs):
        """创建 OpenAI / OpenAI-compatible 客户端"""
        from langchain_openai import ChatOpenAI
        
        if not config.openai_api_key:
            raise ValueError("langchain_openai 客户端需要 OPENAI_API_KEY")
        
        params = config.to_dict()
        params["api_key"] = config.openai_api_key
        
        if config.openai_api_base:
            params["base_url"] = config.openai_api_base
        
        # 处理 extra_body
        if config.extra_body:
            if "model_kwargs" not in params:
                params["model_kwargs"] = {}
            params["model_kwargs"].update(config.extra_body)
        
        # 应用覆盖参数
        params.update(kwargs)
        
        return ChatOpenAI(**params)
    
    @staticmethod
    def _create_cloudflare_client(config: LLMClientConfig, **kwargs):
        """创建 Cloudflare Workers AI 客户端"""
        from langchain_cloudflare import ChatCloudflare
        
        if not config.cloudflare_account_id or not config.cloudflare_api_token:
            raise ValueError("langchain_cloudflare 客户端需要 CLOUDFLARE_ACCOUNT_ID 和 CLOUDFLARE_API_TOKEN")
        
        params = config.to_dict()
        params["account_id"] = config.cloudflare_account_id
        params["api_token"] = config.cloudflare_api_token
        
        # 应用覆盖参数
        params.update(kwargs)
        
        return ChatCloudflare(**params)
    
    @staticmethod
    def _create_google_client(config: LLMClientConfig, **kwargs):
        """创建 Google Gemini 客户端"""
        from langchain_google_genai import ChatGoogleGenerativeAI
        
        if not config.google_api_key:
            raise ValueError("langchain_google 客户端需要 GOOGLE_API_KEY")
        
        params = config.to_dict()
        params["google_api_key"] = config.google_api_key
        
        # 应用覆盖参数
        params.update(kwargs)
        
        return ChatGoogleGenerativeAI(**params)
    
    @staticmethod
    def _create_litellm_client(config: LLMClientConfig, **kwargs):
        """创建 LiteLLM 客户端"""
        from langchain_litellm import ChatLiteLLM
        
        params = config.to_dict()
        
        if config.litellm_api_key:
            params["api_key"] = config.litellm_api_key
        if config.litellm_api_base:
            params["api_base"] = config.litellm_api_base
        
        # 应用覆盖参数
        params.update(kwargs)
        
        return ChatLiteLLM(**params)
    
    @staticmethod
    def _create_nvidia_client(config: LLMClientConfig, **kwargs):
        """创建 NVIDIA AI Endpoints 客户端"""
        from langchain_nvidia_ai_endpoints import ChatNVIDIA
        
        if not config.nvidia_api_key:
            raise ValueError("langchain_nvidia 客户端需要 NVIDIA_API_KEY")
        
        params = config.to_dict()
        # NVIDIA 客户端使用 'nvidia_api_key' 参数
        params["api_key"] = config.nvidia_api_key
        
        # 处理 extra_body（NVIDIA 支持 thinking 等参数）
        if config.extra_body:
            params.update(config.extra_body)
        
        # 应用覆盖参数
        params.update(kwargs)
        return ChatNVIDIA(**params)


class LLMClientManager:
    """
    LLM 客户端管理器（单例）
    
    全局单例管理器，缓存和复用客户端实例。
    """
    
    _instance: Optional['LLMClientManager'] = None
    _config: Optional[LLMClientConfig] = None
    _cache: Dict[str, Any] = {}
    
    def __init__(self, config: LLMClientConfig):
        """
        初始化管理器
        
        Args:
            config: LLM 客户端配置
        """
        self._config = config
        self._cache = {}
    
    @classmethod
    def get_instance(cls, config: Optional[LLMClientConfig] = None) -> 'LLMClientManager':
        """
        获取单例实例
        
        Args:
            config: LLM 客户端配置（首次调用时必须提供）
            
        Returns:
            LLMClientManager 单例实例
            
        Raises:
            ValueError: 如果首次调用时未提供配置
        """
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
        self,
        json_mode: bool = False,
        temperature: Optional[float] = None,
        **kwargs
    ):
        """
        获取 LLM 客户端
        
        Args:
            json_mode: 是否启用 JSON Mode（仅 OpenAI 支持）
            temperature: 温度参数覆盖
            **kwargs: 其他参数覆盖
            
        Returns:
            LangChain 客户端实例
        """
        # 生成缓存键
        cache_key = self._generate_cache_key(json_mode, temperature, **kwargs)
        
        # 检查缓存
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        # 创建新客户端
        client_kwargs = kwargs.copy()
        if temperature is not None:
            client_kwargs["temperature"] = temperature
        
        # 处理 JSON Mode（仅 OpenAI 支持）
        if json_mode and self._config.client_type == LLMClientType.LANGCHAIN_OPENAI:
            if "model_kwargs" not in client_kwargs:
                client_kwargs["model_kwargs"] = {}
            client_kwargs["model_kwargs"]["response_format"] = {"type": "json_object"}
        
        client = LLMClientFactory.create_client(self._config, **client_kwargs)
        
        # 缓存客户端
        self._cache[cache_key] = client
        
        return client
    
    def get_structured_client(
        self,
        output_schema: Type[BaseModel],
        temperature: Optional[float] = None,
        **kwargs
    ):
        """
        获取支持 Structured Output 的 LLM 客户端
        
        Args:
            output_schema: 输出的 Pydantic 模型类
            temperature: 温度参数覆盖
            **kwargs: 其他参数覆盖
            
        Returns:
            支持 structured output 的 LLM 客户端
        """
        # Structured output 不缓存，因为 schema 可能不同
        client = self.get_client(json_mode=True, temperature=temperature, **kwargs)
        return client.with_structured_output(output_schema)
    
    def _generate_cache_key(
        self,
        json_mode: bool,
        temperature: Optional[float],
        **kwargs
    ) -> str:
        """生成缓存键"""
        key_parts = [
            f"json_mode={json_mode}",
            f"temperature={temperature}",
        ]
        
        # 添加其他参数到键中
        for k, v in sorted(kwargs.items()):
            key_parts.append(f"{k}={v}")
        
        return "|".join(key_parts)
    
    def clear_cache(self):
        """清除缓存的客户端实例"""
        self._cache.clear()
