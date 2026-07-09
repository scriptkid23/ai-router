# Browser Event Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace direct `GeminiAdapter.ask()` polling with a per-page event channel, state reducer, job queue, and command executor so asks only run when the browser is truly idle.

**Architecture:** Each Playwright `Page` gets a `PageWorker` coroutine. Raw Playwright events flow into `EventChannel`; `StateReducer` derives `BrowserState`. MCP `handle_ask` enqueues `AskJob` + awaits `Future`. Worker dequeues only when `idle_streak >= threshold`. `GeminiPlanner` emits a `CommandScript`; `CommandExecutor` runs it with state-gated waits.

**Tech Stack:** Python 3.11+, asyncio, Playwright (via cloakbrowser), pytest, pytest-asyncio

**Spec:** `docs/superpowers/specs/2026-07-09-browser-event-queue-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/ai_router/config.py` | Modify | Add browser queue tuning fields |
| `src/ai_router/errors.py` | Modify | Add `GeminiServiceError`, `SubmitFailedError` |
| `src/ai_router/browser/events.py` | Create | `BrowserEvent`, `EventChannel`, listener setup |
| `src/ai_router/browser/state.py` | Create | `BrowserState`, `StateReducer`, `StateSnapshot` |
| `src/ai_router/browser/page_queue.py` | Create | `AskJob`, `PageQueue`, `PageQueueRegistry` |
| `src/ai_router/browser/commands.py` | Create | `Command`, `CommandExecutor` |
| `src/ai_router/browser/page_worker.py` | Create | `PageWorker` loop |
| `src/ai_router/browser/manager.py` | Modify | Remove `acquire()` from ask path (keep for optional use) |
| `src/ai_router/adapters/gemini/planner.py` | Create | `GeminiPlanner.plan()` → command list |
| `src/ai_router/adapters/gemini/selectors.py` | Modify | Extend `SEL_GENERATING`, add `GEMINI_ERROR_MARKERS` |
| `src/ai_router/adapters/gemini/adapter.py` | Modify | Slim: session/open/resume only; remove `_ask_once` |
| `src/ai_router/adapters/gemini/wait.py` | Modify | Keep `braces_balanced`, `is_rate_limited`; remove DOM poll |
| `src/ai_router/session/manager.py` | Modify | Start/stop worker via registry |
| `src/ai_router/mcp/tools.py` | Modify | `handle_ask` enqueue + await future |
| `tests/test_state_reducer.py` | Create | Reducer phase transitions |
| `tests/test_page_queue.py` | Create | FIFO + future |
| `tests/test_gemini_planner.py` | Create | Command script shape |
| `tests/test_command_waits.py` | Create | wait_answer guard logic (unit, no browser) |

---

### Task 1: Config fields for browser queue

**Files:**
- Modify: `src/ai_router/config.py`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_config.py — append

def test_browser_queue_defaults():
    from ai_router.config import load_config
    cfg = load_config()
    assert cfg.idle_streak_required == 6
    assert cfg.generating_streak_required == 2
    assert cfg.answer_stable_ticks == 4
    assert cfg.dom_tick_interval_ms == 500
    assert cfg.max_pages == 10
```

- [ ] **Step 2: Run test to verify it fails**

```bash
poetry run pytest tests/test_config.py::test_browser_queue_defaults -v
```

Expected: FAIL — `AppConfig` has no attribute `idle_streak_required`

- [ ] **Step 3: Add fields to AppConfig**

```python
# src/ai_router/config.py — add to AppConfig dataclass
idle_streak_required: int = 6
generating_streak_required: int = 2
answer_stable_ticks: int = 4
dom_tick_interval_ms: int = 500
max_pages: int = 10
```

Also add YAML/env overrides in `load_config()` for `idle_streak_required` via `AI_ROUTER_IDLE_STREAK_REQUIRED` (optional; defaults suffice for v1).

- [ ] **Step 4: Run test**

```bash
poetry run pytest tests/test_config.py -v
```

Expected: PASS

---

### Task 2: BrowserEvent + EventChannel

**Files:**
- Create: `src/ai_router/browser/events.py`

- [ ] **Step 1: Create events module**

```python
# src/ai_router/browser/events.py
from __future__ import annotations

