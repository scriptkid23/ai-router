from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ai_router.adapters.base import SessionStatus
from ai_router.browser.page_router import PageRouter
from ai_router.config import AppConfig
from ai_router.errors import AiRouterError
from ai_router.mcp import tools
from ai_router.mcp.tools import create_app_state, handle_ask_multi


def make_config() -> AppConfig:
    return AppConfig(
        profile_dir=Path("profile"),
        default_provider="gemini",
        host="127.0.0.1",
        port=0,
        answer_timeout_s=5,
    )


class FakePage:
    def __init__(self) -> None:
        self.url = "about:blank"

    def is_closed(self) -> bool:
        return False


class FakeContext:
    def __init__(self) -> None:
        self.pages: list[FakePage] = []

    async def new_page(self) -> FakePage:
        page = FakePage()
        self.pages.append(page)
        return page


class FakeBrowser:
    def __init__(self) -> None:
        self.ctx = FakeContext()

    async def ensure_context(self) -> FakeContext:
        return self.ctx

    async def new_tab(self) -> FakePage:
        return await self.ctx.new_page()


class FakeAdapter:
    def __init__(self, provider_id: str) -> None:
        self.id = provider_id
        self.name = provider_id
        self.status = "available"
        self.session_status = SessionStatus.LOGGED_IN
        self.ensure_calls: list[FakePage] = []

    async def ensure_page_ready(self, page: FakePage) -> SessionStatus:
        self.ensure_calls.append(page)
        page.url = f"https://{self.id}.example/app"
        return self.session_status


class SlowFakeWorker:
    delays: dict[str, float] = {}
    answers: dict[str, str] = {}

    def __init__(self, page, queue, config, profiles, default_provider) -> None:
        pass

    def start(self) -> None:
        pass

    async def enqueue(self, job) -> None:
        await asyncio.sleep(self.delays.get(job.provider_id, 0))
        job.future.set_result(
            self.answers.get(job.provider_id, f"answer:{job.provider_id}")
        )


@pytest.fixture()
def state(monkeypatch):
    SlowFakeWorker.delays = {}
    SlowFakeWorker.answers = {}
    monkeypatch.setattr(tools, "PageWorker", SlowFakeWorker)
    st = create_app_state(make_config())
    st.browser = FakeBrowser()
    st.page_router = PageRouter(st.browser, max_pages=10)
    return st


@pytest.fixture()
def adapters(monkeypatch):
    fakes = {
        "gemini": FakeAdapter("gemini"),
        "chatgpt": FakeAdapter("chatgpt"),
        "claude": FakeAdapter("claude"),
        "deepseek": FakeAdapter("deepseek"),
        "kimi": FakeAdapter("kimi"),
    }

    def fake_resolve(registry, provider, default):
        reason = "explicit param" if provider else "default provider"
        return fakes[provider or default], reason

    monkeypatch.setattr(tools, "resolve_provider", fake_resolve)
    return fakes


async def test_all_strategy_returns_all_selected_none(state, adapters) -> None:
    res = await handle_ask_multi(
        state,
        prompt="hi",
        providers=["gemini", "chatgpt"],
        strategy="all",
        mcp_session_id=None,
    )
    assert [e["provider"] for e in res["answers"]] == ["gemini", "chatgpt"]
    assert all(e["error"] is None for e in res["answers"])
    assert all(e["answer"] for e in res["answers"])
    assert res["selected"] is None


async def test_default_providers_are_all_available(state, adapters) -> None:
    # providers omitted + empty config list → every "available" adapter
    res = await handle_ask_multi(
        state, prompt="hi", providers=None, strategy="all", mcp_session_id=None
    )
    assert sorted(e["provider"] for e in res["answers"]) == [
        "chatgpt",
        "claude",
        "deepseek",
        "gemini",
        "kimi",
    ]


async def test_first_selects_earliest_finisher(state, adapters) -> None:
    SlowFakeWorker.delays = {"gemini": 0.0, "chatgpt": 0.05}
    res = await handle_ask_multi(
        state,
        prompt="hi",
        providers=["gemini", "chatgpt"],
        strategy="first",
        mcp_session_id=None,
    )
    assert res["selected"]["provider"] == "gemini"
    assert len(res["answers"]) == 2


async def test_longest_selects_longest_answer(state, adapters) -> None:
    SlowFakeWorker.answers = {"gemini": "short", "chatgpt": "a much longer answer"}
    res = await handle_ask_multi(
        state,
        prompt="hi",
        providers=["gemini", "chatgpt"],
        strategy="longest",
        mcp_session_id=None,
    )
    assert res["selected"]["provider"] == "chatgpt"


async def test_partial_failure_keeps_other_answer(state, adapters) -> None:
    adapters["chatgpt"].session_status = SessionStatus.LOGGED_OUT
    res = await handle_ask_multi(
        state,
        prompt="hi",
        providers=["gemini", "chatgpt"],
        strategy="all",
        mcp_session_id=None,
    )
    by = {e["provider"]: e for e in res["answers"]}
    assert by["gemini"]["error"] is None
    assert by["chatgpt"]["error"] == "NOT_LOGGED_IN"
    assert by["chatgpt"]["answer"] is None


async def test_all_fail_returns_entries_never_raises(state, adapters) -> None:
    adapters["gemini"].session_status = SessionStatus.LOGGED_OUT
    adapters["chatgpt"].session_status = SessionStatus.LOGGED_OUT
    res = await handle_ask_multi(
        state,
        prompt="hi",
        providers=["gemini", "chatgpt"],
        strategy="first",
        mcp_session_id=None,
    )
    assert all(e["error"] == "NOT_LOGGED_IN" for e in res["answers"])
    assert res["selected"] is None


async def test_invalid_strategy_raises(state, adapters) -> None:
    with pytest.raises(AiRouterError) as ei:
        await handle_ask_multi(
            state, prompt="hi", providers=None, strategy="best", mcp_session_id=None
        )
    assert ei.value.code == "INVALID_STRATEGY"


async def test_fanout_runs_concurrently(state, monkeypatch) -> None:
    # both warm-ups must be in flight at the same time to pass the barrier;
    # a sequential fan-out deadlocks and trips the 2 s timeout instead
    barrier = asyncio.Barrier(2)

    class BarrierAdapter(FakeAdapter):
        async def ensure_page_ready(self, page: FakePage) -> SessionStatus:
            await asyncio.wait_for(barrier.wait(), timeout=2)
            return await super().ensure_page_ready(page)

    fakes = {"gemini": BarrierAdapter("gemini"), "chatgpt": BarrierAdapter("chatgpt")}
    monkeypatch.setattr(
        tools,
        "resolve_provider",
        lambda registry, provider, default: (fakes[provider], "explicit param"),
    )
    res = await handle_ask_multi(
        state,
        prompt="hi",
        providers=["gemini", "chatgpt"],
        strategy="all",
        mcp_session_id=None,
    )
    assert all(e["error"] is None for e in res["answers"])
