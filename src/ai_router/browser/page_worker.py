from __future__ import annotations

import asyncio
import time

from playwright.async_api import Page

from ai_router.browser.commands import CommandExecutor
from ai_router.browser.events import (
    EventChannel,
    attach_listeners,
    dom_tick_loop,
    page_id_of,
)
from ai_router.browser.page_queue import AskJob, PageQueue
from ai_router.browser.profile import ProviderProfile
from ai_router.browser.state import StateReducer
from ai_router.config import AppConfig
from ai_router.errors import AiRouterError, ProviderNotReadyError
from ai_router.logger import trace


class PageWorker:
    def __init__(
        self,
        page: Page,
        queue: PageQueue,
        cfg: AppConfig,
        profiles: dict[str, ProviderProfile],
        default_provider: str,
    ) -> None:
        self._page = page
        self._page_id = page_id_of(page)
        self._queue = queue
        self._cfg = cfg
        self._profiles = profiles
        self._profile = profiles.get(default_provider) or next(iter(profiles.values()))
        self._channel = EventChannel(self._page_id)
        self._reducer = StateReducer(
            page_id=self._page_id,
            stream_url_res=[p.stream_url_re for p in profiles.values()],
            idle_streak_required=cfg.idle_streak_required,
            generating_streak_required=cfg.generating_streak_required,
            answer_stable_ticks=cfg.answer_stable_ticks,
            no_stream_fallback_ticks=cfg.no_stream_fallback_ticks,
            stream_quiet_s=cfg.stream_quiet_s,
            # Union across profiles is safe: _dom_snapshot only emits error_text
            # after matching the ACTIVE job's profile.error_markers, so the
            # reducer's re-check can never fire on another provider's markers.
            error_markers=tuple({m for p in profiles.values() for m in p.error_markers}),
        )
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._running_job_id: str | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            trace("worker_start", page_id=self._page_id)
            self._task = asyncio.create_task(self._run())

    async def enqueue(self, job: AskJob) -> None:
        depth = self._queue.depth()
        trace(
            "queue_put",
            page_id=self._page_id,
            job_id=job.job_id,
            mcp_session_id=job.mcp_session_id,
            queue_depth=depth + 1,
            running_job_id=self._running_job_id,
            prompt=job.prompt[:80],
        )
        await self._queue.put(job)

    async def _run(self) -> None:
        attach_listeners(self._page, self._channel, list(self._profiles.values()))
        tick_task = asyncio.create_task(
            dom_tick_loop(
                self._page,
                self._channel,
                interval_ms=self._cfg.dom_tick_interval_ms,
                poll_fn=self._dom_snapshot,
                stop_event=self._stop,
            )
        )
        pump_task = asyncio.create_task(self._pump_events())
        try:
            while not self._stop.is_set():
                await self._wait_idle_gate()
                job = await self._queue.get()
                depth = self._queue.depth()
                trace(
                    "queue_dequeue",
                    page_id=self._page_id,
                    job_id=job.job_id,
                    mcp_session_id=job.mcp_session_id,
                    queue_depth=depth,
                    prompt=job.prompt[:80],
                )
                if job.future.cancelled():
                    trace("job_skip_cancelled", page_id=self._page_id, job_id=job.job_id)
                    continue
                try:
                    answer = await self._execute_job(job)
                    if not job.future.done():
                        job.future.set_result(answer)
                except Exception as exc:
                    trace(
                        "job_failed",
                        page_id=self._page_id,
                        job_id=job.job_id,
                        error=type(exc).__name__,
                        message=str(exc)[:120],
                    )
                    if not job.future.done():
                        job.future.set_exception(exc)
        finally:
            self._stop.set()
            tick_task.cancel()
            pump_task.cancel()
            trace("worker_stop", page_id=self._page_id)

    async def _pump_events(self) -> None:
        while not self._stop.is_set():
            ev = await self._channel.get()
            if ev.kind == "request_finished":
                self._reducer.apply_request_finished(ev.payload.get("url", ""))
            elif ev.kind == "stream_end":
                self._reducer.apply_stream_end(
                    url=ev.payload.get("url", ""),
                    ok=ev.payload.get("ok", True),
                    error_kind=ev.payload.get("error_kind"),
                    error_text=ev.payload.get("error_text"),
                )
            elif ev.kind == "dom_tick":
                self._reducer.apply_dom_tick(
                    generating=ev.payload.get("generating", False),
                    response_count=ev.payload.get("response_count", 0),
                    response_text=ev.payload.get("response_text", ""),
                    error_text=ev.payload.get("error_text"),
                )

    async def _dom_snapshot(self, page: Page) -> dict:
        profile = self._profile
        generating = await profile.is_stop_visible(page)
        count, text = await profile.read_response_snapshot(page)
        body = ""
        try:
            body = (await page.locator("body").inner_text())[:2000].lower()
        except Exception:
            pass
        err = None
        for marker in profile.error_markers:
            if marker in body:
                err = body[:200]
                break
        return {
            "generating": generating,
            "response_count": count,
            "response_text": text,
            "error_text": err,
        }

    async def _stop_visible(self) -> bool:
        return await self._profile.is_stop_visible(self._page)

    def _stream_settled(self, *, stop_visible: bool) -> bool:
        st = self._reducer.state
        if st.stream_ended_at is None:
            return not stop_visible
        since_end = time.time() - st.stream_ended_at
        return since_end >= self._cfg.stream_quiet_s and not stop_visible

    async def _wait_idle_gate(self) -> None:
        last_log = 0.0
        while True:
            st = self._reducer.state
            stop_visible = await self._stop_visible()
            stream_ok = self._stream_settled(stop_visible=stop_visible)
            idle_ok = (
                st.phase == "idle"
                and st.idle_streak >= self._cfg.idle_streak_required
                and stream_ok
            )
            if idle_ok:
                trace(
                    "idle_gate_open",
                    page_id=self._page_id,
                    idle_streak=st.idle_streak,
                    queue_depth=self._queue.depth(),
                    running_job_id=self._running_job_id,
                    stop_visible=stop_visible,
                )
                return
            now = time.monotonic()
            if now - last_log >= 3.0:
                since_end = (
                    round(time.time() - st.stream_ended_at, 1)
                    if st.stream_ended_at
                    else None
                )
                trace(
                    "idle_gate_wait",
                    page_id=self._page_id,
                    phase=st.phase,
                    idle_streak=st.idle_streak,
                    idle_required=self._cfg.idle_streak_required,
                    queue_depth=self._queue.depth(),
                    running_job_id=self._running_job_id,
                    stop_visible=stop_visible,
                    stream_quiet_s=self._cfg.stream_quiet_s,
                    since_stream_end_s=since_end,
                )
                last_log = now
            await asyncio.sleep(0.1)

    async def _execute_job(self, job: AskJob) -> str:
        profile = self._profiles.get(job.provider_id)
        if profile is None:
            raise ProviderNotReadyError(job.provider_id)
        self._profile = profile

        started = time.monotonic()
        self._running_job_id = job.job_id
        self._reducer.set_job(job.job_id)
        self._reducer.reset_job_cycle()
        trace(
            "job_start",
            page_id=self._page_id,
            job_id=job.job_id,
            mcp_session_id=job.mcp_session_id,
            phase=self._reducer.state.phase,
            prompt=job.prompt[:80],
        )
        executor = CommandExecutor(
            self._page,
            self._reducer,
            profile=profile,
            job_id=job.job_id,
            page_id=self._page_id,
            answer_timeout_s=job.timeout_s,
            idle_streak_required=self._cfg.idle_streak_required,
        )
        try:
            answer = await executor.run(profile.planner.plan(job))
            trace(
                "job_done",
                page_id=self._page_id,
                job_id=job.job_id,
                duration_s=round(time.monotonic() - started, 2),
                answer_len=len(answer),
            )
            return answer
        except AiRouterError as exc:
            if exc.code in profile.recoverable_codes:
                trace(
                    "job_recovery",
                    page_id=self._page_id,
                    job_id=job.job_id,
                    error=exc.message[:80],
                )
                answer = await executor.run(profile.planner.plan(job, recovery=True))
                trace(
                    "job_done",
                    page_id=self._page_id,
                    job_id=job.job_id,
                    duration_s=round(time.monotonic() - started, 2),
                    answer_len=len(answer),
                    recovered=True,
                )
                return answer
            raise
        finally:
            self._running_job_id = None
            self._reducer.set_job(None)
