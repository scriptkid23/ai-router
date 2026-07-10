from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass

from playwright.async_api import Error as PlaywrightError

from ai_router.adapters.base import SessionStatus
from ai_router.adapters.registry import ProviderRegistry, build_registry
from ai_router.browser.events import page_id_of
from ai_router.browser.manager import BrowserManager
from ai_router.browser.page_queue import AskJob, PageQueueRegistry
from ai_router.browser.page_worker import PageWorker
from ai_router.browser.profile import ProviderProfile
from ai_router.config import AppConfig, load_config
from ai_router.errors import (
    AiRouterError,
    BrowserClosedError,
    NotLoggedInError,
    ProviderNotReadyError,
    TimeoutError_,
)
from ai_router.router.resolve import resolve_provider
from ai_router.logger import trace


@dataclass
class AppState:
    config: AppConfig
    registry: ProviderRegistry
    browser: BrowserManager
    page_queues: PageQueueRegistry
    page_workers: dict[str, PageWorker]
    profiles: dict[str, ProviderProfile]


def create_app_state(config: AppConfig | None = None) -> AppState:
    cfg = config or load_config()
    registry = build_registry()
    # No hasattr guard: an "available" adapter missing build_profile should
    # fail fast at startup (AttributeError) rather than surface later as a
    # confusing ProviderNotReadyError at ask time.
    profiles = {
        a.id: a.build_profile(cfg)
        for a in registry.list_all()
        if a.status == "available"
    }
    return AppState(
        config=cfg,
        registry=registry,
        browser=BrowserManager(cfg),
        page_queues=PageQueueRegistry(),
        page_workers={},
        profiles=profiles,
    )


def ensure_worker(state: AppState, page) -> PageWorker:
    pid = page_id_of(page)
    if pid not in state.page_workers:
        if len(state.page_workers) >= state.config.max_pages:
            raise AiRouterError("BROWSER_BUSY", "Maximum page workers reached")
        queue = state.page_queues.queue_for(page)
        worker = PageWorker(
            page, queue, state.config, state.profiles, state.config.default_provider
        )
        worker.start()
        state.page_workers[pid] = worker
        trace("worker_created", page_id=pid, worker_count=len(state.page_workers))
    return state.page_workers[pid]


async def handle_ask(
    state: AppState,
    *,
    prompt: str,
    provider: str | None,
    mcp_session_id: str | None,
) -> dict:
    adapter, routing_reason = resolve_provider(
        state.registry, provider, default=state.config.default_provider
    )
    if adapter.status == "coming_soon":
        raise ProviderNotReadyError(adapter.id)

    try:
        page = await state.browser.new_page()
        if hasattr(adapter, "ensure_page_ready"):
            status = await adapter.ensure_page_ready(page)
        else:
            status = await adapter.check_session(page)
        if status == SessionStatus.LOGGED_OUT:
            raise NotLoggedInError()

        worker = ensure_worker(state, page)
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        profile = state.profiles.get(adapter.id)
        timeout_s = (
            profile.answer_timeout_s
            if profile is not None and profile.answer_timeout_s
            else float(state.config.answer_timeout_s)
        )
        job = AskJob(
            job_id=str(uuid.uuid4()),
            mcp_session_id=mcp_session_id,
            prompt=prompt,
            provider_id=adapter.id,
            future=future,
            timeout_s=timeout_s,
        )
        page_id = page_id_of(page)
        trace(
            "ask_enqueue",
            job_id=job.job_id,
            mcp_session_id=mcp_session_id,
            page_id=page_id,
            provider=adapter.id,
            prompt=prompt[:80],
            queue_depth=state.page_queues.queue_for(page).depth(),
            running_job_id=getattr(worker, "_running_job_id", None),
        )
        await worker.enqueue(job)
        trace("ask_await", job_id=job.job_id, mcp_session_id=mcp_session_id)
        try:
            answer = await asyncio.wait_for(future, timeout=job.timeout_s)
        except asyncio.TimeoutError:
            future.cancel()
            trace("ask_timeout", job_id=job.job_id, mcp_session_id=mcp_session_id)
            raise TimeoutError_() from None
        trace(
            "ask_return",
            job_id=job.job_id,
            mcp_session_id=mcp_session_id,
            answer_len=len(answer),
        )
    except PlaywrightError as exc:
        if "closed" in str(exc).lower():
            raise BrowserClosedError() from exc
        raise AiRouterError("ADAPTER_ERROR", str(exc)) from exc

    return {
        "answer": answer,
        "provider": adapter.id,
        "routing_reason": routing_reason,
    }


async def handle_list_providers(state: AppState) -> dict:
    return {
        "providers": [
            {"id": a.id, "name": a.name, "status": a.status}
            for a in state.registry.list_all()
        ]
    }


async def handle_session_status(
    state: AppState,
    *,
    provider: str | None,
) -> dict:
    page = await state.browser.new_page()
    targets = (
        [state.registry.get(provider)]
        if provider
        else state.registry.list_all()
    )
    result: dict[str, str] = {}
    for adapter in targets:
        if adapter.status == "coming_soon":
            result[adapter.id] = SessionStatus.UNKNOWN.value
            continue
        status = await adapter.check_session(page)
        result[adapter.id] = status.value
    return result
