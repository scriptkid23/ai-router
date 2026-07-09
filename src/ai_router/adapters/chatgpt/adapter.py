from __future__ import annotations

from playwright.async_api import Page

from ai_router.adapters.base import SessionStatus
from ai_router.errors import ProviderNotReadyError


class ChatGPTAdapter:
    id = "chatgpt"
    name = "ChatGPT"
    keywords: list[str] = ["chatgpt", "gpt", "@chatgpt"]
    status = "coming_soon"

    async def check_session(self, page: Page) -> SessionStatus:
        return SessionStatus.UNKNOWN

    async def open_new_chat(self, page: Page) -> None:
        raise ProviderNotReadyError(self.id)

    async def ask(self, page: Page, prompt: str, *, timeout_s: int) -> str:
        raise ProviderNotReadyError(self.id)