import asyncio
import time
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


async def attach_listeners(page: Page, channel: EventChannel) -> None:
    def on_request_finished(request) -> None:
        asyncio.get_event_loop().create_task(
            channel.emit("request_finished", url=request.url)
        )

    def on_framenavigated(frame) -> None:
        if frame == page.main_frame:
            asyncio.get_event_loop().create_task(
                channel.emit("framenavigated", url=frame.url)
            )

    page.on("requestfinished", on_request_finished)
    page.on("framenavigated", on_framenavigated)


async def dom_tick_loop(
    page: Page,
    channel: EventChannel,
    *,
    interval_ms: int,
    poll_fn,
    stop_event: asyncio.Event,
) -> None:
    """poll_fn: async callable returning dict snapshot for dom_tick payload."""
    while not stop_event.is_set():
        snapshot = await poll_fn(page)
        await channel.emit("dom_tick", **snapshot)
        await asyncio.sleep(interval_ms / 1000)
```

- [ ] **Step 2: Verify import**

```bash
poetry run python -c "from ai_router.browser.events import EventChannel; print('ok')"
```

Expected: `ok`

---

### Task 3: BrowserState + StateReducer (TDD)

**Files:**
- Create: `src/ai_router/browser/state.py`
- Create: `tests/test_state_reducer.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_state_reducer.py
import time
from ai_router.browser.state import StateReducer, BrowserState


def test_idle_after_quiet_dom_ticks():
    r = StateReducer(idle_streak_required=3, generating_streak_required=2)
    for _ in range(3):
        r.apply_dom_tick(generating=False, response_count=0, response_text="", error_text=None)
    assert r.state.phase == "idle"
    assert r.state.idle_streak == 3


def test_generating_when_stop_visible():
    r = StateReducer(idle_streak_required=3, generating_streak_required=2)
    r.apply_dom_tick(generating=True, response_count=0, response_text="", error_text=None)
    r.apply_dom_tick(generating=True, response_count=0, response_text="", error_text=None)
    assert r.state.phase == "generating"
    assert r.state.generating_streak == 2


def test_error_on_1095_marker():
    r = StateReducer(idle_streak_required=3, generating_streak_required=2)
    r.apply_dom_tick(
        generating=False,
        response_count=0,
        response_text="",
        error_text="Something went wrong (1095)",
    )
    assert r.state.phase == "error"


def test_stream_generate_sets_timestamp():
    r = StateReducer(idle_streak_required=3, generating_streak_required=2)
    before = time.time()
    r.apply_request_finished(
        "https://gemini.google.com/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate"
    )
    assert r.state.last_stream_at is not None
    assert r.state.last_stream_at >= before
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
poetry run pytest tests/test_state_reducer.py -v
```

- [ ] **Step 3: Implement StateReducer**

```python
# src/ai_router/browser/state.py
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Literal

STREAM_GENERATE_RE = re.compile(
    r"assistant\.lamda\.BardFrontendService/StreamGenerate", re.I
)

Phase = Literal["idle", "submitting", "generating", "error", "closed"]


@dataclass
class BrowserState:
    phase: Phase = "idle"
    generating_streak: int = 0
    idle_streak: int = 0
    last_stream_at: float | None = None
    error_text: str | None = None
    response_count: int = 0
    last_response_text: str = ""
    response_stable_streak: int = 0
    saw_generating_this_job: bool = False


