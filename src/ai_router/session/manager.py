from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from playwright.async_api import Page

from ai_router.adapters.base import ProviderAdapter
from ai_router.browser.manager import BrowserManager


CHAT_URL_RE = re.compile(r"https://gemini\.google\.com/app/[a-f0-9]+", re.I)


@dataclass
class ChatSession:
    mcp_session_id: str
    page: Page
    provider_id: str
    chat_url: str | None = None
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    message_count: int = 0


class SessionManager:
    def __init__(self) -> None:
        self._sessions: dict[str, ChatSession] = {}

    def clear_all(self) -> None:
        """Drop page handles only — chat_url and message_count survive browser relaunch."""
        self._sessions.clear()

    @staticmethod
    def normalize_chat_url(url: str) -> str | None:
        match = CHAT_URL_RE.search(url)
        return match.group(0) if match else None

    async def get_or_create(
        self,
        mcp_session_id: str,
        adapter: ProviderAdapter,
        browser: BrowserManager,
    ) -> ChatSession:
        existing = self._sessions.get(mcp_session_id)
        if existing:
            try:
                if not existing.page.is_closed():
                    existing.last_activity = time.time()
                    return existing
            except Exception:
                pass

            page = await browser.new_page()
            await self._open_chat(adapter, page, existing.chat_url)
            existing.page = page
            existing.last_activity = time.time()
            return existing

        page = await browser.new_page()
        await adapter.open_new_chat(page)
        session = ChatSession(
            mcp_session_id=mcp_session_id,
            page=page,
            provider_id=adapter.id,
        )
        self._sessions[mcp_session_id] = session
        return session

    async def _open_chat(
        self,
        adapter: ProviderAdapter,
        page: Page,
        chat_url: str | None,
    ) -> None:
        if chat_url and hasattr(adapter, "resume_chat"):
            await adapter.resume_chat(page, chat_url)
        else:
            await adapter.open_new_chat(page)

    def record_message(self, mcp_session_id: str, *, page_url: str | None = None) -> None:
        session = self._sessions.get(mcp_session_id)
        if session:
            session.message_count += 1
            session.last_activity = time.time()
            if page_url:
                chat_url = self.normalize_chat_url(page_url)
                if chat_url:
                    session.chat_url = chat_url

    def record_chat_url(self, mcp_session_id: str, page_url: str) -> None:
        session = self._sessions.get(mcp_session_id)
        if not session:
            return
        chat_url = self.normalize_chat_url(page_url)
        if chat_url:
            session.chat_url = chat_url
