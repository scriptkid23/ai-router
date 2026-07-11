from __future__ import annotations

from ai_router.adapters.base import ProviderAdapter
from ai_router.adapters.registry import ProviderRegistry


def resolve_provider(
    registry: ProviderRegistry,
    provider: str | None,
    *,
    default: str,
) -> tuple[ProviderAdapter, str]:
    if provider:
        return registry.get(provider), "explicit param"
    return registry.get(default), "default provider"
