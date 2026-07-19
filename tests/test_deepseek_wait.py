from unittest.mock import AsyncMock, MagicMock

import pytest

from ai_router.adapters.deepseek.wait import is_generating_body_visible, is_rate_limited


def test_rate_limit_english():
    assert is_rate_limited("Rate limit exceeded, try again later") is True


def test_rate_limit_negative():
    assert is_rate_limited("The answer is 42") is False


def test_rate_limit_long_answer_mentioning_topic_is_not_error():
    text = "Rủi ro 3: API rate limit khi peak traffic. Giải pháp: token bucket, CDN."
    assert is_rate_limited(text * 20) is False


@pytest.mark.asyncio
async def test_generating_body_markers_detect_search_phase():
    page = MagicMock()
    page.locator.return_value.inner_text = AsyncMock(
        return_value="Searching the web for restaurants in Hanoi"
    )
    assert await is_generating_body_visible(page) is True


@pytest.mark.asyncio
async def test_generating_body_markers_ignore_search_toggle_label():
    page = MagicMock()
    page.locator.return_value.inner_text = AsyncMock(
        return_value="Start chatting with Instant\nSearch\nDeepThink"
    )
    assert await is_generating_body_visible(page) is False
