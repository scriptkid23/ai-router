from __future__ import annotations

from pathlib import Path

import pytest

from ai_router.adapters.base import SessionStatus
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
    url = "https://gemini.google.com/app"

    def is_closed(self) -> bool:
        return False


class FakeBrowser:
    def __init__(self, page: FakePage) -> None:
        self._page = page

    async def new_page(self) -> FakePage:
        return self._page


class FakeAdapter:
    id = "gemini"
    name = "Gemini"
    status = "available"

    def __init__(self) -> None:
        self.session_status = SessionStatus.LOGGED_IN
        self.ensure_calls: list[FakePage] = []

    async def ensure_page_ready(self, page: FakePage) -> SessionStatus:
        self.ensure_calls.append(page)
        return self.session_status


class FakeWorker:
    def __init__(self, page, queue, config) -> None:
        self.jobs = []

    def start(self) -> None:
        pass

    async def enqueue(self, job) -> None:
        self.jobs.append(job)
        job.future.set_result("fake answer")


@pytest.fixture()
def state(monkeypatch):
    monkeypatch.setattr(tools, "PageWorker", FakeWorker)
    st = create_app_state(make_config())
    st.browser = FakeBrowser(FakePage())
    return st


@pytest.fixture()
def adapter(monkeypatch):
    fake = FakeAdapter()
    monkeypatch.setattr(
        tools,
        "resolve_provider",
        lambda registry, provider, default: (fake, "default"),
    )
    return fake


async def test_ask_without_mcp_session_id(state, adapter) -> None:
    result = await handle_ask(state, prompt="hi", provider=None, mcp_session_id=None)
    assert result["answer"] == "fake answer"


async def test_each_ask_opens_new_chat(state, adapter) -> None:
    await handle_ask(state, prompt="one", provider=None, mcp_session_id="s1")
    await handle_ask(state, prompt="two", provider=None, mcp_session_id="s1")
    # ensure_page_ready runs on every ask — a fresh chat each time,
    # never a resume of a prior conversation
    assert len(adapter.ensure_calls) == 2


async def test_logged_out_raises(state, adapter) -> None:
    adapter.session_status = SessionStatus.LOGGED_OUT
    with pytest.raises(NotLoggedInError):
        await handle_ask(state, prompt="hi", provider=None, mcp_session_id=None)


def test_app_state_has_no_sessions() -> None:
    st = create_app_state(make_config())
    assert not hasattr(st, "sessions")
