from __future__ import annotations

import asyncio
import time

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from ai_router.adapters.base import SessionStatus
from ai_router.adapters.gemini.selectors import (
    GEMINI_URL,
    SEL_GENERATING,
    SEL_PROMPT_INPUT,
    SEL_RESPONSE_BLOCK,
    SEL_SIGN_IN,
    SEL_SUBMIT_BUTTON,
)
from ai_router.adapters.gemini.wait import (
    is_rate_limited,
    wait_for_answer_dom,
    wait_for_stream,
)
from ai_router.errors import AiRouterError, RateLimitedError, TimeoutError_


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
        await self._wait_until_idle(page, timeout_s=min(timeout_s, 120))

        box = page.locator(SEL_PROMPT_INPUT).first
        await box.wait_for(state="visible", timeout=15_000)
        await box.click()
        await page.keyboard.press("Control+A")
        await page.keyboard.press("Backspace")
        await page.keyboard.insert_text(prompt)

        before_count = await page.locator(SEL_RESPONSE_BLOCK).count()

        stream_task = asyncio.create_task(
            wait_for_stream(page, min(timeout_s, 30)),
        )
        await self._submit_prompt(page, box)
        await stream_task

        answer = await wait_for_answer_dom(
            page,
            before_count=before_count,
            timeout_s=timeout_s,
        )

        if is_rate_limited(answer):
            raise RateLimitedError(answer[:200])

        await self._wait_until_idle(page, timeout_s=30)
        return answer

    async def _wait_until_idle(self, page: Page, *, timeout_s: float) -> None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if await page.locator(SEL_GENERATING).count() == 0:
                return
            await asyncio.sleep(0.5)
        raise TimeoutError_("Gemini is still generating a response")

    async def _submit_prompt(self, page: Page, box) -> None:
        """Enter once; click Send only if generation did not start. Never press Enter twice."""
        await box.focus()
        await page.keyboard.press("Enter")

        if await self._poll_generating(page, polls=10):
            return

        submit = page.locator(SEL_SUBMIT_BUTTON).last
        try:
            if await submit.count() > 0 and await submit.is_visible() and await submit.is_enabled():
                await submit.click()
        except PlaywrightTimeout:
            pass

        if await self._poll_generating(page, polls=10):
            return

        raise AiRouterError(
            "SUBMIT_FAILED",
            "Prompt was typed but Gemini did not start generating. "
            "Check SEL_GENERATING / SEL_SUBMIT_BUTTON selectors.",
        )

    async def _poll_generating(self, page: Page, *, polls: int) -> bool:
        for _ in range(polls):
            if await page.locator(SEL_GENERATING).count() > 0:
                return True
            await asyncio.sleep(0.5)
        return False
