from __future__ import annotations

from playwright.async_api import Page

from ai_router.adapters.kimi.selectors import (
    CHALLENGE_MARKERS,
    KIMI_NEW_CHAT_URL,
    RATE_LIMIT_MARKERS,
    SEL_ASSISTANT_MAIN,
    SEL_ASSISTANT_TEXT,
    SEL_CHALLENGE,
    SEL_NEW_CHAT,
    SEL_PROMPT_INPUT,
    SEL_STOP_BUTTON,
)

_STRIP_UI_JS = """(el) => {
    const clone = el.cloneNode(true);
    clone.querySelectorAll(
        '.table-actions, .icon-button, .kimi-tooltip'
    ).forEach(n => n.remove());
    return clone.innerText.trim();
}"""


def is_rate_limited(text: str) -> bool:
    lower = text.strip().lower()
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


async def is_stop_visible(page: Page) -> bool:
    return await page.locator(SEL_STOP_BUTTON).count() > 0


async def submit_ready(page: Page) -> bool:
    loc = page.locator(SEL_PROMPT_INPUT).first
    if await loc.count() == 0:
        return False
    try:
        return await loc.is_visible() and await loc.is_editable()
    except Exception:
        return False


async def read_response_snapshot(page: Page) -> tuple[int, str]:
    segments = page.locator(SEL_ASSISTANT_MAIN)
    count = await segments.count()
    if count == 0:
        return 0, ""
    last = segments.nth(count - 1)
    markdown = last.locator(SEL_ASSISTANT_TEXT).first
    if await markdown.count() == 0:
        return count, ""
    try:
        text = await markdown.evaluate(_STRIP_UI_JS)
    except Exception:
        text = ""
    return count, text or ""


async def ensure_new_chat(page: Page) -> None:
    await page.goto(KIMI_NEW_CHAT_URL, wait_until="domcontentloaded")
    count, _ = await read_response_snapshot(page)
    if count == 0:
        return
    btn = page.locator(SEL_NEW_CHAT).first
    if await btn.count() > 0:
        await btn.click()
        await page.wait_for_timeout(500)
