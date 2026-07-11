from __future__ import annotations

import re

from ai_router.browser.commands import Command, CommandExecutor
from ai_router.browser.profile import ProviderProfile, ProviderSelectors, StreamDone
from ai_router.browser.state import StateReducer


class FakePage:
    def __init__(self) -> None:
        self.gotos: list[str] = []

    async def goto(self, url: str, wait_until: str = "domcontentloaded") -> None:
        self.gotos.append(url)


def make_profile(snapshots: list[tuple[int, str]]) -> ProviderProfile:
    async def read_response_snapshot(page) -> tuple[int, str]:
        return snapshots.pop(0) if len(snapshots) > 1 else snapshots[0]

    async def is_stop_visible(page) -> bool:
        return False

    async def submit_ready(page) -> bool:
        return True

    return ProviderProfile(
        provider_id="fake",
        stream_url_re=re.compile("/stream"),
        parse_stream_done=lambda status, body: StreamDone(done=False, ok=True),
        is_stop_visible=is_stop_visible,
        read_response_snapshot=read_response_snapshot,
        is_rate_limited=lambda text: False,
        submit_ready=submit_ready,
        planner=None,
        selectors=ProviderSelectors(prompt_input="#in", submit_button="#btn"),
        error_markers=(),
        recoverable_codes=(),
    )


async def test_goto_rebases_before_count():
    # The old chat shows 2 responses when run() starts; after the goto the
    # fresh chat shows 0. The answer (count 1) must satisfy new_response
    # against the POST-goto baseline — with the stale baseline (2) the
    # answer would never be "ready" and wait_answer would time out.
    page = FakePage()
    profile = make_profile(snapshots=[(2, "old"), (0, ""), (1, "answer")])
    reducer = StateReducer(
        page_id="t",
        stream_url_res=[profile.stream_url_re],
        idle_streak_required=1,
        generating_streak_required=1,
        answer_stable_ticks=1,
        stream_quiet_s=0.0,
        error_markers=(),
    )
    # a finished turn in the fresh chat: generating seen, then stable answer
    reducer.apply_dom_tick(generating=True, response_count=0, response_text="", error_text=None)
    reducer.apply_dom_tick(generating=False, response_count=1, response_text="answer", error_text=None)
    reducer.apply_dom_tick(generating=False, response_count=1, response_text="answer", error_text=None)
    ex = CommandExecutor(
        page,
        reducer,
        profile=profile,
        job_id="j",
        page_id="t",
        answer_timeout_s=2.0,
        idle_streak_required=1,
    )
    answer = await ex.run([Command("goto", {"url": "https://x/home"}), Command("wait_answer")])
    assert answer == "answer"
    assert page.gotos == ["https://x/home"]
