from __future__ import annotations

import asyncio
from dataclasses import dataclass

from playwright.async_api import Page

from ai_router.browser.events import page_id_of


@dataclass
class AskJob:
    job_id: str
    mcp_session_id: str
    prompt: str
    provider_id: str
    future: asyncio.Future[str]
    timeout_s: float


class PageQueue:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[AskJob] = asyncio.Queue()

    def depth(self) -> int:
        return self._queue.qsize()

    async def put(self, job: AskJob) -> None:
        await self._queue.put(job)

    async def get(self) -> AskJob:
        return await self._queue.get()


class PageQueueRegistry:
    def __init__(self) -> None:
        self._queues: dict[str, PageQueue] = {}

    def queue_for(self, page: Page) -> PageQueue:
        pid = page_id_of(page)
        if pid not in self._queues:
            self._queues[pid] = PageQueue()
        return self._queues[pid]

    def drop(self, page: Page) -> None:
        self._queues.pop(page_id_of(page), None)
