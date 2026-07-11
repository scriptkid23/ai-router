from __future__ import annotations

from typing import TYPE_CHECKING

from playwright.async_api import Page

from ai_router.adapters.base import SessionStatus
from ai_router.browser.events import page_id_of
from ai_router.browser.manager import BrowserManager
from ai_router.errors import AiRouterError, NotLoggedInError
from ai_router.logger import trace

if TYPE_CHECKING:
    from ai_router.adapters.base import ProviderAdapter


class PageRouter:
    """Pin one browser tab per provider so providers run in parallel.

    Same provider stays FIFO on its pinned tab (PageQueue + idle gate);
    different providers never share a tab, so one generating never blocks
    the other. Pinned tabs are never closed between asks — the job plan's
    leading goto resets them to a fresh chat (stateless invariant).
    """

    def __init__(self, browser: BrowserManager, max_pages: int) -> None:
        self._browser = browser
        self._max_pages = max_pages
        self._pages: dict[str, Page] = {}
        self._warmed: set[str] = set()
        self._status_page: Page | None = None

    async def page_for(self, adapter: "ProviderAdapter") -> Page:
        page = self._pages.get(adapter.id)
        if page is not None and page.is_closed():
            trace("router_pin_dropped", provider=adapter.id)
            self._pages.pop(adapter.id, None)
            self._warmed.discard(adapter.id)
            page = None
        if page is None:
            if len(self._pages) >= self._max_pages:
                raise AiRouterError("BROWSER_BUSY", "Maximum pinned tabs reached")
            page = await self._adopt_or_create()
            self._pages[adapter.id] = page
            trace("router_pin_created", provider=adapter.id, page_id=page_id_of(page))
        if adapter.id not in self._warmed:
            status = await adapter.ensure_page_ready(page)
            if status == SessionStatus.LOGGED_OUT:
                raise NotLoggedInError()
            self._warmed.add(adapter.id)
            trace("router_warmed", provider=adapter.id, status=str(status))
        return page

    async def status_page(self) -> Page:
        if self._status_page is None or self._status_page.is_closed():
            self._status_page = await self._adopt_or_create()
        return self._status_page

    async def _adopt_or_create(self) -> Page:
        ctx = await self._browser.ensure_context()
        claimed = {page_id_of(p) for p in self._pages.values()}
        if self._status_page is not None:
            claimed.add(page_id_of(self._status_page))
        for p in ctx.pages:
            if not p.is_closed() and p.url == "about:blank" and page_id_of(p) not in claimed:
                return p
        return await self._browser.new_tab()
