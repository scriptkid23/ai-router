from __future__ import annotations

from playwright.async_api import Page

from ai_router.adapters.deepseek.selectors import (
    ACTIVE_GENERATING_MARKERS,
    CHALLENGE_MARKERS,
    RATE_LIMIT_MARKERS,
    SEL_ASSISTANT_MAIN,
    SEL_CHALLENGE,
    SEL_NEW_CHAT,
    SEL_PROMPT_INPUT,
    SEL_SEARCH_INDICATOR,
    SEL_SIDEBAR_CHAT,
    SEL_STOP_BUTTON,
    SEL_SUBMIT_BUTTON,
)


def is_rate_limited(text: str) -> bool:
    lower = text.strip().lower()
    # Long answers often discuss rate limiting as a topic — not a provider error.
    if len(lower) > 400:
        return False
    return any(marker in lower for marker in RATE_LIMIT_MARKERS)


async def is_challenge_visible(page: Page) -> bool:
    if await page.locator(SEL_CHALLENGE).count() > 0:
        return True
    try:
        body = (await page.locator("body").inner_text()).lower()
    except Exception:
        return False
    return any(marker in body for marker in CHALLENGE_MARKERS)


async def _assistant_has_active_generation(page: Page) -> bool:
    blocks = page.locator(SEL_ASSISTANT_MAIN)
    count = await blocks.count()
    if count == 0:
        return False
    last = blocks.nth(count - 1)
    if await last.locator(SEL_SEARCH_INDICATOR).count() > 0:
        return True
    try:
        text = (await last.inner_text()).lower()
    except Exception:
        return False
    return any(marker in text for marker in ACTIVE_GENERATING_MARKERS)


async def is_stop_visible(page: Page) -> bool:
    """True while the model is actively generating (not composer toggles)."""
    if await page.locator(SEL_STOP_BUTTON).count() > 0:
        return True
    return await _assistant_has_active_generation(page)


async def is_generating_body_visible(page: Page) -> bool:
    try:
        body = (await page.locator("body").inner_text()).lower()
    except Exception:
        return False
    return any(marker in body for marker in ACTIVE_GENERATING_MARKERS)


async def is_generating_started(page: Page) -> bool:
    """Extra generation signals for Search/DeepThink before Stop/SSE appear."""
    if await _assistant_has_active_generation(page):
        return True
    return await is_generating_body_visible(page)


async def ensure_active_chat_view(page: Page) -> None:
    """Open the latest sidebar chat when submit lands on the empty landing view."""
    count, _ = await read_response_snapshot(page)
    if count > 0:
        return
    for _ in range(40):
        items = page.locator(SEL_SIDEBAR_CHAT)
        if await items.count() > 0:
            await items.first.click()
            for _ in range(20):
                count, _ = await read_response_snapshot(page)
                if count > 0:
                    return
                await page.wait_for_timeout(250)
            return
        await page.wait_for_timeout(250)


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
