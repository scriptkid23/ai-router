from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from ai_router.adapters.gemini.selectors import (
    RATE_LIMIT_MARKERS,
    SEL_GENERATING,
    SEL_RESPONSE_BLOCK,
    STREAM_GENERATE_RE,
)
from ai_router.errors import RateLimitedError, TimeoutError_

if TYPE_CHECKING:
    from playwright.async_api import Page


def braces_balanced(text: str) -> bool:
    if "{" not in text:
        return True
    return text.count("{") == text.count("}")


def is_rate_limited(text: str) -> bool:
    lower = text.lower()
    return any(marker in lower for marker in RATE_LIMIT_MARKERS)


async def wait_for_stream(page: Page, timeout_s: float) -> bool:
    loop = asyncio.get_running_loop()
    done = loop.create_future()

    def on_finished(request) -> None:
        if not done.done() and STREAM_GENERATE_RE.search(request.url):
            done.set_result(True)

    page.on("requestfinished", on_finished)
    try:
        await asyncio.wait_for(done, timeout=timeout_s)
        return True
    except asyncio.TimeoutError:
        return False
    finally:
        page.remove_listener("requestfinished", on_finished)


async def wait_for_answer_dom(
    page: Page,
    *,
    before_count: int,
    timeout_s: float,
    poll_interval_s: float = 0.5,
    stable_polls: int = 4,
) -> str:
    deadline = time.monotonic() + timeout_s
    last_text = ""
    stable_streak = 0

    while time.monotonic() < deadline:
        blocks = page.locator(SEL_RESPONSE_BLOCK)
        count = await blocks.count()
        generating = await page.locator(SEL_GENERATING).count()

        if count > before_count and generating == 0:
            text = (await blocks.nth(count - 1).inner_text()).strip()
            if text and braces_balanced(text):
                if text == last_text:
                    stable_streak += 1
                    if stable_streak >= stable_polls:
                        if is_rate_limited(text):
                            raise RateLimitedError(text[:200])
                        return text
                else:
                    last_text = text
                    stable_streak = 1

        await asyncio.sleep(poll_interval_s)

    raise TimeoutError_("DOM polling timed out waiting for stable answer")
