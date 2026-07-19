import pytest

from ai_router.adapters.chatgpt.adapter import ChatGPTAdapter
from ai_router.adapters.claude.adapter import ClaudeAdapter
from ai_router.adapters.deepseek.adapter import DeepSeekAdapter
from ai_router.adapters.registry import ProviderRegistry
from ai_router.errors import AiRouterError
from ai_router.router.resolve import resolve_provider


class _FakeGemini:
    id = "gemini"
    name = "Gemini"
    keywords: list[str] = []
    status = "available"


def test_resolve_default_provider():
    registry = ProviderRegistry([_FakeGemini(), ChatGPTAdapter()])
    adapter, reason = resolve_provider(registry, None, default="gemini")
    assert adapter.id == "gemini"
    assert reason == "default provider"


def test_resolve_explicit_provider():
    registry = ProviderRegistry([_FakeGemini(), ChatGPTAdapter()])
    adapter, reason = resolve_provider(registry, "gemini", default="gemini")
    assert adapter.id == "gemini"
    assert reason == "explicit param"


def test_resolve_unknown_provider():
    registry = ProviderRegistry([_FakeGemini()])
    with pytest.raises(AiRouterError) as exc:
        resolve_provider(registry, "unknown", default="gemini")
    assert exc.value.code == "UNKNOWN_PROVIDER"


def test_resolve_deepseek_provider():
    registry = ProviderRegistry(
        [_FakeGemini(), ChatGPTAdapter(), ClaudeAdapter(), DeepSeekAdapter()]
    )
    adapter, reason = resolve_provider(registry, "deepseek", default="gemini")
    assert adapter.id == "deepseek"
    assert reason == "explicit param"
