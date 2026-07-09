from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

from ai_router.browser.cloak import launch_persistent_context_async
from playwright.async_api import BrowserContext, Page

from ai_router.config import AppConfig
from ai_router.errors import BrowserBusyError


class BrowserManager:
    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._ctx: BrowserContext | None = None
        self._lock = asyncio.Lock()
        self._busy = False

    async def ensure_context(self) -> BrowserContext:
        if self._ctx is None:
            self._config.profile_dir.mkdir(parents=True, exist_ok=True)
            self._ctx = await launch_persistent_context_async(
                str(self._config.profile_dir),
                headless=False,
            )
        return self._ctx

    async def new_page(self) -> Page:
        ctx = await self.ensure_context()
        if ctx.pages:
            return ctx.pages[0]
        return await ctx.new_page()

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[BrowserContext]:
        async with self._lock:
            if self._busy:
                raise BrowserBusyError()
            self._busy = True
            try:
                yield await self.ensure_context()
            finally:
                self._busy = False

    async def close(self) -> None:
        if self._ctx:
            await self._ctx.close()
            self._ctx = None