class StateReducer:
    def __init__(
        self,
        *,
        idle_streak_required: int,
        generating_streak_required: int,
        answer_stable_ticks: int,
        error_markers: tuple[str, ...],
    ) -> None:
        self._idle_required = idle_streak_required
        self._gen_required = generating_streak_required
        self._answer_stable = answer_stable_ticks
        self._error_markers = error_markers
        self.state = BrowserState()

    def mark_submitting(self) -> None:
        self.state.phase = "submitting"
        self.state.saw_generating_this_job = False

    def mark_closed(self) -> None:
        self.state.phase = "closed"

    def reset_job_cycle(self) -> None:
        self.state.saw_generating_this_job = False
        self.state.response_stable_streak = 0

    def apply_request_finished(self, url: str) -> None:
        if STREAM_GENERATE_RE.search(url):
            self.state.last_stream_at = time.time()

    def apply_dom_tick(
        self,
        *,
        generating: bool,
        response_count: int,
        response_text: str,
        error_text: str | None,
    ) -> None:
        st = self.state
        if error_text and any(m in error_text.lower() for m in self._error_markers):
            st.phase = "error"
            st.error_text = error_text
            return

        st.response_count = response_count
        if response_text and response_text == st.last_response_text:
            st.response_stable_streak += 1
        else:
            st.last_response_text = response_text
            st.response_stable_streak = 1 if response_text else 0

        if generating:
            st.generating_streak += 1
            st.idle_streak = 0
            st.phase = "generating"
            if st.generating_streak >= self._gen_required:
                st.saw_generating_this_job = True
        else:
            st.idle_streak += 1
            st.generating_streak = 0
            if st.idle_streak >= self._idle_required:
                st.phase = "idle"

    def answer_ready(self, *, before_count: int) -> bool:
        st = self.state
        return (
            st.saw_generating_this_job
            and st.response_count > before_count
            and st.phase == "idle"
            and st.idle_streak >= self._idle_required
            and st.response_stable_streak >= self._answer_stable
            and bool(st.last_response_text)
        )
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
poetry run pytest tests/test_state_reducer.py -v
```

---

### Task 4: AskJob + PageQueue + PageQueueRegistry (TDD)

**Files:**
- Create: `src/ai_router/browser/page_queue.py`
- Create: `tests/test_page_queue.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_page_queue.py
import asyncio
import pytest
from ai_router.browser.page_queue import AskJob, PageQueue


@pytest.mark.asyncio
async def test_fifo_order():
    q = PageQueue()
    loop = asyncio.get_running_loop()
    f1 = loop.create_future()
    f2 = loop.create_future()
    await q.put(AskJob("j1", "s1", "p1", "gemini", f1, 120.0))
    await q.put(AskJob("j2", "s2", "p2", "gemini", f2, 120.0))
    j1 = await q.get()
    j2 = await q.get()
    assert j1.job_id == "j1"
    assert j2.job_id == "j2"


@pytest.mark.asyncio
async def test_future_resolve():
    q = PageQueue()
    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    job = AskJob("j1", "s1", "hello", "gemini", fut, 120.0)
    await q.put(job)
    got = await q.get()
    got.future.set_result("42")
    assert await fut == "42"
```

- [ ] **Step 2: Run — expect FAIL**

```bash
poetry run pytest tests/test_page_queue.py -v
```

- [ ] **Step 3: Implement**

```python
# src/ai_router/browser/page_queue.py
from __future__ import annotations

import asyncio
import uuid
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
```

- [ ] **Step 4: Run — expect PASS**

```bash
poetry run pytest tests/test_page_queue.py -v
```

---

### Task 5: Command + CommandExecutor

**Files:**
- Create: `src/ai_router/browser/commands.py`
- Create: `tests/test_command_waits.py`

- [ ] **Step 1: Write failing test for answer_ready guard**

```python
# tests/test_command_waits.py
from ai_router.browser.state import StateReducer


def test_answer_not_ready_without_generating_phase():
    r = StateReducer(
        idle_streak_required=3,
        generating_streak_required=2,
        answer_stable_ticks=2,
        error_markers=("something went wrong",),
    )
    for _ in range(3):
        r.apply_dom_tick(generating=False, response_count=1, response_text="hi", error_text=None)
    assert r.answer_ready(before_count=0) is False  # never saw generating


