"""LLM 客户端测试：JSON 提取与重试语义。"""

import pytest

from src.llm import LLMClient, LLMError, extract_json


class TestExtractJson:
    def test_bare_json(self):
        assert extract_json('{"action": "HOLD"}') == {"action": "HOLD"}

    def test_fenced_json(self):
        text = '前置说明\n```json\n{"action": "BUY", "confidence": 0.8}\n```\n后置'
        assert extract_json(text)["action"] == "BUY"

    def test_embedded_json(self):
        text = '我认为应该观望。{"action": "HOLD", "reason": "含{括号}的字符串"}完'
        result = extract_json(text)
        assert result["action"] == "HOLD"
        assert result["reason"] == "含{括号}的字符串"

    def test_nested_object(self):
        text = 'x {"a": {"b": {"c": 1}}} y'
        assert extract_json(text) == {"a": {"b": {"c": 1}}}

    def test_dict_passthrough(self):
        assert extract_json({"a": 1}) == {"a": 1}

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            extract_json("")

    def test_no_json_raises(self):
        with pytest.raises(ValueError):
            extract_json("纯文本，没有任何对象")


class _FakeResponse:
    def __init__(self, content: str | None, status_error: Exception | None = None):
        self._content = content
        self._status_error = status_error

    def raise_for_status(self):
        if self._status_error:
            raise self._status_error

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr("src.llm.time.sleep", lambda _s: None)


def _make_client() -> LLMClient:
    return LLMClient(base_url="https://api.example.com/v1", api_key="k", model="m", max_retries=3)


def test_chat_success(monkeypatch, no_sleep):
    monkeypatch.setattr("src.llm.requests.post", lambda *a, **kw: _FakeResponse("回复内容"))
    assert _make_client().chat("sys", "user") == "回复内容"


def test_chat_retries_on_empty_reply(monkeypatch, no_sleep):
    replies = [_FakeResponse(""), _FakeResponse("第二次成功")]
    monkeypatch.setattr("src.llm.requests.post", lambda *a, **kw: replies.pop(0))
    assert _make_client().chat("sys", "user") == "第二次成功"


def test_chat_retries_on_network_error(monkeypatch, no_sleep):
    calls = {"n": 0}

    def flaky(*a, **kw):
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("网络抖动")
        return _FakeResponse("恢复")

    monkeypatch.setattr("src.llm.requests.post", flaky)
    assert _make_client().chat("sys", "user") == "恢复"
    assert calls["n"] == 3


def test_chat_exhausted_raises_llm_error(monkeypatch, no_sleep):
    monkeypatch.setattr(
        "src.llm.requests.post",
        lambda *a, **kw: (_ for _ in ()).throw(ConnectionError("持续故障")),
    )
    with pytest.raises(LLMError, match="持续故障"):
        _make_client().chat("sys", "user")


def test_chat_temperature_override(monkeypatch, no_sleep):
    captured = {}

    def capture(url, headers=None, json=None, timeout=None):
        captured.update(json)
        return _FakeResponse("ok")

    monkeypatch.setattr("src.llm.requests.post", capture)
    _make_client().chat("sys", "user", temperature=0.1)
    assert captured["temperature"] == 0.1
    assert captured["messages"][0]["role"] == "system"
