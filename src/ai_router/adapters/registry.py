from __future__ import annotations

from ai_router.adapters.base import ProviderAdapter
from ai_router.adapters.chatgpt.adapter import ChatGPTAdapter
from ai_router.errors import AiRouterError


class ProviderRegistry:
    def __init__(self, adapters: list[ProviderAdapter] | None = None) -> None:
        self._adapters: dict[str, ProviderAdapter] = {}
        for adapter in adapters or []:
            self.register(adapter)

    def register(self, adapter: ProviderAdapter) -> None:
        self._adapters[adapter.id] = adapter

    def get(self, provider_id: str) -> ProviderAdapter:
        adapter = self._adapters.get(provider_id)
        if not adapter:
            raise AiRouterError("UNKNOWN_PROVIDER", f"Unknown provider: {provider_id}")
        return adapter

    def list_all(self) -> list[ProviderAdapter]:
        return list(self._adapters.values())


def build_registry() -> ProviderRegistry:
    from ai_router.adapters.claude.adapter import ClaudeAdapter
    from ai_router.adapters.gemini.adapter import GeminiAdapter

    registry = ProviderRegistry()
    registry.register(GeminiAdapter())
    registry.register(ChatGPTAdapter())
    registry.register(ClaudeAdapter())
    return registry