def test_answer_ready_after_full_cycle():
    r = StateReducer(
        idle_streak_required=2,
        generating_streak_required=1,
        answer_stable_ticks=2,
        error_markers=(),
    )
    r.apply_dom_tick(generating=True, response_count=0, response_text="", error_text=None)
    r.apply_dom_tick(generating=False, response_count=1, response_text="answer", error_text=None)
    r.apply_dom_tick(generating=False, response_count=1, response_text="answer", error_text=None)
    assert r.answer_ready(before_count=0) is True
```

- [ ] **Step 2: Run — expect FAIL or PASS after Task 3**

- [ ] **Step 3: Implement CommandExecutor skeleton**

```python
# src/ai_router/browser/commands.py
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Literal

from playwright.async_api import Page

from ai_router.adapters.gemini.selectors import (
    SEL_GENERATING,
    SEL_PROMPT_INPUT,
    SEL_RESPONSE_BLOCK,
    SEL_SUBMIT_BUTTON,
)
from ai_router.browser.state import StateReducer
from ai_router.errors import AiRouterError, TimeoutError_

CommandOp = Literal[
    "wait_idle", "clear_input", "type", "submit",
    "wait_generating", "wait_answer", "goto",
]


@dataclass
class Command:
    op: CommandOp
    args: dict[str, Any] = field(default_factory=dict)


class CommandExecutor:
    def __init__(self, page: Page, reducer: StateReducer, *, answer_timeout_s: float) -> None:
        self._page = page
        self._reducer = reducer
        self._answer_timeout_s = answer_timeout_s

    async def run(self, commands: list[Command]) -> str:
        before_count = await self._page.locator(SEL_RESPONSE_BLOCK).count()
        for cmd in commands:
            if cmd.op == "wait_idle":
                await self._wait_phase("idle")
            elif cmd.op == "clear_input":
                await self._clear_input()
            elif cmd.op == "type":
                await self._type(cmd.args["prompt"])
            elif cmd.op == "submit":
                self._reducer.mark_submitting()
                await self._submit()
            elif cmd.op == "wait_generating":
                await self._wait_generating()
            elif cmd.op == "wait_answer":
                return await self._wait_answer(before_count=before_count)
            elif cmd.op == "goto":
                await self._page.goto(cmd.args["url"], wait_until="domcontentloaded")
        raise AiRouterError("ADAPTER_ERROR", "CommandScript missing wait_answer")

    async def _clear_input(self) -> None:
        box = self._page.locator(SEL_PROMPT_INPUT).first
        await box.click()
        await self._page.keyboard.press("Control+A")
        await self._page.keyboard.press("Backspace")

    async def _type(self, prompt: str) -> None:
        await self._page.keyboard.insert_text(prompt)

    async def _submit(self) -> None:
        submit = self._page.locator(SEL_SUBMIT_BUTTON).last
        await submit.click()

    async def _wait_generating(self) -> None:
        deadline = asyncio.get_event_loop().time() + 15
        while asyncio.get_event_loop().time() < deadline:
            if self._reducer.state.saw_generating_this_job:
                return
            if self._reducer.state.phase == "error":
                raise AiRouterError("GEMINI_ERROR", self._reducer.state.error_text or "error")
            await asyncio.sleep(0.1)
        raise AiRouterError("SUBMIT_FAILED", "Send click did not start generation")

    async def _wait_answer(self, *, before_count: int) -> str:
        deadline = asyncio.get_event_loop().time() + self._answer_timeout_s
        while asyncio.get_event_loop().time() < deadline:
            if self._reducer.answer_ready(before_count=before_count):
                return self._reducer.state.last_response_text
            if self._reducer.state.phase == "error":
                raise AiRouterError("GEMINI_ERROR", self._reducer.state.error_text or "error")
            await asyncio.sleep(0.1)
        raise TimeoutError_("DOM/state polling timed out waiting for stable answer")

    async def _wait_phase(self, phase: str) -> None:
        deadline = asyncio.get_event_loop().time() + 120
        while asyncio.get_event_loop().time() < deadline:
            if self._reducer.state.phase == phase:
                return
            await asyncio.sleep(0.1)
        raise TimeoutError_(f"Timed out waiting for phase={phase}")
