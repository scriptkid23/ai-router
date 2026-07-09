from __future__ import annotations

import time
from dataclasses import dataclass, field

from playwright.async_api import BrowserContext, Page

from ai_router.adapters.base import ProviderAdapter
from ai_router.browser.manager import BrowserManager


@dataclass
class ChatSession:
    mcp_session_id: str
    page: Page
    provider_id: str
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    message_count: int = 0


class SessionManager:
    def __init__(self, browser: BrowserManager) -> None:
        self._browser = browser
        self._sessions: dict[str, ChatSession] = {}

    async def get_or_create(
        self,
        mcp_session_id: str,
        adapter: ProviderAdapter,
        ctx: BrowserContext,
    ) -> ChatSession:
        existing = self._sessions.get(mcp_session_id)
        if existing:
            existing.last_activity = time.time()
            return existing

        page = await ctx.new_page()
        await adapter.open_new_chat(page)
        session = ChatSession(
            mcp_session_id=mcp_session_id,
            page=page,
            provider_id=adapter.id,
        )
        self._sessions[mcp_session_id] = session
        return session

    def record_message(self, mcp_session_id: str) -> None:
        session = self._sessions.get(mcp_session_id)
        if session:
            session.message_count += 1
            session.last_activity = time.time()
