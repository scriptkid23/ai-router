from __future__ import annotations

from playwright.async_api import Page

from ai_router.adapters.deepseek.selectors import (
    CHALLENGE_MARKERS,
    RATE_LIMIT_MARKERS,
    SEL_ASSISTANT_MAIN,
    SEL_CHALLENGE,
    SEL_NEW_CHAT,
    SEL_PROMPT_INPUT,
    SEL_STOP_BUTTON,
    SEL_SUBMIT_BUTTON,
)


def is_rate_limited(text: str) -> bool:
    lower = text.lower()
    return any(marker in lower for marker in RATE_LIMIT_MARKERS)


async def is_challenge_visible(page: Page) -> bool:
    if await page.locator(SEL_CHALLENGE).count() > 0:
        return True
    try:
        body = (await page.locator("body").inner_text()).lower()
    except Exception:
        return False
    return any(marker in body for marker in CHALLENGE_MARKERS)


async def is_stop_visible(page: Page) -> bool:
    return await page.locator(SEL_STOP_BUTTON).count() > 0


async def read_response_snapshot(page: Page) -> tuple[int, str]:
    """Return assistant main-content count and text of the latest response."""
    blocks = page.locator(SEL_ASSISTANT_MAIN)
    count = await blocks.count()
    if not count:
        return 0, ""
    last = blocks.nth(count - 1)
    inner = last.locator(".ds-markdown")
    if await inner.count():
        text = (await inner.first.inner_text()).strip()
    else:
        text = (await last.inner_text()).strip()
    return count, text


async def submit_ready(page: Page) -> bool:
    if await page.locator(SEL_PROMPT_INPUT).count() == 0:
        return False
    submit = page.locator(SEL_SUBMIT_BUTTON).first
    if await submit.count() == 0:
        return False
    return not await submit.is_disabled()


async def ensure_new_chat(page: Page) -> None:
    """Click New Chat and wait until no assistant messages remain."""
    btn = page.locator(SEL_NEW_CHAT).first
    if await btn.count() > 0:
        await btn.click()
    count, _ = await read_response_snapshot(page)
    if count == 0:
        return
    # Fallback: hard navigation if button did not clear history
    from ai_router.adapters.deepseek.selectors import DEEPSEEK_URL

    await page.goto(DEEPSEEK_URL, wait_until="domcontentloaded")
    for _ in range(20):
        count, _ = await read_response_snapshot(page)
        if count == 0:
            return
        await page.wait_for_timeout(250)
