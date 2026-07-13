from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ai_router.adapters.base import SessionStatus
from ai_router.browser.manager import BrowserManager
from ai_router.browser.page_router import PageRouter
from ai_router.config import AppConfig
from ai_router.errors import AiRouterError, NotLoggedInError


class FakePage:
    def __init__(self, url: str = "about:blank") -> None:
        self.url = url
        self.closed = False

    def is_closed(self) -> bool:
        return self.closed


class FakeContext:
    def __init__(self, pages: list[FakePage] | None = None) -> None:
        self.pages: list[FakePage] = pages if pages is not None else []

    async def new_page(self) -> FakePage:
        page = FakePage()
        self.pages.append(page)
        return page


class FakeBrowser:
    def __init__(self, ctx: FakeContext | None = None) -> None:
        self.ctx = ctx or FakeContext()

    async def ensure_context(self) -> FakeContext:
        return self.ctx

    async def new_tab(self) -> FakePage:
        return await self.ctx.new_page()


class FakeAdapter:
    def __init__(self, provider_id: str) -> None:
        self.id = provider_id
        self.session_status = SessionStatus.LOGGED_IN
        self.ensure_calls: list[FakePage] = []

    async def ensure_page_ready(self, page: FakePage) -> SessionStatus:
        self.ensure_calls.append(page)
        page.url = f"https://{self.id}.example/app"  # warm-up navigates off about:blank
        return self.session_status


async def test_new_tab_always_creates(monkeypatch) -> None:
    cfg = AppConfig(
        profile_dir=Path("profile"),
        default_provider="gemini",
        host="127.0.0.1",
        port=0,
        answer_timeout_s=5,
    )
    mgr = BrowserManager(cfg)
    ctx = FakeContext(pages=[FakePage()])

    async def fake_ensure():
        return ctx

    monkeypatch.setattr(mgr, "ensure_context", fake_ensure)
    p1 = await mgr.new_tab()
    p2 = await mgr.new_tab()
    assert p1 is not p2
    assert len(ctx.pages) == 3  # new_page() would have reused pages[0]


async def test_pinned_tab_per_provider() -> None:
    router = PageRouter(FakeBrowser(), max_pages=10)
    gemini, chatgpt = FakeAdapter("gemini"), FakeAdapter("chatgpt")
    g1 = await router.page_for(gemini)
    c1 = await router.page_for(chatgpt)
    g2 = await router.page_for(gemini)
    assert g1 is g2
    assert g1 is not c1


async def test_warm_runs_once_per_provider() -> None:
    router = PageRouter(FakeBrowser(), max_pages=10)
    gemini = FakeAdapter("gemini")
    await router.page_for(gemini)
    await router.page_for(gemini)
    assert len(gemini.ensure_calls) == 1


async def test_logged_out_raises_then_rewarms() -> None:
    router = PageRouter(FakeBrowser(), max_pages=10)
    gemini = FakeAdapter("gemini")
    gemini.session_status = SessionStatus.LOGGED_OUT
    with pytest.raises(NotLoggedInError):
        await router.page_for(gemini)
    gemini.session_status = SessionStatus.LOGGED_IN
    page = await router.page_for(gemini)
    assert len(gemini.ensure_calls) == 2  # warm-up not falsely cached
    assert page is gemini.ensure_calls[0]  # pin itself was kept


async def test_closed_pin_recreated_and_rewarmed() -> None:
    router = PageRouter(FakeBrowser(), max_pages=10)
    gemini = FakeAdapter("gemini")
    p1 = await router.page_for(gemini)
    p1.closed = True
    p2 = await router.page_for(gemini)
    assert p2 is not p1
    assert len(gemini.ensure_calls) == 2


async def test_max_pages_exceeded_raises_busy() -> None:
    router = PageRouter(FakeBrowser(), max_pages=1)
    await router.page_for(FakeAdapter("gemini"))
    with pytest.raises(AiRouterError) as ei:
        await router.page_for(FakeAdapter("chatgpt"))
    assert ei.value.code == "BROWSER_BUSY"


async def test_adopts_initial_blank_tab() -> None:
    # persistent contexts open with one about:blank page — the first pin
    # should adopt it instead of leaving a stray blank tab
    ctx = FakeContext(pages=[FakePage()])
    router = PageRouter(FakeBrowser(ctx), max_pages=10)
    p1 = await router.page_for(FakeAdapter("gemini"))
    assert p1 is ctx.pages[0]
    p2 = await router.page_for(FakeAdapter("chatgpt"))
    assert p2 is not p1
    assert len(ctx.pages) == 2


async def test_status_page_is_separate_and_stable() -> None:
    router = PageRouter(FakeBrowser(), max_pages=10)
    pin = await router.page_for(FakeAdapter("gemini"))
    s1 = await router.status_page()
    s2 = await router.status_page()
    assert s1 is s2
    assert s1 is not pin


async def test_concurrent_cold_start_gets_separate_tabs() -> None:
    ctx = FakeContext(pages=[FakePage()])
    router = PageRouter(FakeBrowser(ctx), max_pages=10)

    class SlowAdapter(FakeAdapter):
        async def ensure_page_ready(self, page: FakePage) -> SessionStatus:
            await asyncio.sleep(0.05)
            return await super().ensure_page_ready(page)

    gemini, chatgpt = SlowAdapter("gemini"), SlowAdapter("chatgpt")
    g_page, c_page = await asyncio.gather(
        router.page_for(gemini),
        router.page_for(chatgpt),
    )
    assert g_page is not c_page
    assert len(ctx.pages) == 2
