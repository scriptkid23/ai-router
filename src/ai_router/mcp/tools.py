from __future__ import annotations

from dataclasses import dataclass

from playwright.async_api import Error as PlaywrightError

from ai_router.adapters.base import SessionStatus
from ai_router.adapters.registry import ProviderRegistry, build_registry
from ai_router.browser.manager import BrowserManager
from ai_router.config import AppConfig, load_config
from ai_router.errors import (
    AiRouterError,
    BrowserClosedError,
    NotLoggedInError,
    ProviderNotReadyError,
)
from ai_router.router.resolve import resolve_provider
from ai_router.session.manager import SessionManager


@dataclass
class AppState:
    config: AppConfig
    registry: ProviderRegistry
    browser: BrowserManager
    sessions: SessionManager


def create_app_state(config: AppConfig | None = None) -> AppState:
    cfg = config or load_config()
    sessions = SessionManager()
    browser = BrowserManager(cfg)
    return AppState(
        config=cfg,
        registry=build_registry(),
        browser=browser,
        sessions=sessions,
    )


async def handle_ask(
    state: AppState,
    *,
    prompt: str,
    provider: str | None,
    mcp_session_id: str | None,
) -> dict:
    if not mcp_session_id:
        raise AiRouterError("MISSING_SESSION", "Mcp-Session-Id header required")

    adapter, routing_reason = resolve_provider(
        state.registry, provider, default=state.config.default_provider
    )
    if adapter.status == "coming_soon":
        raise ProviderNotReadyError(adapter.id)

    async with state.browser.acquire():
        try:
            session = await state.sessions.get_or_create(mcp_session_id, adapter, state.browser)
            preserve_chat = session.message_count > 0 or session.chat_url is not None
            if hasattr(adapter, "ensure_page_ready"):
                status = await adapter.ensure_page_ready(session.page, preserve_chat=preserve_chat)
            else:
                status = await adapter.check_session(session.page)
            if status == SessionStatus.LOGGED_OUT:
                raise NotLoggedInError()
            answer = await adapter.ask(
                session.page,
                prompt,
                timeout_s=state.config.answer_timeout_s,
            )
            state.sessions.record_message(
                mcp_session_id,
                page_url=session.page.url,
            )
        except PlaywrightError as exc:
            if "closed" in str(exc).lower():
                raise BrowserClosedError() from exc
            raise AiRouterError("ADAPTER_ERROR", str(exc)) from exc

    return {
        "answer": answer,
        "provider": adapter.id,
        "routing_reason": routing_reason,
    }


async def handle_list_providers(state: AppState) -> dict:
    return {
        "providers": [
            {"id": a.id, "name": a.name, "status": a.status}
            for a in state.registry.list_all()
        ]
    }


async def handle_session_status(
    state: AppState,
    *,
    provider: str | None,
) -> dict:
    async with state.browser.acquire():
        page = await state.browser.new_page()
        targets = (
            [state.registry.get(provider)]
            if provider
            else state.registry.list_all()
        )
        result: dict[str, str] = {}
        for adapter in targets:
            if adapter.status == "coming_soon":
                result[adapter.id] = SessionStatus.UNKNOWN.value
                continue
            status = await adapter.check_session(page)
            result[adapter.id] = status.value
        return result
