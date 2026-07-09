from __future__ import annotations

import re
from playwright.async_api import Page

from ai_router.adapters.gemini.selectors import (
    RATE_LIMIT_MARKERS,
    SEL_GENERATING,
    SEL_RESPONSE_BLOCK,
    SEL_RESPONSE_INNER,
    SEL_RESPONSE_TEXT,
    SEL_SEND_CONTAINER,
)

# Gemini StreamGenerate end-of-turn marker: ["e", ...]
STREAM_END_RE = re.compile(r'\[\s*"e"\s*,', re.I)


def braces_balanced(text: str) -> bool:
    if "{" not in text:
        return True
    return text.count("{") == text.count("}")


def is_rate_limited(text: str) -> bool:
    lower = text.lower()
    return any(marker in lower for marker in RATE_LIMIT_MARKERS)


def is_stream_end(body: str) -> bool:
    """True when StreamGenerate payload contains Gemini's end-of-turn tag."""
    return bool(STREAM_END_RE.search(body))


async def is_stop_visible(page: Page) -> bool:
    """True while Gemini is still generating (Stop/Pause control visible)."""
    if await page.locator(SEL_GENERATING).count() > 0:
        return True
    try:
        return bool(
            await page.evaluate(
                """() => {
                    const sel = [
                        'div.send-button-container.visible',
                        'div[data-test-id="send-button-container"].visible',
                    ].join(',');
                    const container = document.querySelector(sel);
                    if (!container) return false;
                    if (container.querySelector('gem-icon-button.stop')) return true;
                    const btn = container.querySelector('button');
                    if (!btn) return false;
                    const label = (btn.getAttribute('aria-label') || '').toLowerCase();
                    return label.includes('stop') || label.includes('dừng');
                }"""
            )
        )
    except Exception:
        container = page.locator(SEL_SEND_CONTAINER).last
        if await container.count() == 0:
            return False
        return await container.locator("gem-icon-button.stop").count() > 0


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
