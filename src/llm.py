"""
LLM 客户端：单一 OpenAI 兼容端点。

DeepSeek/OpenAI/本地部署/各类网关均暴露 OpenAI 兼容接口，因此只保留
一种客户端形态：base_url + api_key + model。调用失败（网络错误、5xx、
空回复）做有界指数退避重试；重试耗尽抛出 ``LLMError``，由调用方决定
兜底策略——策略层的原则是「LLM 故障绝不放大成交易动作」。
"""

import json
import re
import time
from typing import Any

import requests

# 瞬时故障的重试等待上限（秒）
_MAX_BACKOFF_SECONDS = 5.0


class LLMError(RuntimeError):
    """LLM 调用失败（重试耗尽后抛出）。"""


class LLMClient:
    """OpenAI 兼容聊天补全客户端。

    线程安全：无共享可变状态，可被多个策略并用。
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float = 0.2,
        timeout: float = 120.0,
        max_retries: int = 3,
    ):
        """
        Args:
            base_url: OpenAI 兼容端点根地址（如 https://api.deepseek.com/v1）
            api_key: API 密钥
            model: 模型名
            temperature: 默认采样温度（交易决策建议低温度）
            timeout: 单次请求超时（秒）
            max_retries: 瞬时故障最大重试次数
        """
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.timeout = timeout
        self.max_retries = max(1, int(max_retries))
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def chat(self, system: str, user: str, temperature: float | None = None) -> str:
        """发送一轮对话，返回助手回复文本。

        空回复视为故障并计入重试——推理类模型偶发返回「仅含 reasoning、
        正文为空」的回复，重发即可绕开（线上实测有效）。

        Raises:
            LLMError: 重试耗尽仍未获得非空回复
        """
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature if temperature is None else temperature,
        }

        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = requests.post(
                    f"{self.base_url}/chat/completions",
                    headers=self._headers,
                    json=payload,
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"]["content"]
                if content and content.strip():
                    return content
                last_error = LLMError("LLM 返回空回复")
            except Exception as e:  # noqa: BLE001 —— 网络/HTTP/解析异常统一按瞬时故障重试
                last_error = e
            if attempt < self.max_retries:
                time.sleep(min(2.0 * attempt, _MAX_BACKOFF_SECONDS))

        raise LLMError(f"LLM 调用失败（已重试 {self.max_retries} 次）: {last_error}")


def extract_json(text: Any) -> dict[str, Any]:
    """从 LLM 回复中提取首个 JSON 对象。

    依次尝试：```json 围栏 → 整体解析 → 括号平衡扫描提取首个对象。
    平衡扫描正确处理字符串内的花括号与转义，线上验证过 19 类畸形输出。

    Raises:
        ValueError: 文本中不存在可解析的 JSON 对象
    """
    if isinstance(text, dict):
        return text

    raw = str(text or "").strip()
    if not raw:
        raise ValueError("LLM 回复为空")

    fenced = re.search(r"```json\s*([\s\S]*?)```", raw, re.IGNORECASE)
    if fenced and fenced.group(1).strip():
        return json.loads(fenced.group(1))

    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        pass

    return json.loads(_first_json_object(raw))


def _first_json_object(text: str) -> str:
    """括号平衡扫描：返回文本中首个完整的 ``{...}`` 片段。"""
    start = -1
    depth = 0
    in_string = False
    escape = False

    for idx, ch in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
        elif ch == "{":
            if depth == 0:
                start = idx
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start >= 0:
                return text[start : idx + 1]

    raise ValueError("未找到有效 JSON 对象")
