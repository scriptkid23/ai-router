from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from playwright.async_api import Page

BrowserEventKind = Literal[
    "request_finished",
    "response",
    "framenavigated",
    "console",
    "pageerror",
    "dom_tick",
]

DomPollFn = Callable[[Page], Awaitable[dict[str, Any]]]


@dataclass
class BrowserEvent:
    page_id: str
    kind: BrowserEventKind
    payload: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)


class EventChannel:
    def __init__(self, page_id: str, *, maxsize: int = 256) -> None:
        self.page_id = page_id
        self._queue: asyncio.Queue[BrowserEvent] = asyncio.Queue(maxsize=maxsize)

    async def emit(self, kind: BrowserEventKind, **payload: Any) -> None:
        await self._queue.put(BrowserEvent(page_id=self.page_id, kind=kind, payload=payload))

    async def get(self) -> BrowserEvent:
        return await self._queue.get()

    def try_get_nowait(self) -> BrowserEvent | None:
        try:
            return self._queue.get_nowait()
        except asyncio.QueueEmpty:
            return None


def page_id_of(page: Page) -> str:
    return str(id(page))


def attach_listeners(page: Page, channel: EventChannel) -> None:
    loop = asyncio.get_event_loop()

    def on_request_finished(request) -> None:
        loop.create_task(channel.emit("request_finished", url=request.url))

    def on_framenavigated(frame) -> None:
        if frame == page.main_frame:
            loop.create_task(channel.emit("framenavigated", url=frame.url))

    page.on("requestfinished", on_request_finished)
    page.on("framenavigated", on_framenavigated)


async def dom_tick_loop(
    page: Page,
    channel: EventChannel,
    *,
    interval_ms: int,
    poll_fn: DomPollFn,
    stop_event: asyncio.Event,
) -> None:
    while not stop_event.is_set():
        snapshot = await poll_fn(page)
        await channel.emit("dom_tick", **snapshot)
        await asyncio.sleep(interval_ms / 1000)
