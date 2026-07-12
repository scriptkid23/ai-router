from __future__ import annotations

import json
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
from ai_router.browser.profile import StreamDone

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


def extract_stream_answer(body: str) -> str | None:
    """Best-effort answer text from a finished StreamGenerate body."""
    if not is_stream_end(body):
        return None
    candidates: list[str] = []
    for match in re.finditer(r'"((?:[^"\\]|\\.)*)"', body):
        raw = match.group(1)
        if len(raw) < 20:
            continue
        try:
            text = json.loads(f'"{raw}"')
        except json.JSONDecodeError:
            text = raw.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")
        if not isinstance(text, str):
            continue
        stripped = text.strip()
        if len(stripped) < 20:
            continue
        if stripped.startswith("http") or stripped.startswith("rc_"):
            continue
        if stripped[0] in "[{":
            continue
        if re.fullmatch(r"[\w\-.:]+", stripped):
            continue
        candidates.append(stripped)
    if not candidates:
        return None
    return max(candidates, key=len)


def parse_stream_done(status: int, body: str) -> StreamDone:
    """Gemini StreamGenerate: done when the end-of-turn ["e", ...] tag appears."""
    if is_stream_end(body):
        return StreamDone(
            done=True,
            ok=True,
            answer_text=extract_stream_answer(body),
        )
    return StreamDone(done=False, ok=False)


async def send_button_ready(page: Page) -> bool:
    """True when Gemini's Send control is present and enabled."""
    container = page.locator(SEL_SEND_CONTAINER).last
    if await container.count() == 0:
        return False
    wrapper = container.locator("gem-icon-button.send-button.submit").first
    if await wrapper.count() > 0:
        return await wrapper.get_attribute("aria-disabled") != "true"
    submit = container.locator('button[aria-label="Send message"]').first
    if await submit.count() == 0:
        return False
    return not await submit.is_disabled()


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
