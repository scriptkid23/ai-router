import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from ai_router.adapters.gemini.selectors import SEL_GENERATING, SEL_PROMPT_INPUT
from ai_router.adapters.gemini.wait import clear_prompt, type_prompt
from ai_router.errors import AiRouterError


def test_prompt_selector_targets_quill_editor_not_clipboard():
    assert 'role="textbox"' in SEL_PROMPT_INPUT
    assert "textarea-inner" in SEL_PROMPT_INPUT
    assert "ql-clipboard" not in SEL_PROMPT_INPUT


def test_generating_selector_scoped_to_send_container_only():
    assert "button[aria-label" not in SEL_GENERATING.split(",")[0]
    assert "send-button-container" in SEL_GENERATING


def test_clear_prompt_uses_page_evaluate():
    page = MagicMock()
    page.wait_for_selector = AsyncMock()
    page.evaluate = AsyncMock(return_value={"ok": True})

    asyncio.run(clear_prompt(page))

    page.wait_for_selector.assert_awaited_once()
    page.evaluate.assert_awaited_once()
    assert "execCommand" in page.evaluate.await_args.args[0]


def test_clear_prompt_raises_when_editor_missing():
    page = MagicMock()
    page.wait_for_selector = AsyncMock()
    page.evaluate = AsyncMock(return_value={"ok": False, "reason": "no_editor"})

    with pytest.raises(AiRouterError, match="prompt input not found"):
        asyncio.run(clear_prompt(page))


def test_type_prompt_inserts_text_via_page_evaluate():
    page = MagicMock()
    page.wait_for_selector = AsyncMock()
    page.evaluate = AsyncMock(return_value={"ok": True, "text": "1+1"})

    asyncio.run(type_prompt(page, "1+1"))

    page.evaluate.assert_awaited_once()
    js, text = page.evaluate.await_args.args
    assert "insertText" in js
    assert text == "1+1"


def test_type_prompt_raises_when_text_not_written():
    page = MagicMock()
    page.wait_for_selector = AsyncMock()
    page.evaluate = AsyncMock(return_value={"ok": False, "text": "", "reason": "empty"})

    with pytest.raises(AiRouterError, match="Failed to type"):
        asyncio.run(type_prompt(page, "1+1"))
