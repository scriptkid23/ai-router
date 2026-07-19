import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
import re

from ai_router.browser.commands import Command, CommandExecutor
from ai_router.browser.profile import ProviderProfile, ProviderSelectors, StreamDone
from ai_router.browser.state import StateReducer
from ai_router.errors import AiRouterError


def _make_reducer(profile: ProviderProfile) -> StateReducer:
    return StateReducer(
        page_id="p1",
        stream_url_res=[profile.stream_url_re],
        idle_streak_required=1,
        generating_streak_required=1,
        answer_stable_ticks=1,
        stream_quiet_s=0.1,
        error_markers=(),
    )


@pytest.mark.asyncio
async def test_after_submit_hook_runs_on_successful_submit():
    called = asyncio.Event()
    page = MagicMock()
    page.locator.return_value.first = MagicMock()
    page.locator.return_value.first.click = AsyncMock()
    page.locator.return_value.first.inner_text = AsyncMock(return_value="")
    page.keyboard = MagicMock()
    page.keyboard.press = AsyncMock()
    page.keyboard.insert_text = AsyncMock()

    async def after_submit(p):
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
        after_submit=after_submit,
    )
    reducer = _make_reducer(profile)
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
    executor._last_prompt_len = 12
    await executor._submit()
    assert called.is_set()


@pytest.mark.asyncio
async def test_wait_generating_accepts_new_dom_response():
    page = MagicMock()
    profile = ProviderProfile(
        provider_id="fake",
        stream_url_re=re.compile(r"/completion"),
        parse_stream_done=lambda s, b: StreamDone(done=False, ok=False),
        is_stop_visible=AsyncMock(return_value=False),
        read_response_snapshot=AsyncMock(return_value=(1, "partial")),
        is_rate_limited=lambda t: False,
        submit_ready=AsyncMock(return_value=True),
        planner=MagicMock(),
        selectors=ProviderSelectors(prompt_input="#in", submit_button="#btn"),
        error_markers=(),
        recoverable_codes=(),
    )
    reducer = _make_reducer(profile)
    reducer.mark_submitting()

    executor = CommandExecutor(
        page,
        reducer,
        profile=profile,
        job_id="j1",
        page_id="p1",
        answer_timeout_s=30.0,
        idle_streak_required=1,
    )
    executor._response_count_at_submit = 0
    await executor._wait_generating()


@pytest.mark.asyncio
async def test_wait_generating_uses_profile_timeout():
    page = MagicMock()
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
        generating_start_timeout_s=0.05,
    )
    reducer = _make_reducer(profile)
    reducer.mark_submitting()

    executor = CommandExecutor(
        page,
        reducer,
        profile=profile,
        job_id="j1",
        page_id="p1",
        answer_timeout_s=30.0,
        idle_streak_required=1,
    )
    executor._response_count_at_submit = 0
    page.locator.return_value.first.inner_text = AsyncMock(return_value="")
    with pytest.raises(AiRouterError, match="Generation not started"):
        await executor._wait_generating()
