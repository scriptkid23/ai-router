from __future__ import annotations

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from ai_router.adapters.base import SessionStatus
from ai_router.adapters.gemini.selectors import (
    GEMINI_URL,
    SEL_PROMPT_INPUT,
    SEL_SIGN_IN,
)


class GeminiAdapter:
    id = "gemini"
    name = "Gemini"
    keywords = ["gemini", "@gemini", "google gemini"]
    status = "available"

    async def check_session(self, page: Page) -> SessionStatus:
        return await self.ensure_page_ready(page)

    async def ensure_page_ready(self, page: Page, *, preserve_chat: bool = False) -> SessionStatus:
        """Verify login without leaving an in-progress chat URL."""
        if preserve_chat and "gemini.google.com" in page.url:
            try:
                await page.wait_for_selector(SEL_PROMPT_INPUT, timeout=5_000)
                return SessionStatus.LOGGED_IN
            except PlaywrightTimeout:
                pass

        await page.goto(GEMINI_URL, wait_until="domcontentloaded")
        try:
            await page.wait_for_selector(SEL_PROMPT_INPUT, timeout=15_000)
            return SessionStatus.LOGGED_IN
        except PlaywrightTimeout:
            if await page.locator(SEL_SIGN_IN).count() > 0:
                return SessionStatus.LOGGED_OUT
            return SessionStatus.UNKNOWN

    async def open_new_chat(self, page: Page) -> None:
        await page.goto(GEMINI_URL, wait_until="domcontentloaded")

    async def resume_chat(self, page: Page, chat_url: str) -> None:
        await page.goto(chat_url, wait_until="domcontentloaded")
        await page.wait_for_selector(SEL_PROMPT_INPUT, timeout=15_000)
