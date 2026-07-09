from __future__ import annotations

from playwright.async_api import Page

from ai_router.adapters.gemini.selectors import (
    RATE_LIMIT_MARKERS,
    SEL_RESPONSE_BLOCK,
    SEL_RESPONSE_INNER,
    SEL_RESPONSE_TEXT,
)


def braces_balanced(text: str) -> bool:
    if "{" not in text:
        return True
    return text.count("{") == text.count("}")


def is_rate_limited(text: str) -> bool:
    lower = text.lower()
    return any(marker in lower for marker in RATE_LIMIT_MARKERS)


async def read_response_snapshot(page: Page) -> tuple[int, str]:
    """Return assistant turn count and text from the latest response block."""
    blocks = page.locator(SEL_RESPONSE_BLOCK)
    count = await blocks.count()
    if count:
        last = blocks.nth(count - 1)
        inner = last.locator(SEL_RESPONSE_INNER)
        if await inner.count():
            text = (await inner.first.inner_text()).strip()
        else:
            text = (await last.inner_text()).strip()
        return count, text

    texts = page.locator(SEL_RESPONSE_TEXT)
    count = await texts.count()
    if count:
        return count, (await texts.nth(count - 1).inner_text()).strip()

    return 0, ""