```

- [ ] **Step 4: Add errors**

```python
# src/ai_router/errors.py — append as needed; SUBMIT_FAILED/GEMINI_ERROR use AiRouterError directly
```

- [ ] **Step 5: Run tests**

```bash
poetry run pytest tests/test_command_waits.py tests/test_state_reducer.py -v
```

---

### Task 6: GeminiPlanner + selectors

**Files:**
- Create: `src/ai_router/adapters/gemini/planner.py`
- Modify: `src/ai_router/adapters/gemini/selectors.py`
- Create: `tests/test_gemini_planner.py`

- [ ] **Step 1: Extend selectors**

```python
# selectors.py additions
GEMINI_ERROR_MARKERS = (
    "something went wrong",
    "(1095)", "(1096)", "(1097)",
)
SEL_GENERATING = (
    'button[aria-label*="Stop" i], '
    'button[aria-label*="Dừng" i], '
    'button[aria-label*="stop response" i], '
    'button[aria-label*="dừng" i]'
)
```

- [ ] **Step 2: Write planner test**

```python
# tests/test_gemini_planner.py
from ai_router.adapters.gemini.planner import GeminiPlanner
from ai_router.browser.page_queue import AskJob
import asyncio


def test_plan_returns_six_commands():
    loop = asyncio.new_event_loop()
    fut = loop.create_future()
    job = AskJob("1", "sess", "hello", "gemini", fut, 120.0)
    cmds = GeminiPlanner().plan(job)
    ops = [c.op for c in cmds]
    assert ops == ["wait_idle", "clear_input", "type", "submit", "wait_generating", "wait_answer"]
```

- [ ] **Step 3: Implement planner**

```python
# src/ai_router/adapters/gemini/planner.py
from ai_router.browser.commands import Command
from ai_router.browser.page_queue import AskJob


class GeminiPlanner:
    def plan(self, job: AskJob, *, recovery: bool = False) -> list[Command]:
        if recovery:
            return [
                Command("goto", {"url": "https://gemini.google.com/app"}),
                Command("wait_idle"),
                *self._core(job),
            ]
        return self._core(job)

    def _core(self, job: AskJob) -> list[Command]:
        return [
            Command("wait_idle"),
            Command("clear_input"),
            Command("type", {"prompt": job.prompt}),
            Command("submit"),
            Command("wait_generating"),
            Command("wait_answer"),
        ]
```

- [ ] **Step 4: Run**

```bash
poetry run pytest tests/test_gemini_planner.py -v
```

---

### Task 7: PageWorker + dom poll integration

**Files:**
- Create: `src/ai_router/browser/page_worker.py`

- [ ] **Step 1: Implement PageWorker**

```python
# src/ai_router/browser/page_worker.py
from __future__ import annotations

import asyncio

from playwright.async_api import Page

from ai_router.adapters.gemini.planner import GeminiPlanner
from ai_router.adapters.gemini.selectors import (
    GEMINI_ERROR_MARKERS,
    SEL_GENERATING,
    SEL_RESPONSE_BLOCK,
)
from ai_router.browser.commands import CommandExecutor
from ai_router.browser.events import EventChannel, attach_listeners, dom_tick_loop, page_id_of
from ai_router.browser.page_queue import AskJob, PageQueue
from ai_router.browser.state import StateReducer
from ai_router.config import AppConfig
from ai_router.errors import AiRouterError


