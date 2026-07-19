import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from ai_router.browser.commands import Command, CommandExecutor
from ai_router.browser.profile import ProviderProfile, ProviderSelectors, StreamDone
from ai_router.browser.state import StateReducer
from ai_router.errors import AiRouterError
import re


@pytest.mark.asyncio
async def test_new_chat_calls_profile_hook():
    called = asyncio.Event()
    page = MagicMock()

    async def on_new_chat(p):
        assert p is page
        called.set()

    profile = ProviderProfile(
        provider_id="fake",
        stream_url_re=re.compile(r"/completion"),
        parse_stream_done=lambda s, b: StreamDone(done=False, ok=False),
        is_stop_visible=AsyncMock(return_value=False),
        read_response_snapshot=AsyncMock(return_value=(0, "")),
        is_rate_limited=lambda t: False,
        submit_ready=AsyncMock(return_value=True),
        planner=MagicMock(),
        selectors=ProviderSelectors(prompt_input="#in", submit_button="#btn"),
        error_markers=(),
        recoverable_codes=(),
        on_new_chat=on_new_chat,
    )
    reducer = StateReducer(
        page_id="p1",
        stream_url_res=[profile.stream_url_re],
        idle_streak_required=1,
        generating_streak_required=1,
        answer_stable_ticks=1,
        stream_quiet_s=0.1,
        error_markers=(),
    )
    reducer.state.phase = "idle"
    reducer.state.idle_streak = 99

    executor = CommandExecutor(
        page,
        reducer,
        profile=profile,
        job_id="j1",
        page_id="p1",
        answer_timeout_s=30.0,
        idle_streak_required=1,
    )
    with pytest.raises(AiRouterError, match="missing wait_answer"):
        await executor.run([Command("new_chat")])
    assert called.is_set()
