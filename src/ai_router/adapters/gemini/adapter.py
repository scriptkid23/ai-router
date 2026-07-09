from __future__ import annotations

import asyncio

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from ai_router.adapters.base import SessionStatus
from ai_router.adapters.gemini.selectors import (
    GEMINI_URL,
    SEL_PROMPT_INPUT,
    SEL_RESPONSE_BLOCK,
    SEL_SIGN_IN,
)
from ai_router.adapters.gemini.wait import (
    is_rate_limited,
    wait_for_answer_dom,
    wait_for_stream,
)
from ai_router.errors import AiRouterError, RateLimitedError


class GeminiAdapter:
    id = "gemini"
    name = "Gemini"
    keywords = ["gemini", "@gemini", "google gemini"]
    status = "available"

    async def check_session(self, page: Page) -> SessionStatus:
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

    async def ask(self, page: Page, prompt: str, *, timeout_s: int) -> str:
        last_err: Exception | None = None
        for attempt in range(2):
            try:
                return await self._ask_once(page, prompt, timeout_s=timeout_s)
            except RuntimeError as exc:
                last_err = exc
                if attempt == 0:
                    await asyncio.sleep(2)
                    await page.reload(wait_until="domcontentloaded")
                    continue
                raise AiRouterError("ADAPTER_ERROR", str(exc)) from exc
        raise AiRouterError("ADAPTER_ERROR", str(last_err or "unknown"))

    async def _ask_once(self, page: Page, prompt: str, *, timeout_s: int) -> str:
        box = page.locator(SEL_PROMPT_INPUT).first
        await box.wait_for(state="visible", timeout=15_000)
        await box.click()
        await page.keyboard.insert_text(prompt)

        before_count = await page.locator(SEL_RESPONSE_BLOCK).count()

        stream_task = asyncio.create_task(wait_for_stream(page, timeout_s))
        await page.keyboard.press("Enter")
        await stream_task

        answer = await wait_for_answer_dom(
            page,
            before_count=before_count,
            timeout_s=timeout_s,
        )

        if is_rate_limited(answer):
            raise RateLimitedError(answer[:200])

        return answer