class PageWorker:
    def __init__(self, page: Page, queue: PageQueue, cfg: AppConfig) -> None:
        self._page = page
        self._queue = queue
        self._cfg = cfg
        self._channel = EventChannel(page_id_of(page))
        self._reducer = StateReducer(
            idle_streak_required=cfg.idle_streak_required,
            generating_streak_required=cfg.generating_streak_required,
            answer_stable_ticks=cfg.answer_stable_ticks,
            error_markers=GEMINI_ERROR_MARKERS,
        )
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def enqueue(self, job: AskJob) -> None:
        await self._queue.put(job)

    async def _run(self) -> None:
        await attach_listeners(self._page, self._channel)
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
                if job.future.cancelled():
                    continue
                try:
                    answer = await self._execute_job(job)
                    if not job.future.done():
                        job.future.set_result(answer)
                except Exception as exc:
                    if not job.future.done():
                        job.future.set_exception(exc)
        finally:
            self._stop.set()
            tick_task.cancel()
            pump_task.cancel()

    async def _pump_events(self) -> None:
        while not self._stop.is_set():
            ev = await self._channel.get()
            if ev.kind == "request_finished":
                self._reducer.apply_request_finished(ev.payload.get("url", ""))
            elif ev.kind == "dom_tick":
                self._reducer.apply_dom_tick(
                    generating=ev.payload.get("generating", False),
                    response_count=ev.payload.get("response_count", 0),
                    response_text=ev.payload.get("response_text", ""),
                    error_text=ev.payload.get("error_text"),
                )

    async def _dom_snapshot(self, page: Page) -> dict:
        generating = await page.locator(SEL_GENERATING).count() > 0
        blocks = page.locator(SEL_RESPONSE_BLOCK)
        count = await blocks.count()
        text = ""
        if count:
            text = (await blocks.nth(count - 1).inner_text()).strip()
        body = ""
        try:
            body = (await page.locator("body").inner_text())[:2000]
        except Exception:
            pass
        err = None
        for m in GEMINI_ERROR_MARKERS:
            if m in body.lower():
                err = body[:200]
                break
        return {
            "generating": generating,
            "response_count": count,
            "response_text": text,
            "error_text": err,
        }

    async def _wait_idle_gate(self) -> None:
        while self._reducer.state.phase != "idle" or self._reducer.state.idle_streak < self._cfg.idle_streak_required:
            await asyncio.sleep(0.1)

    async def _execute_job(self, job: AskJob) -> str:
        self._reducer.reset_job_cycle()
        executor = CommandExecutor(self._page, self._reducer, answer_timeout_s=job.timeout_s)
        planner = GeminiPlanner()
        try:
            return await executor.run(planner.plan(job))
        except AiRouterError as exc:
            if exc.code == "GEMINI_ERROR":
                return await executor.run(planner.plan(job, recovery=True))
            raise
```

- [ ] **Step 2: Verify import**

```bash
poetry run python -c "from ai_router.browser.page_worker import PageWorker; print('ok')"
```

---

### Task 8: Wire SessionManager + AppState

**Files:**
- Modify: `src/ai_router/session/manager.py`
- Modify: `src/ai_router/mcp/tools.py`

- [ ] **Step 1: Add PageWorkerRegistry to AppState**

```python
# tools.py AppState — add field
from ai_router.browser.page_worker import PageWorker
from ai_router.browser.page_queue import PageQueueRegistry

@dataclass
class AppState:
    ...
    page_workers: dict[str, PageWorker]  # page_id -> worker
    page_queues: PageQueueRegistry
```

Initialize in `create_app_state()`.

- [ ] **Step 2: ensure_worker helper**

```python
def ensure_worker(state: AppState, page: Page) -> PageWorker:
    pid = page_id_of(page)
    if pid not in state.page_workers:
        q = state.page_queues.queue_for(page)
        w = PageWorker(page, q, state.config)
        w.start()
        state.page_workers[pid] = w
    return state.page_workers[pid]
