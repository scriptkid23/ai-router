from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ai_router.adapters.base import SessionStatus
from ai_router.browser.page_router import PageRouter
from ai_router.config import AppConfig
from ai_router.errors import NotLoggedInError
from ai_router.mcp import tools
from ai_router.mcp.tools import create_app_state, handle_ask


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
    def __init__(self, provider_id: str = "gemini") -> None:
        self.id = provider_id
        self.name = provider_id
        self.status = "available"
        self.session_status = SessionStatus.LOGGED_IN
        self.ensure_calls: list[FakePage] = []
        self.check_calls: list[FakePage] = []

    async def ensure_page_ready(self, page: FakePage) -> SessionStatus:
        self.ensure_calls.append(page)
        page.url = f"https://{self.id}.example/app"
        return self.session_status

    async def check_session(self, page: FakePage) -> SessionStatus:
        self.check_calls.append(page)
        return self.session_status


class FakeWorker:
    def __init__(self, page, queue, config, profiles, default_provider) -> None:
        self.jobs = []

    def start(self) -> None:
        pass

    async def enqueue(self, job) -> None:
        self.jobs.append(job)
        job.future.set_result(f"fake answer:{job.provider_id}")


@pytest.fixture()
def state(monkeypatch):
    monkeypatch.setattr(tools, "PageWorker", FakeWorker)
    st = create_app_state(make_config())
    st.browser = FakeBrowser()
    st.page_router = PageRouter(st.browser, max_pages=10)
    return st


@pytest.fixture()
def adapter(monkeypatch):
    fake = FakeAdapter("gemini")
    monkeypatch.setattr(
        tools,
        "resolve_provider",
        lambda registry, provider, default: (fake, "default"),
    )
    return fake


async def test_ask_without_mcp_session_id(state, adapter) -> None:
    result = await handle_ask(state, prompt="hi", provider=None, mcp_session_id=None)
    assert result["answer"] == "fake answer:gemini"


async def test_warm_runs_once_fresh_chat_lives_in_plan(state, adapter) -> None:
    await handle_ask(state, prompt="one", provider=None, mcp_session_id="s1")
    await handle_ask(state, prompt="two", provider=None, mcp_session_id="s1")
    # Login warm-up runs once per pinned tab. The "fresh chat per ask"
    # invariant moved into the job plan: every planner script starts with
    # goto home (see test_gemini_planner / test_chatgpt_planner).
    assert len(adapter.ensure_calls) == 1


async def test_logged_out_raises(state, adapter) -> None:
    adapter.session_status = SessionStatus.LOGGED_OUT
    with pytest.raises(NotLoggedInError):
        await handle_ask(state, prompt="hi", provider=None, mcp_session_id=None)


async def test_concurrent_asks_use_separate_tabs(state, monkeypatch) -> None:
    gemini, chatgpt = FakeAdapter("gemini"), FakeAdapter("chatgpt")
    fakes = {"gemini": gemini, "chatgpt": chatgpt}
    monkeypatch.setattr(
        tools,
        "resolve_provider",
        lambda registry, provider, default: (fakes[provider], "explicit param"),
    )
    r1, r2 = await asyncio.gather(
        handle_ask(state, prompt="hi", provider="gemini", mcp_session_id=None),
        handle_ask(state, prompt="hi", provider="chatgpt", mcp_session_id=None),
    )
    assert r1["answer"] == "fake answer:gemini"
    assert r2["answer"] == "fake answer:chatgpt"
    assert gemini.ensure_calls[0] is not chatgpt.ensure_calls[0]


def test_app_state_has_router_and_no_sessions() -> None:
    st = create_app_state(make_config())
    assert not hasattr(st, "sessions")
    assert st.page_router is not None


class FakeRegistry:
    def __init__(self, adapters):
        self._adapters = adapters

    def list_all(self):
        return self._adapters


async def test_session_status_never_touches_pinned_tabs(state, adapter) -> None:
    await handle_ask(state, prompt="hi", provider=None, mcp_session_id=None)
    pinned = adapter.ensure_calls[0]
    state.registry = FakeRegistry([adapter])
    await tools.handle_session_status(state, provider=None)
    assert adapter.check_calls[0] is not pinned
