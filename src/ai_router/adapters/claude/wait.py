from __future__ import annotations

from playwright.async_api import Page

from ai_router.adapters.claude.selectors import (
    RATE_LIMIT_MARKERS,
    SEL_ASSISTANT_MESSAGE,
    SEL_ASSISTANT_TEXT,
    SEL_ASSISTANT_TURN,
    SEL_PROMPT_INPUT,
    SEL_STOP_BUTTON,
    SEL_SUBMIT_BUTTON,
)


def is_rate_limited(text: str) -> bool:
    lower = text.lower()
    return any(marker in lower for marker in RATE_LIMIT_MARKERS)


async def is_stop_visible(page: Page) -> bool:
    """True while Claude is still generating."""
    if await page.locator(SEL_STOP_BUTTON).count() > 0:
        return True
    return False


async def read_response_snapshot(page: Page) -> tuple[int, str]:
    """Return assistant turn count and text of the latest assistant message."""
    last_msg = page.locator(SEL_ASSISTANT_MESSAGE)
    if await last_msg.count():
        inner = last_msg.locator(SEL_ASSISTANT_TEXT)
        if await inner.count():
            text = (await inner.first.inner_text()).strip()
        else:
            text = (await last_msg.first.inner_text()).strip()
        return 1, text

    turns = page.locator(SEL_ASSISTANT_TURN)
    count = await turns.count()
    if not count:
        return 0, ""
    last = turns.nth(count - 1)
    inner = last.locator(SEL_ASSISTANT_TEXT)
    if await inner.count():
        text = (await inner.first.inner_text()).strip()
    else:
        text = (await last.inner_text()).strip()
    return count, text


async def submit_ready(page: Page) -> bool:
    """True when the composer send button exists and is enabled."""
    if await page.locator(SEL_PROMPT_INPUT).count() == 0:
        return False
    submit = page.locator(SEL_SUBMIT_BUTTON).first
    if await submit.count() == 0:
        return False
    return not await submit.is_disabled()