```

Call from `get_or_create` after page is ready.

---

### Task 9: Rewrite handle_ask

**Files:**
- Modify: `src/ai_router/mcp/tools.py`

- [ ] **Step 1: Replace adapter.ask with enqueue**

```python
import asyncio
import uuid
from ai_router.browser.page_queue import AskJob
from ai_router.browser.events import page_id_of

async def handle_ask(...):
    ...
    session = await state.sessions.get_or_create(...)
    # ensure_page_ready (login check) — keep
    worker = ensure_worker(state, session.page)
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    job = AskJob(
        job_id=str(uuid.uuid4()),
        mcp_session_id=mcp_session_id,
        prompt=prompt,
        provider_id=adapter.id,
        future=future,
        timeout_s=state.config.answer_timeout_s,
    )
    await worker.enqueue(job)
    try:
        answer = await asyncio.wait_for(future, timeout=job.timeout_s)
    except asyncio.TimeoutError:
        future.cancel()
        raise TimeoutError_()
    state.sessions.record_message(mcp_session_id, page_url=session.page.url)
    return {"answer": answer, ...}
```

- [ ] **Step 2: Remove `async with state.browser.acquire()` from handle_ask**

Keep `browser.ensure_context()` implicit via `get_or_create` → `new_page`.

- [ ] **Step 3: session_status — read-only, no worker queue**

Use `adapter.ensure_page_ready` on a throwaway page or cached worker state without enqueue.

---

### Task 10: Slim GeminiAdapter + remove old wait path

**Files:**
- Modify: `src/ai_router/adapters/gemini/adapter.py`
- Modify: `src/ai_router/adapters/gemini/wait.py`

- [ ] **Step 1: Remove from adapter.py**

Delete: `ask`, `_ask_once`, `_submit_prompt`, `_poll_generating`, `_wait_until_idle`

Keep: `check_session`, `ensure_page_ready`, `open_new_chat`, `resume_chat`

- [ ] **Step 2: Remove `wait_for_answer_dom`, `wait_for_stream` from wait.py**

Keep: `braces_balanced`, `is_rate_limited` (used by reducer/executor if needed)

- [ ] **Step 3: Update base Protocol**

```python
# adapters/base.py — ProviderAdapter no longer requires ask()
# ask path goes through PageWorker + planner
```

- [ ] **Step 4: Run full test suite**

```bash
poetry run pytest -v
```

Expected: all unit tests PASS

---

### Task 11: Manual E2E checklist

- [ ] **Step 1: Start server**

```bash
poetry run ai serve --port 9090
```

- [ ] **Step 2: Single ask** — `1+1=?` → answer `2`, input empty after done

- [ ] **Step 3: Follow-up same Cursor tab** — `1+2=?` → `3`, same chat URL

- [ ] **Step 4: Parallel tabs** — two Cursor tabs ask concurrently → both complete without blocking each other

- [ ] **Step 5: Close browser + ask** — resumes `chat_url` or new chat gracefully

- [ ] **Step 6: Long prompt (VertexBook)** — no text re-injection during stream, full answer returned

---

## Spec Coverage Checklist

| Spec section | Task |
|--------------|------|
| Per-page queue | Task 4, 7, 8 |
| Hybrid channel | Task 2, 7 |
| BrowserState reducer | Task 3 |
| Blocking Future | Task 9 |
| CommandScript | Task 5, 6 |
| Send-only submit | Task 5 |
| idle_streak gate | Task 3, 7 |
| wait_answer full cycle | Task 3, 5 |
| 1095 recovery | Task 7 |
| chat_url persistence | Task 8 (unchanged SessionManager) |
| Remove global acquire for ask | Task 9 |
| Config tuning | Task 1 |
| session_status read-only | Task 9 |

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-09-browser-event-queue.md`.

**Two execution options:**

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks
2. **Inline Execution** — implement tasks in this session with checkpoints

Which approach do you want?
