# Parallel Ask (Pinned Tab per Provider) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Send the same prompt to Gemini and ChatGPT truly in parallel — each provider on its own pinned Playwright tab in the one shared browser profile — via a new `ask_multi` MCP tool, without changing the existing `ask` API.

**Architecture:** A new `PageRouter` pins one tab per provider (lazy create + one-time login warm-up per tab) so different providers never share a tab. The "fresh chat per ask" goto moves OUT of `handle_ask` and INTO the job plan (planners now start every script with `goto home`), so it runs behind the worker's idle gate and can never navigate a tab that is still generating a previous answer. `handle_ask_multi` fans out over `handle_ask` with `asyncio.gather`, catching `AiRouterError` per provider (no fail-fast).

**Tech Stack:** Python 3.11, Playwright (async), pytest + pytest-asyncio (asyncio_mode=auto), Poetry.

**Spec:** `docs/superpowers/specs/2026-07-10-parallel-ask-design.md`

## Deviations from the spec (found during review — all preserve the spec's decided outcomes)

1. **Fresh-chat goto lives in the planner, not in `handle_ask`.** The spec's §3 shape (`page_router.open_new_chat()` inside `handle_ask`, before enqueue) would let ask N+1 navigate the pinned tab while ask N is still generating on it — violating the spec's own parallelism table ("same provider / same tab: FIFO"). Moving the goto into the job script (executed after the worker's idle gate) preserves the stateless invariant AND the FIFO guarantee. This also matches the spec's backward-compat note that the "fresh chat mỗi ask" invariant may move.
2. **`CommandExecutor` rebases `before_count` after a `goto`.** `answer_ready` requires `response_count > before_count`, but `run()` reads `before_count` before any goto — a goto to a fresh chat resets the DOM count to 0, so a stale baseline ≥1 makes the answer permanently "not ready". This is a latent bug in the existing recovery path too; the fix covers both.
3. **No light per-ask `check_session`.** The spec's per-ask "light check" doesn't exist today (`check_session` == full `ensure_page_ready`). Login is verified once per pinned tab at warm-up; a mid-session logout surfaces as a job failure (`SUBMIT_FAILED`/timeout). Tab crash / context reset drops the pin and re-warms on the next ask.
4. **Pin map lives in `PageRouter`; `BrowserManager` only gains `new_tab()`** (always creates a tab, with the existing crash-reset retry). The spec allowed either location. `new_page()` keeps its old reuse-`pages[0]` behavior for `cli/browser.py` login compat.
5. **`page_for(adapter)` takes the adapter object, not a provider id** — avoids a router→registry dependency and lets tests pass fake adapters (spec left the signature open).
6. **`handle_session_status` gets its own status tab** — after pinning, `browser.new_page()`'s `pages[0]` is Gemini's pinned tab; a status goto would kill an in-flight ask.
7. **Spec §4 Phase 2 (single-profile worker) is skipped** — spec marks it optional; YAGNI.

## Global Constraints

- Run tests with `poetry run pytest -q` (asyncio_mode=auto — async tests need no decorator). Lint: `poetry run ruff check src tests` (line-length 100).
- MCP tool `ask` — signature and response **unchanged**.
- Stateless invariant: every ask gets a fresh chat (now enforced by the job plan's leading `goto home`).
- `ask_multi` fan-out never fail-fasts: per-provider errors become entries (`error` = AiRouterError code), never a raised exception — even when ALL providers fail.
- Existing error types only: `NotLoggedInError`, `BrowserClosedError`, `TimeoutError_`, `ProviderNotReadyError`, `RateLimitedError`, plus `AiRouterError("BROWSER_BUSY")` for pin/worker limits and `AiRouterError("INVALID_STRATEGY")` for a bad strategy.
- ChatGPT answer timeout stays 300.0 s (`chatgpt_answer_timeout_s`); Gemini uses `answer_timeout_s` (120 s default).
- Full suite green after every task (Task 0 fixes today's red baseline first). Commit after every task.

---

### Task 0: Fix the red baseline (stale test fakes from commit 1c8f84b)

The suite currently fails 13 tests: `StateReducer` gained a required `stream_url_res` kwarg and `PageWorker` gained `profiles` + `default_provider` ctor args, but the test fakes were never updated.

**Files:**
- Modify: `tests/test_state_reducer.py`
- Modify: `tests/test_command_waits.py`
- Modify: `tests/test_tools_stateless.py` (FakeWorker signature only)

**Interfaces:**
- Consumes: `StateReducer.__init__(*, page_id, stream_url_res, idle_streak_required, generating_streak_required, answer_stable_ticks, stream_quiet_s, error_markers, no_stream_fallback_ticks=20)` — `src/ai_router/browser/state.py:32`; `PageWorker.__init__(page, queue, cfg, profiles, default_provider)` — `src/ai_router/browser/page_worker.py:24`.
- Produces: a green baseline. No production code changes.

- [ ] **Step 1: Confirm the failures**

Run: `poetry run pytest -q`
Expected: 13 failed (TypeError: `stream_url_res` missing ×11, `FakeWorker.__init__() takes 4 positional arguments but 6 were given` ×2), 21 passed.

- [ ] **Step 2: Fix the StateReducer fakes**

In BOTH `tests/test_state_reducer.py` and `tests/test_command_waits.py`, add at the top (after existing imports):

```python
import re

STREAM_RE = re.compile(r"StreamGenerate")
```

Then in EVERY `StateReducer(...)` constructor call in both files (7 in `test_state_reducer.py`, 4 in `test_command_waits.py`), add this line directly after `page_id="test",`:

```python
        stream_url_res=[STREAM_RE],
```

(The regex matches the Gemini stream URL already used by `apply_request_finished` calls in these tests, so stream-dependent assertions keep working.)

- [ ] **Step 3: Fix the FakeWorker signature**

In `tests/test_tools_stateless.py` replace:

```python
class FakeWorker:
    def __init__(self, page, queue, config) -> None:
        self.jobs = []
```

with:

```python
class FakeWorker:
    def __init__(self, page, queue, config, profiles, default_provider) -> None:
        self.jobs = []
```

- [ ] **Step 4: Run the suite — green**

Run: `poetry run pytest -q`
Expected: 34 passed, 0 failed.

- [ ] **Step 5: Commit**

```bash
git add tests/test_state_reducer.py tests/test_command_waits.py tests/test_tools_stateless.py
git commit -m "test: update stale fakes for StateReducer stream_url_res and PageWorker profiles"
```

---

### Task 1: `BrowserManager.new_tab()` + `PageRouter`

**Files:**
- Modify: `src/ai_router/browser/manager.py` (add `new_tab()` after `new_page()`, `manager.py:64`)
- Create: `src/ai_router/browser/page_router.py`
- Test: `tests/test_page_router.py`

**Interfaces:**
- Consumes: `BrowserManager.ensure_context()`, `page_id_of(page) -> str` (`src/ai_router/browser/events.py:53`, it's `str(id(page))`), `SessionStatus` (`src/ai_router/adapters/base.py:13` — a leaf module, no import cycle), `NotLoggedInError` / `AiRouterError` (`src/ai_router/errors.py`).
- Produces (used by Tasks 3, 4, 6):
  - `BrowserManager.new_tab() -> Page` — ALWAYS creates a new tab (unlike `new_page()` which reuses `pages[0]`), with the same crash-reset retry.
  - `PageRouter(browser: BrowserManager, max_pages: int)`
  - `async PageRouter.page_for(adapter) -> Page` — pinned tab keyed by `adapter.id`; creates the tab and runs `adapter.ensure_page_ready(page)` once per tab (warm-up); raises `NotLoggedInError` on `LOGGED_OUT` (and will re-warm on the next call); `UNKNOWN` proceeds (matches current `handle_ask`); recreates + re-warms if the pinned tab is closed; raises `AiRouterError("BROWSER_BUSY")` when a NEW pin would exceed `max_pages`.
  - `async PageRouter.status_page() -> Page` — dedicated diagnostic tab, never one of the pins, not counted against `max_pages`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_page_router.py`:

```python
from __future__ import annotations

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
        profile_dir=Path("profile"), default_provider="gemini",
        host="127.0.0.1", port=0, answer_timeout_s=5,
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_page_router.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ai_router.browser.page_router'` (and `new_tab` AttributeError).

- [ ] **Step 3: Implement**

Add to `src/ai_router/browser/manager.py` (after `new_page()`):

```python
    async def new_tab(self) -> Page:
        """Always create a NEW tab (new_page() reuses pages[0])."""
        ctx = await self.ensure_context()
        try:
            return await ctx.new_page()
        except PlaywrightError:
            await self._reset_context()
            ctx = await self.ensure_context()
            return await ctx.new_page()
```

Create `src/ai_router/browser/page_router.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_page_router.py -q` then `poetry run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/ai_router/browser/manager.py src/ai_router/browser/page_router.py tests/test_page_router.py
git commit -m "feat: PageRouter pins one tab per provider; BrowserManager.new_tab"
```

---

### Task 2: Fresh-chat goto moves into the job plan

Every planner script now starts with `goto home` + `wait_idle`, so the fresh chat opens INSIDE the job (behind the worker's idle gate) instead of from `handle_ask`. `CommandExecutor` rebases its `before_count` baseline after a goto (also fixes the latent recovery bug where a stale baseline could make `answer_ready` unreachable).

**Files:**
- Modify: `src/ai_router/adapters/gemini/planner.py`
- Modify: `src/ai_router/adapters/chatgpt/planner.py`
- Modify: `src/ai_router/browser/commands.py:95-96` (the `goto` branch in `run()`)
- Modify: `tests/test_gemini_planner.py` (replace content)
- Create: `tests/test_chatgpt_planner.py`
- Create: `tests/test_command_executor.py`

**Interfaces:**
- Consumes: `Command` / `CommandExecutor` (`src/ai_router/browser/commands.py`), `GEMINI_URL` / `CHATGPT_URL` selector constants, `AskJob(job_id, mcp_session_id, prompt, provider_id, future, timeout_s)`.
- Produces: `GeminiPlanner.plan(job, *, recovery=False)` and `ChatGPTPlanner.plan(job, *, recovery=False)` both return `[goto home, wait_idle, clear_input, type, submit, wait_generating, wait_answer]` (recovery returns the same script). Executor `goto` refreshes the `before_count` baseline.

- [ ] **Step 1: Write the failing planner tests**

Replace `tests/test_gemini_planner.py` with:

```python
from ai_router.adapters.gemini.planner import GeminiPlanner
from ai_router.adapters.gemini.selectors import GEMINI_URL
from ai_router.browser.page_queue import AskJob


def make_job() -> AskJob:
    import asyncio

    loop = asyncio.new_event_loop()
    fut = loop.create_future()
    return AskJob("1", "sess", "hello", "gemini", fut, 120.0)


def test_plan_opens_fresh_chat_first():
    # stateless invariant: every ask starts its own fresh chat, inside the job
    cmds = GeminiPlanner().plan(make_job())
    ops = [c.op for c in cmds]
    assert ops == [
        "goto",
        "wait_idle",
        "clear_input",
        "type",
        "submit",
        "wait_generating",
        "wait_answer",
    ]
    assert cmds[0].args["url"] == GEMINI_URL


def test_recovery_plan_also_opens_fresh_chat():
    cmds = GeminiPlanner().plan(make_job(), recovery=True)
    assert cmds[0].op == "goto"
    assert cmds[0].args["url"] == GEMINI_URL
    assert cmds[1].op == "wait_idle"
```

Create `tests/test_chatgpt_planner.py` — identical shape with `ChatGPTPlanner`, `CHATGPT_URL` (from `ai_router.adapters.chatgpt.planner` / `ai_router.adapters.chatgpt.selectors`) and `provider_id "chatgpt"` in the job:

```python
from ai_router.adapters.chatgpt.planner import ChatGPTPlanner
from ai_router.adapters.chatgpt.selectors import CHATGPT_URL
from ai_router.browser.page_queue import AskJob


def make_job() -> AskJob:
    import asyncio

    loop = asyncio.new_event_loop()
    fut = loop.create_future()
    return AskJob("1", "sess", "hello", "chatgpt", fut, 300.0)


def test_plan_opens_fresh_chat_first():
    cmds = ChatGPTPlanner().plan(make_job())
    ops = [c.op for c in cmds]
    assert ops == [
        "goto",
        "wait_idle",
        "clear_input",
        "type",
        "submit",
        "wait_generating",
        "wait_answer",
    ]
    assert cmds[0].args["url"] == CHATGPT_URL


def test_recovery_plan_also_opens_fresh_chat():
    cmds = ChatGPTPlanner().plan(make_job(), recovery=True)
    assert cmds[0].op == "goto"
    assert cmds[0].args["url"] == CHATGPT_URL
    assert cmds[1].op == "wait_idle"
```

- [ ] **Step 2: Write the failing executor test**

Create `tests/test_command_executor.py`:

```python
from __future__ import annotations

import re

from ai_router.browser.commands import Command, CommandExecutor
from ai_router.browser.profile import ProviderProfile, ProviderSelectors, StreamDone
from ai_router.browser.state import StateReducer


class FakePage:
    def __init__(self) -> None:
        self.gotos: list[str] = []

    async def goto(self, url: str, wait_until: str = "domcontentloaded") -> None:
        self.gotos.append(url)


def make_profile(snapshots: list[tuple[int, str]]) -> ProviderProfile:
    async def read_response_snapshot(page) -> tuple[int, str]:
        return snapshots.pop(0) if len(snapshots) > 1 else snapshots[0]

    async def is_stop_visible(page) -> bool:
        return False

    async def submit_ready(page) -> bool:
        return True

    return ProviderProfile(
        provider_id="fake",
        stream_url_re=re.compile("/stream"),
        parse_stream_done=lambda status, body: StreamDone(done=False, ok=True),
        is_stop_visible=is_stop_visible,
        read_response_snapshot=read_response_snapshot,
        is_rate_limited=lambda text: False,
        submit_ready=submit_ready,
        planner=None,
        selectors=ProviderSelectors(prompt_input="#in", submit_button="#btn"),
        error_markers=(),
        recoverable_codes=(),
    )


async def test_goto_rebases_before_count():
    # The old chat shows 2 responses when run() starts; after the goto the
    # fresh chat shows 0. The answer (count 1) must satisfy new_response
    # against the POST-goto baseline — with the stale baseline (2) the
    # answer would never be "ready" and wait_answer would time out.
    page = FakePage()
    profile = make_profile(snapshots=[(2, "old"), (0, ""), (1, "answer")])
    reducer = StateReducer(
        page_id="t",
        stream_url_res=[profile.stream_url_re],
        idle_streak_required=1,
        generating_streak_required=1,
        answer_stable_ticks=1,
        stream_quiet_s=0.0,
        error_markers=(),
    )
    # a finished turn in the fresh chat: generating seen, then stable answer
    reducer.apply_dom_tick(generating=True, response_count=0, response_text="", error_text=None)
    reducer.apply_dom_tick(generating=False, response_count=1, response_text="answer", error_text=None)
    reducer.apply_dom_tick(generating=False, response_count=1, response_text="answer", error_text=None)
    ex = CommandExecutor(
        page,
        reducer,
        profile=profile,
        job_id="j",
        page_id="t",
        answer_timeout_s=2.0,
        idle_streak_required=1,
    )
    answer = await ex.run([Command("goto", {"url": "https://x/home"}), Command("wait_answer")])
    assert answer == "answer"
    assert page.gotos == ["https://x/home"]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `poetry run pytest tests/test_gemini_planner.py tests/test_chatgpt_planner.py tests/test_command_executor.py -q`
Expected: planner tests FAIL (ops start with `wait_idle`, no goto); executor test FAILS after ~2 s with `TimeoutError_` ("State polling timed out…").

- [ ] **Step 4: Implement**

Replace `src/ai_router/adapters/gemini/planner.py` with:

```python
from ai_router.adapters.gemini.selectors import GEMINI_URL
from ai_router.browser.commands import Command
from ai_router.browser.page_queue import AskJob


class GeminiPlanner:
    def plan(self, job: AskJob, *, recovery: bool = False) -> list[Command]:
        # The fresh-chat goto runs INSIDE the job — after the worker's idle
        # gate — so it can never navigate a tab that is still generating a
        # previous answer. Recovery uses the same script: reload + retry.
        return [
            Command("goto", {"url": GEMINI_URL}),
            Command("wait_idle"),
            *self._core(job),
        ]

    def _core(self, job: AskJob) -> list[Command]:
        return [
            Command("clear_input"),
            Command("type", {"prompt": job.prompt}),
            Command("submit"),
            Command("wait_generating"),
            Command("wait_answer"),
        ]
```

Replace `src/ai_router/adapters/chatgpt/planner.py` identically, with `CHATGPT_URL` from `ai_router.adapters.chatgpt.selectors` and class name `ChatGPTPlanner`.

In `src/ai_router/browser/commands.py`, replace the `goto` branch in `run()`:

```python
            elif cmd.op == "goto":
                await self._page.goto(cmd.args["url"], wait_until="domcontentloaded")
```

with:

```python
            elif cmd.op == "goto":
                await self._page.goto(cmd.args["url"], wait_until="domcontentloaded")
                # A goto opens a fresh chat and resets the response list —
                # rebase the baseline so wait_answer compares against the
                # NEW chat, not the previous one.
                before_count, _ = await self._profile.read_response_snapshot(self._page)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `poetry run pytest tests/test_gemini_planner.py tests/test_chatgpt_planner.py tests/test_command_executor.py -q` then `poetry run pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/ai_router/adapters/gemini/planner.py src/ai_router/adapters/chatgpt/planner.py src/ai_router/browser/commands.py tests/test_gemini_planner.py tests/test_chatgpt_planner.py tests/test_command_executor.py
git commit -m "feat: open fresh chat inside the job plan; rebase before_count on goto"
```

---

### Task 3: `handle_ask` routes through `PageRouter`

**Files:**
- Modify: `src/ai_router/mcp/tools.py` (`AppState`, `create_app_state`, `ensure_worker`, `handle_ask`)
- Modify: `tests/test_tools_stateless.py` (replace content)

**Interfaces:**
- Consumes: `PageRouter(browser, max_pages)`, `page_for(adapter)` from Task 1.
- Produces (used by Tasks 4, 6):
  - `AppState` gains field `page_router: PageRouter`.
  - `ensure_worker(state, page, default_provider: str | None = None) -> PageWorker` — worker's initial profile matches the tab's provider (correct DOM snapshots from the first tick).
  - `handle_ask` unchanged signature/response; no longer calls `browser.new_page()` / `ensure_page_ready` — `page_for` handles tab + warm + login.

- [ ] **Step 1: Write the failing tests**

Replace `tests/test_tools_stateless.py` with:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_tools_stateless.py -q`
Expected: FAIL — `AppState` has no `page_router` field (TypeError on fixture / AttributeError).

- [ ] **Step 3: Implement in `src/ai_router/mcp/tools.py`**

Add import: `from ai_router.browser.page_router import PageRouter`. Remove the now-unused `NotLoggedInError` import (the router raises it). Keep `SessionStatus` (used by `handle_session_status`).

`AppState` — add the field:

```python
@dataclass
class AppState:
    config: AppConfig
    registry: ProviderRegistry
    browser: BrowserManager
    page_queues: PageQueueRegistry
    page_workers: dict[str, PageWorker]
    profiles: dict[str, ProviderProfile]
    page_router: PageRouter
```

`create_app_state` — build the router on the same browser instance:

```python
    browser = BrowserManager(cfg)
    return AppState(
        config=cfg,
        registry=registry,
        browser=browser,
        page_queues=PageQueueRegistry(),
        page_workers={},
        profiles=profiles,
        page_router=PageRouter(browser, cfg.max_pages),
    )
```

`ensure_worker` — accept the tab's provider:

```python
def ensure_worker(
    state: AppState, page, default_provider: str | None = None
) -> PageWorker:
    pid = page_id_of(page)
    if pid not in state.page_workers:
        if len(state.page_workers) >= state.config.max_pages:
            raise AiRouterError("BROWSER_BUSY", "Maximum page workers reached")
        queue = state.page_queues.queue_for(page)
        worker = PageWorker(
            page,
            queue,
            state.config,
            state.profiles,
            default_provider or state.config.default_provider,
        )
        worker.start()
        state.page_workers[pid] = worker
        trace("worker_created", page_id=pid, worker_count=len(state.page_workers))
    return state.page_workers[pid]
```

`handle_ask` — replace the page/session block (`tools.py:87-96`):

```python
    try:
        page = await state.browser.new_page()
        if hasattr(adapter, "ensure_page_ready"):
            status = await adapter.ensure_page_ready(page)
        else:
            status = await adapter.check_session(page)
        if status == SessionStatus.LOGGED_OUT:
            raise NotLoggedInError()

        worker = ensure_worker(state, page)
```

with:

```python
    try:
        page = await state.page_router.page_for(adapter)
        worker = ensure_worker(state, page, default_provider=adapter.id)
```

Everything from `loop = asyncio.get_running_loop()` down is unchanged.

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/ai_router/mcp/tools.py tests/test_tools_stateless.py
git commit -m "feat: handle_ask routes through PageRouter pinned tabs"
```

---

### Task 4: `handle_session_status` uses a dedicated status tab

After pinning, `browser.new_page()` returns `pages[0]` — Gemini's pinned tab. A session check navigates, which would kill an in-flight ask. Use the router's status tab.

**Files:**
- Modify: `src/ai_router/mcp/tools.py:164` (`handle_session_status`)
- Test: `tests/test_tools_stateless.py` (append)

**Interfaces:**
- Consumes: `PageRouter.status_page()` (Task 1), `FakeAdapter.check_calls` (Task 3 fake already records it).
- Produces: `handle_session_status` — same signature/response, different page source.

- [ ] **Step 1: Write the failing test** (append to `tests/test_tools_stateless.py`)

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_tools_stateless.py::test_session_status_never_touches_pinned_tabs -q`
Expected: FAIL — `browser.new_page()` doesn't exist on `FakeBrowser` (AttributeError) or the check page IS the pinned tab.

- [ ] **Step 3: Implement**

In `handle_session_status`, replace:

```python
    page = await state.browser.new_page()
```

with:

```python
    # Session checks navigate — use a dedicated status tab so they never
    # touch a pinned provider tab that may be mid-generation.
    page = await state.page_router.status_page()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/ai_router/mcp/tools.py tests/test_tools_stateless.py
git commit -m "feat: session_status checks on a dedicated status tab"
```

---

### Task 5: Config — `parallel_ask` block

**Files:**
- Modify: `src/ai_router/config.py`
- Test: `tests/test_config.py` (append)

**Interfaces:**
- Produces (used by Task 6): `AppConfig.parallel_default_providers: list[str]` (default `[]` = all available providers) and `AppConfig.parallel_default_strategy: str` (default `"all"`), loadable from `~/.ai-router/config.yaml`:

```yaml
parallel_ask:
  default_providers:
    - gemini
    - chatgpt
  default_strategy: all
```

No env vars (spec: not required for phase 1).

- [ ] **Step 1: Write the failing tests** (append to `tests/test_config.py`)

```python
def test_parallel_ask_defaults(tmp_path):
    cfg = load_config(tmp_path / "missing.yaml")
    assert cfg.parallel_default_providers == []
    assert cfg.parallel_default_strategy == "all"


def test_parallel_ask_from_yaml(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(
        "parallel_ask:\n"
        "  default_providers:\n"
        "    - gemini\n"
        "    - chatgpt\n"
        "  default_strategy: longest\n",
        encoding="utf-8",
    )
    cfg = load_config(p)
    assert cfg.parallel_default_providers == ["gemini", "chatgpt"]
    assert cfg.parallel_default_strategy == "longest"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_config.py -q`
Expected: FAIL — `AppConfig` has no attribute `parallel_default_providers`.

- [ ] **Step 3: Implement**

In `AppConfig`, after `max_pages: int = 10`:

```python
    # ask_multi fan-out defaults; empty list = all "available" providers
    parallel_default_providers: list[str] = field(default_factory=list)
    parallel_default_strategy: str = "all"
```

In `load_config`, inside the `if config_path.exists():` block, after the `providers` handling:

```python
        if "parallel_ask" in raw:
            pa = raw["parallel_ask"] or {}
            if "default_providers" in pa:
                cfg.parallel_default_providers = [str(p) for p in pa["default_providers"]]
            if "default_strategy" in pa:
                cfg.parallel_default_strategy = str(pa["default_strategy"])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_config.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/ai_router/config.py tests/test_config.py
git commit -m "feat: parallel_ask config block (default providers + strategy)"
```

---

### Task 6: `handle_ask_multi` — fan-out + strategies

**Files:**
- Modify: `src/ai_router/mcp/tools.py` (add `import time`, add `handle_ask_multi`)
- Test: `tests/test_ask_multi.py`

**Interfaces:**
- Consumes: `handle_ask` (Task 3), `AppConfig.parallel_default_providers` / `parallel_default_strategy` (Task 5).
- Produces (used by Task 7):

```python
async def handle_ask_multi(
    state: AppState,
    *,
    prompt: str,
    providers: list[str] | None = None,
    strategy: str | None = None,
    mcp_session_id: str | None,
) -> dict: ...
```

Returns `{"answers": [{"provider", "answer", "duration_s", "routing_reason", "error"}, ...], "selected": <entry dict or None>}`. Entries keep input order. `strategy="all"` → `selected` is `None`; `"first"` → earliest finisher among non-error entries; `"longest"` → longest `answer` among non-error entries; all-error → `selected` is `None`, never raises. Unknown strategy → `AiRouterError("INVALID_STRATEGY")`. Unknown provider id in the list → entry with `error: "UNKNOWN_PROVIDER"` (registry raises it; the wrapper catches it).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ask_multi.py`:

```python
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ai_router.adapters.base import SessionStatus
from ai_router.browser.page_router import PageRouter
from ai_router.config import AppConfig
from ai_router.errors import AiRouterError
from ai_router.mcp import tools
from ai_router.mcp.tools import create_app_state, handle_ask_multi


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
    def __init__(self, provider_id: str) -> None:
        self.id = provider_id
        self.name = provider_id
        self.status = "available"
        self.session_status = SessionStatus.LOGGED_IN
        self.ensure_calls: list[FakePage] = []

    async def ensure_page_ready(self, page: FakePage) -> SessionStatus:
        self.ensure_calls.append(page)
        page.url = f"https://{self.id}.example/app"
        return self.session_status


class SlowFakeWorker:
    delays: dict[str, float] = {}
    answers: dict[str, str] = {}

    def __init__(self, page, queue, config, profiles, default_provider) -> None:
        pass

    def start(self) -> None:
        pass

    async def enqueue(self, job) -> None:
        await asyncio.sleep(self.delays.get(job.provider_id, 0))
        job.future.set_result(
            self.answers.get(job.provider_id, f"answer:{job.provider_id}")
        )


@pytest.fixture()
def state(monkeypatch):
    SlowFakeWorker.delays = {}
    SlowFakeWorker.answers = {}
    monkeypatch.setattr(tools, "PageWorker", SlowFakeWorker)
    st = create_app_state(make_config())
    st.browser = FakeBrowser()
    st.page_router = PageRouter(st.browser, max_pages=10)
    return st


@pytest.fixture()
def adapters(monkeypatch):
    fakes = {"gemini": FakeAdapter("gemini"), "chatgpt": FakeAdapter("chatgpt")}

    def fake_resolve(registry, provider, default):
        reason = "explicit param" if provider else "default provider"
        return fakes[provider or default], reason

    monkeypatch.setattr(tools, "resolve_provider", fake_resolve)
    return fakes


async def test_all_strategy_returns_all_selected_none(state, adapters) -> None:
    res = await handle_ask_multi(
        state,
        prompt="hi",
        providers=["gemini", "chatgpt"],
        strategy="all",
        mcp_session_id=None,
    )
    assert [e["provider"] for e in res["answers"]] == ["gemini", "chatgpt"]
    assert all(e["error"] is None for e in res["answers"])
    assert all(e["answer"] for e in res["answers"])
    assert res["selected"] is None


async def test_default_providers_are_all_available(state, adapters) -> None:
    # providers omitted + empty config list → every "available" adapter
    res = await handle_ask_multi(
        state, prompt="hi", providers=None, strategy="all", mcp_session_id=None
    )
    assert sorted(e["provider"] for e in res["answers"]) == ["chatgpt", "gemini"]


async def test_first_selects_earliest_finisher(state, adapters) -> None:
    SlowFakeWorker.delays = {"gemini": 0.0, "chatgpt": 0.05}
    res = await handle_ask_multi(
        state,
        prompt="hi",
        providers=["gemini", "chatgpt"],
        strategy="first",
        mcp_session_id=None,
    )
    assert res["selected"]["provider"] == "gemini"
    assert len(res["answers"]) == 2


async def test_longest_selects_longest_answer(state, adapters) -> None:
    SlowFakeWorker.answers = {"gemini": "short", "chatgpt": "a much longer answer"}
    res = await handle_ask_multi(
        state,
        prompt="hi",
        providers=["gemini", "chatgpt"],
        strategy="longest",
        mcp_session_id=None,
    )
    assert res["selected"]["provider"] == "chatgpt"


async def test_partial_failure_keeps_other_answer(state, adapters) -> None:
    adapters["chatgpt"].session_status = SessionStatus.LOGGED_OUT
    res = await handle_ask_multi(
        state,
        prompt="hi",
        providers=["gemini", "chatgpt"],
        strategy="all",
        mcp_session_id=None,
    )
    by = {e["provider"]: e for e in res["answers"]}
    assert by["gemini"]["error"] is None
    assert by["chatgpt"]["error"] == "NOT_LOGGED_IN"
    assert by["chatgpt"]["answer"] is None


async def test_all_fail_returns_entries_never_raises(state, adapters) -> None:
    adapters["gemini"].session_status = SessionStatus.LOGGED_OUT
    adapters["chatgpt"].session_status = SessionStatus.LOGGED_OUT
    res = await handle_ask_multi(
        state,
        prompt="hi",
        providers=["gemini", "chatgpt"],
        strategy="first",
        mcp_session_id=None,
    )
    assert all(e["error"] == "NOT_LOGGED_IN" for e in res["answers"])
    assert res["selected"] is None


async def test_invalid_strategy_raises(state, adapters) -> None:
    with pytest.raises(AiRouterError) as ei:
        await handle_ask_multi(
            state, prompt="hi", providers=None, strategy="best", mcp_session_id=None
        )
    assert ei.value.code == "INVALID_STRATEGY"


async def test_fanout_runs_concurrently(state, monkeypatch) -> None:
    # both warm-ups must be in flight at the same time to pass the barrier;
    # a sequential fan-out deadlocks and trips the 2 s timeout instead
    barrier = asyncio.Barrier(2)

    class BarrierAdapter(FakeAdapter):
        async def ensure_page_ready(self, page: FakePage) -> SessionStatus:
            await asyncio.wait_for(barrier.wait(), timeout=2)
            return await super().ensure_page_ready(page)

    fakes = {"gemini": BarrierAdapter("gemini"), "chatgpt": BarrierAdapter("chatgpt")}
    monkeypatch.setattr(
        tools,
        "resolve_provider",
        lambda registry, provider, default: (fakes[provider], "explicit param"),
    )
    res = await handle_ask_multi(
        state,
        prompt="hi",
        providers=["gemini", "chatgpt"],
        strategy="all",
        mcp_session_id=None,
    )
    assert all(e["error"] is None for e in res["answers"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_ask_multi.py -q`
Expected: FAIL — `ImportError: cannot import name 'handle_ask_multi'`.

- [ ] **Step 3: Implement in `src/ai_router/mcp/tools.py`**

Add `import time` to the imports. Add after `handle_ask`:

```python
async def handle_ask_multi(
    state: AppState,
    *,
    prompt: str,
    providers: list[str] | None = None,
    strategy: str | None = None,
    mcp_session_id: str | None,
) -> dict:
    chosen = strategy or state.config.parallel_default_strategy
    if chosen not in ("all", "first", "longest"):
        raise AiRouterError("INVALID_STRATEGY", f"Unknown strategy: {chosen}")
    ids = list(
        providers
        or state.config.parallel_default_providers
        or [a.id for a in state.registry.list_all() if a.status == "available"]
    )
    if not ids:
        raise AiRouterError("NO_PROVIDERS", "No providers available for ask_multi")

    async def _one(pid: str) -> tuple[dict, float]:
        started = time.monotonic()
        try:
            res = await handle_ask(
                state, prompt=prompt, provider=pid, mcp_session_id=mcp_session_id
            )
            elapsed = time.monotonic() - started
            entry = {
                "provider": res["provider"],
                "answer": res["answer"],
                "duration_s": round(elapsed, 1),
                "routing_reason": res["routing_reason"],
                "error": None,
            }
        except AiRouterError as exc:
            elapsed = time.monotonic() - started
            trace("ask_multi_provider_error", provider=pid, code=exc.code)
            entry = {
                "provider": pid,
                "answer": None,
                "duration_s": round(elapsed, 1),
                "routing_reason": "explicit param",
                "error": exc.code,
            }
        return entry, elapsed

    trace("ask_multi_fanout", providers=",".join(ids), strategy=chosen)
    results = await asyncio.gather(*(_one(pid) for pid in ids))
    answers = [entry for entry, _ in results]
    ok = [(entry, took) for entry, took in results if entry["error"] is None]
    selected = None
    if chosen == "first" and ok:
        selected = min(ok, key=lambda item: item[1])[0]
    elif chosen == "longest" and ok:
        selected = max(ok, key=lambda item: len(item[0]["answer"]))[0]
    return {"answers": answers, "selected": selected}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_ask_multi.py -q` then `poetry run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/ai_router/mcp/tools.py tests/test_ask_multi.py
git commit -m "feat: handle_ask_multi parallel fan-out with all/first/longest strategies"
```

---

### Task 7: MCP tool `ask_multi` + lint + manual smoke

**Files:**
- Modify: `src/ai_router/mcp/server.py`

**Interfaces:**
- Consumes: `handle_ask_multi` (Task 6).
- Produces: MCP tool `ask_multi(prompt, providers?, strategy?)` on the FastMCP app, same error-wrapping convention as `ask`.

- [ ] **Step 1: Register the tool**

In `src/ai_router/mcp/server.py`, add `handle_ask_multi` to the `ai_router.mcp.tools` import list, then add after the `ask` tool inside `create_mcp_app`:

```python
    @mcp.tool()
    async def ask_multi(
        ctx: Context,
        prompt: str,
        providers: list[str] | None = None,
        strategy: str | None = None,
    ) -> dict:
        """Send one prompt to several providers in parallel; return every answer.

        strategy: "all" (default; selected=null, client compares),
        "first" (earliest finisher), "longest" (longest non-error answer).
        """
        try:
            return await handle_ask_multi(
                _state,
                prompt=prompt,
                providers=providers,
                strategy=strategy,
                mcp_session_id=_mcp_session_id(ctx),
            )
        except AiRouterError as exc:
            raise RuntimeError(f"[{exc.code}] {exc.message}") from exc
```

- [ ] **Step 2: Full suite + lint**

Run: `poetry run pytest -q` and `poetry run ruff check src tests`
Expected: all tests pass; no lint errors.

- [ ] **Step 3: Commit**

```bash
git add src/ai_router/mcp/server.py
git commit -m "feat: ask_multi MCP tool"
```

- [ ] **Step 4: Manual integration smoke (headed browser, logged-in profile required)**

1. `poetry run ai browser login` — log in to BOTH gemini.google.com and chatgpt.com in the opened profile.
2. `poetry run ai serve`
3. From an MCP client: `ask_multi` with a short prompt → expect 2 answer entries; wall-clock ≈ max(gemini, chatgpt), NOT their sum; two tabs visible (one per provider) plus no stray blank tab.
4. `ask` with `provider="gemini"`, then `provider="chatgpt"` — no regression; each ask opens a fresh chat (watch the tab navigate home).
5. Two `ask` calls to the SAME provider back-to-back — second waits for the first (FIFO), first answer is NOT clobbered.
6. `session_status` while idle — returns statuses without disturbing pinned tabs.

Record results; fix anything found before merging.
