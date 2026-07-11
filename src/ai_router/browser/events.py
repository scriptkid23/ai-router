from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from playwright.async_api import Page

from ai_router.browser.profile import ProviderProfile
from ai_router.logger import trace

BrowserEventKind = Literal[
    "request_finished",
    "response",
    "stream_end",
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


async def handle_response(
    response: Any, channel: EventChannel, profiles: Sequence[ProviderProfile]
) -> None:
    profile = next(
        (p for p in profiles if p.stream_url_re.search(response.url)), None
    )
    if profile is None:
        return
    trace(
        "stream_response",
        provider=profile.provider_id,
        url=response.url[:110],
        status=getattr(response, "status", None),
    )
    try:
        await response.finished()
        status = response.status
        body = await response.text()
    except Exception as exc:
        trace(
            "stream_response_error",
            provider=profile.provider_id,
            url=response.url[:110],
            error=repr(exc)[:110],
        )
        return
    result = profile.parse_stream_done(status, body)
    trace(
        "stream_parse",
        provider=profile.provider_id,
        done=result.done,
        ok=result.ok,
        kind=result.error_kind,
        body_len=len(body),
    )
    if result.done:
        await channel.emit(
            "stream_end",
            url=response.url,
            ok=result.ok,
            error_kind=result.error_kind,
            error_text=result.error_text,
        )


def attach_listeners(
    page: Page, channel: EventChannel, profiles: Sequence[ProviderProfile]
) -> None:
    loop = asyncio.get_event_loop()

    def on_request_finished(request) -> None:
        loop.create_task(channel.emit("request_finished", url=request.url))

    def on_response(response) -> None:
        loop.create_task(handle_response(response, channel, profiles))

    def on_framenavigated(frame) -> None:
        if frame == page.main_frame:
            loop.create_task(channel.emit("framenavigated", url=frame.url))

    def on_websocket(ws) -> None:
        trace("websocket_open", url=ws.url[:110])

        def on_frame(payload) -> None:
            if isinstance(payload, bytes):
                text = payload.decode("utf-8", errors="ignore")
            else:
                text = payload
            for profile in profiles:
                parser = profile.parse_ws_frame
                if parser is None:
                    continue
                result = parser(text)
                if result is not None and result.done:
                    trace(
                        "ws_stream_end",
                        provider=profile.provider_id,
                        ok=result.ok,
                        kind=result.error_kind,
                    )
                    loop.create_task(
                        channel.emit(
                            "stream_end",
                            url=ws.url,
                            ok=result.ok,
                            error_kind=result.error_kind,
                            error_text=result.error_text,
                        )
                    )
                    break

        ws.on("framereceived", on_frame)

    page.on("requestfinished", on_request_finished)
    page.on("response", on_response)
    page.on("framenavigated", on_framenavigated)
    page.on("websocket", on_websocket)


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
