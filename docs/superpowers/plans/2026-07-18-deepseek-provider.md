# DeepSeek Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `deepseek` provider that automates chat.deepseek.com web sessions — DOM main-response text (no thinking), SSE `/api/v0/chat/completion` stream-end signals (`FINISHED` + `event: close`), explicit New Chat per ask.

**Architecture:** New `src/ai_router/adapters/deepseek/` package. Extend `CommandOp` with `new_chat` and `ProviderProfile` with optional `on_new_chat` / `is_challenge_visible` hooks. Register in `build_registry()`, default timeout 600s. Reuse existing `StateReducer` hybrid gate unchanged.

**Tech Stack:** Python 3.11+, Playwright (CloakBrowser), existing `ProviderProfile` / `Command` / `StateReducer` infrastructure.

**Spec:** `docs/superpowers/specs/2026-07-18-deepseek-provider-design.md`

## Global Constraints

- Answer source: DOM only — `.ds-assistant-message-main-content` (stream is signal-only)
- Thinking blocks: excluded from returned text
- Chat lifecycle: new chat per `ask` — navigate + explicit **New Chat**
- Stream completion: `event: close` after `response/status: FINISHED` (both required for SSE success)
- Challenge detection: fail fast on Cloudflare Turnstile / verify UI during `wait_idle`
- Answer timeout default: **`600.0`** seconds
- Recoverable codes: `("DEEPSEEK_ERROR",)` only

---

## File map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/ai_router/browser/profile.py` | Modify | Optional `on_new_chat`, `is_challenge_visible` hooks |
| `src/ai_router/browser/commands.py` | Modify | `new_chat` op + challenge check in `_wait_idle` |
| `src/ai_router/adapters/deepseek/__init__.py` | Create | Package marker |
| `src/ai_router/adapters/deepseek/selectors.py` | Create | URLs, regex, DOM selectors, error markers |
| `src/ai_router/adapters/deepseek/stream.py` | Create | SSE `parse_stream_done` with event+data parsing |
| `src/ai_router/adapters/deepseek/wait.py` | Create | DOM wait helpers, `ensure_new_chat`, challenge check |
| `src/ai_router/adapters/deepseek/planner.py` | Create | Command plan per ask |
| `src/ai_router/adapters/deepseek/adapter.py` | Create | `DeepSeekAdapter` + `build_profile` |
| `src/ai_router/adapters/registry.py` | Modify | Register `DeepSeekAdapter` |
| `src/ai_router/config.py` | Modify | Default `deepseek` provider URL + 600s timeout |
| `tests/test_commands_new_chat.py` | Create | `new_chat` command unit test |
| `tests/test_deepseek_stream.py` | Create | Stream parser unit tests |
| `tests/test_deepseek_wait.py` | Create | Rate-limit + challenge helper tests |
| `tests/test_deepseek_planner.py` | Create | Planner unit tests |
| `tests/test_registry.py` | Modify | Registry includes deepseek |
| `tests/test_router.py` | Modify | Resolve `provider=deepseek` |
| `tests/test_config.py` | Modify | Assert `deepseek` in default providers |
| `tests/test_ask_multi.py` | Modify | Include deepseek in available providers list |
| `README.md` | Modify | Document DeepSeek provider |

---

### Task 1: Core hooks — `new_chat` command + challenge detection

**Files:**
- Modify: `src/ai_router/browser/profile.py`
- Modify: `src/ai_router/browser/commands.py`
- Create: `tests/test_commands_new_chat.py`

**Interfaces:**
- Produces: `ProviderProfile.on_new_chat: Callable[[Page], Awaitable[None]] | None`
- Produces: `ProviderProfile.is_challenge_visible: Callable[[Page], Awaitable[bool]] | None`
- Produces: `CommandOp` includes `"new_chat"`

- [ ] **Step 1: Extend ProviderProfile**

```python
# src/ai_router/browser/profile.py — add imports and fields to ProviderProfile
from collections.abc import Awaitable, Callable
# ... existing imports ...

@dataclass
class ProviderProfile:
    # ... existing fields ...
    on_new_chat: Callable[[Page], Awaitable[None]] | None = None
    is_challenge_visible: Callable[[Page], Awaitable[bool]] | None = None
```

- [ ] **Step 2: Write failing test for new_chat command**

```python
# tests/test_commands_new_chat.py
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from ai_router.browser.commands import Command, CommandExecutor
from ai_router.browser.profile import ProviderProfile, ProviderSelectors, StreamDone
from ai_router.browser.state import StateReducer
import re


@pytest.mark.asyncio
async def test_new_chat_calls_profile_hook():
    called = asyncio.Event()
    page = MagicMock()

    async def on_new_chat(p):
        assert p is page
        called.set()

    profile = ProviderProfile(
        provider_id="fake",
        stream_url_re=re.compile(r"/completion"),
        parse_stream_done=lambda s, b: StreamDone(done=False, ok=False),
        is_stop_visible=AsyncMock(return_value=False),
        read_response_snapshot=AsyncMock(return_value=(0, "")),
        is_rate_limited=lambda t: False,
        submit_ready=AsyncMock(return_value=True),
        planner=MagicMock(),
        selectors=ProviderSelectors(prompt_input="#in", submit_button="#btn"),
        error_markers=(),
        recoverable_codes=(),
        on_new_chat=on_new_chat,
    )
    reducer = StateReducer(
        page_id="p1",
        stream_url_res=[profile.stream_url_re],
        idle_streak_required=1,
        generating_streak_required=1,
        answer_stable_ticks=1,
        stream_quiet_s=0.1,
        error_markers=(),
    )
    reducer.state.phase = "idle"
    reducer.state.idle_streak = 99

    executor = CommandExecutor(
        page,
        reducer,
        profile=profile,
        job_id="j1",
        page_id="p1",
        answer_timeout_s=30.0,
        idle_streak_required=1,
    )
    await executor.run([Command("new_chat")])
    assert called.is_set()
```

- [ ] **Step 3: Run test to verify it fails**

```bash
pytest tests/test_commands_new_chat.py -v
```

Expected: FAIL — `new_chat` not in `CommandOp` or handler missing

- [ ] **Step 4: Implement new_chat op and challenge check**

```python
# src/ai_router/browser/commands.py

# Extend CommandOp:
CommandOp = Literal[
    "wait_idle",
    "clear_input",
    "type",
    "submit",
    "wait_generating",
    "wait_answer",
    "goto",
    "new_chat",
]

# In run(), add branch before wait_idle handling:
            elif cmd.op == "new_chat":
                hook = self._profile.on_new_chat
                if hook is None:
                    raise AiRouterError(
                        "ADAPTER_ERROR",
                        f"Provider {self._profile.provider_id} has no new_chat handler",
                    )
                await hook(self._page)
                before_count, _ = await self._profile.read_response_snapshot(self._page)

# In _wait_idle(), at top of while loop after deadline check:
            challenge = self._profile.is_challenge_visible
            if challenge is not None and await challenge(self._page):
                prefix = self._profile.provider_id.upper()
                raise AiRouterError(
                    f"{prefix}_ERROR",
                    "Challenge or verification page detected",
                )
```

- [ ] **Step 5: Run test to verify it passes**

```bash
pytest tests/test_commands_new_chat.py -v
```

Expected: 1 passed

- [ ] **Step 6: Commit**

```bash
git add src/ai_router/browser/profile.py src/ai_router/browser/commands.py tests/test_commands_new_chat.py
git commit -m "feat: add new_chat command and challenge detection hooks"
```

---

### Task 2: Selectors module

**Files:**
- Create: `src/ai_router/adapters/deepseek/__init__.py`
- Create: `src/ai_router/adapters/deepseek/selectors.py`

**Interfaces:**
- Produces: `DEEPSEEK_URL`, `DEEPSEEK_COMPLETION_RE`, all `SEL_*` constants, marker tuples

- [ ] **Step 1: Create package and selectors**

```python
# src/ai_router/adapters/deepseek/__init__.py
# (empty file)
```

```python
# src/ai_router/adapters/deepseek/selectors.py
import re

DEEPSEEK_URL = "https://chat.deepseek.com/"

DEEPSEEK_COMPLETION_RE = re.compile(
    r"/api/v\d+/chat/completion(?:\?|$)",
    re.I,
)

SEL_NEW_CHAT = (
    'button[aria-label*="New chat" i], '
    'a[aria-label*="New chat" i], '
    'button:has-text("New chat")'
)
SEL_PROMPT_INPUT = (
    ".ds-chat-input-container textarea, "
    "#chat-input, "
    'textarea:not([aria-hidden="true"]):visible, '
    'div[contenteditable="true"]:visible'
)
SEL_SUBMIT_BUTTON = (
    'button[aria-label*="Send" i], '
    'button[type="submit"]'
)
SEL_STOP_BUTTON = 'button[aria-label*="Stop" i]'
SEL_ASSISTANT_MAIN = (
    '[data-testid="assistant-message"], '
    ".ds-assistant-message-main-content"
)
SEL_ASSISTANT_TEXT = (
    '[data-testid="assistant-message"] .ds-markdown, '
    ".ds-assistant-message-main-content .ds-markdown"
)
SEL_LOGIN = 'a[href*="/login"], button:has-text("Log in")'
SEL_CHALLENGE = (
    'iframe[src*="challenges.cloudflare.com"], '
    'iframe[src*="turnstile"], '
    '[class*="turnstile"]'
)

RATE_LIMIT_MARKERS = (
    "rate limit",
    "too many requests",
    "try again later",
)

CHALLENGE_MARKERS = (
    "checking your browser",
    "verify you are human",
)

DEEPSEEK_ERROR_MARKERS = (
    "something went wrong",
    "unable to respond",
    "an error occurred",
)

FAILURE_STATUSES = frozenset(
    {"ERROR", "FAILED", "CANCELLED", "INTERRUPTED", "ABORTED"}
)
```

- [ ] **Step 2: Commit**

```bash
git add src/ai_router/adapters/deepseek/
git commit -m "feat(deepseek): add selectors and completion URL regex"
```

---

### Task 3: Stream parser (TDD)

**Files:**
- Create: `tests/test_deepseek_stream.py`
- Create: `src/ai_router/adapters/deepseek/stream.py`

**Interfaces:**
- Produces: `parse_stream_done(status: int, body: str) -> StreamDone`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_deepseek_stream.py
from ai_router.adapters.deepseek.stream import parse_stream_done


def _sse(*lines: str) -> str:
    return "\n".join(lines)


def test_close_after_finished_is_success():
    body = _sse(
        'data: {"p":"response/status","o":"SET","v":"FINISHED"}',
        "event: close",
        'data: {"click_behavior":"none","auto_resume":false}',
    )
    result = parse_stream_done(200, body)
    assert result.done is True
    assert result.ok is True
    assert result.error_kind is None


def test_finished_without_close_not_done():
    body = _sse(
        'data: {"p":"response/status","o":"SET","v":"FINISHED"}',
    )
    result = parse_stream_done(200, body)
    assert result.done is False
    assert result.ok is False


def test_batch_quasi_finished_plus_close_is_success():
    body = _sse(
        'data: {"p":"response","o":"BATCH","v":[{"p":"quasi_status","v":"FINISHED"}]}',
        "event: close",
        'data: {}',
    )
    result = parse_stream_done(200, body)
    assert result.done is True
    assert result.ok is True


def test_status_error_is_failure():
    body = _sse(
        'data: {"p":"response/status","o":"SET","v":"ERROR"}',
    )
    result = parse_stream_done(200, body)
    assert result.done is True
    assert result.ok is False
    assert result.error_kind == "error"


def test_close_without_finished_not_done():
    body = _sse(
        "event: close",
        'data: {"click_behavior":"none"}',
    )
    result = parse_stream_done(200, body)
    assert result.done is False
    assert result.ok is False


def test_think_only_partial_not_done():
    body = _sse(
        'data: {"v":{"response":{"fragments":[{"type":"THINK","content":"We"}]}}}',
        'data: {"p":"response/fragments/-1/content","o":"APPEND","v":" think"}',
    )
    result = parse_stream_done(200, body)
    assert result.done is False
    assert result.ok is False


def test_http_429_is_rate_limit():
    result = parse_stream_done(429, "too many requests")
    assert result.done is True
    assert result.ok is False
    assert result.error_kind == "rate_limit"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_deepseek_stream.py -v
```

Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement stream parser**

```python
# src/ai_router/adapters/deepseek/stream.py
from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from ai_router.adapters.deepseek.selectors import FAILURE_STATUSES, RATE_LIMIT_MARKERS
from ai_router.browser.profile import StreamDone

_RATE_LIMIT_STATUSES = (401, 403, 429)


def _iter_sse_events(body: str) -> Iterator[tuple[str | None, dict[str, Any] | None]]:
    """Yield (event_name, data_payload) pairs from an SSE body."""
    current_event: str | None = None
    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("event:"):
            current_event = line[len("event:") :].strip()
            continue
        if line.startswith("data:"):
            payload = line[len("data:") :].strip()
            data: dict[str, Any] | None = None
            if payload:
                try:
                    parsed = json.loads(payload)
                    if isinstance(parsed, dict):
                        data = parsed
                except json.JSONDecodeError:
                    pass
            yield current_event, data
            current_event = None


def _patch_failure(data: dict[str, Any]) -> bool:
    if data.get("o") != "SET":
        return False
    path = data.get("p")
    value = data.get("v")
    if path == "response/status" and isinstance(value, str):
        return value.upper() in FAILURE_STATUSES
    return False


def _patch_finished(data: dict[str, Any]) -> bool:
    if data.get("o") == "SET":
        if data.get("p") == "response/status" and data.get("v") == "FINISHED":
            return True
    if data.get("o") == "BATCH" and isinstance(data.get("v"), list):
        for item in data["v"]:
            if isinstance(item, dict) and item.get("v") == "FINISHED":
                if item.get("p") in ("quasi_status", "response/status", "status"):
                    return True
    return False


def parse_stream_done(status: int, body: str) -> StreamDone:
    """Classify a finished chat.deepseek.com /completion SSE body.

    Success requires a FINISHED status signal AND event: close.
    Answer text is read from the DOM by StateReducer — not from this parser.
    """
    if status >= 400:
        lower = body.lower()
        if status in _RATE_LIMIT_STATUSES or any(
            m in lower for m in RATE_LIMIT_MARKERS
        ):
            return StreamDone(
                done=True,
                ok=False,
                error_kind="rate_limit",
                error_text=f"HTTP {status}: {body[:200]}",
            )
        return StreamDone(
            done=True,
            ok=False,
            error_kind="error",
            error_text=f"HTTP {status}: {body[:200]}",
        )

    saw_finished = False
    saw_close = False

    for event_name, data in _iter_sse_events(body):
        if event_name == "close":
            saw_close = True
        if data is None:
            continue
        if _patch_failure(data):
            return StreamDone(
                done=True,
                ok=False,
                error_kind="error",
                error_text=f"Stream status: {data.get('v')}",
            )
        if _patch_finished(data):
            saw_finished = True

    if saw_close and saw_finished:
        return StreamDone(done=True, ok=True)
    if saw_close and not saw_finished:
        return StreamDone(done=False, ok=False)
    return StreamDone(done=False, ok=False)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_deepseek_stream.py -v
```

Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add tests/test_deepseek_stream.py src/ai_router/adapters/deepseek/stream.py
git commit -m "feat(deepseek): add SSE completion stream parser"
```

---

### Task 4: Wait helpers (TDD)

**Files:**
- Create: `tests/test_deepseek_wait.py`
- Create: `src/ai_router/adapters/deepseek/wait.py`

**Interfaces:**
- Produces: `is_rate_limited(text: str) -> bool`
- Produces: `is_challenge_visible(page: Page) -> bool` (async)
- Produces: `is_stop_visible(page: Page) -> bool` (async)
- Produces: `read_response_snapshot(page: Page) -> tuple[int, str]` (async)
- Produces: `submit_ready(page: Page) -> bool` (async)
- Produces: `ensure_new_chat(page: Page) -> None` (async)

- [ ] **Step 1: Write failing tests**

```python
# tests/test_deepseek_wait.py
from ai_router.adapters.deepseek.wait import is_rate_limited


def test_rate_limit_english():
    assert is_rate_limited("Rate limit exceeded, try again later") is True


def test_rate_limit_negative():
    assert is_rate_limited("The answer is 42") is False
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_deepseek_wait.py -v
```

Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement wait helpers**

```python
# src/ai_router/adapters/deepseek/wait.py
from __future__ import annotations

from playwright.async_api import Page

from ai_router.adapters.deepseek.selectors import (
    CHALLENGE_MARKERS,
    RATE_LIMIT_MARKERS,
    SEL_ASSISTANT_MAIN,
    SEL_ASSISTANT_TEXT,
    SEL_CHALLENGE,
    SEL_NEW_CHAT,
    SEL_PROMPT_INPUT,
    SEL_STOP_BUTTON,
    SEL_SUBMIT_BUTTON,
)


def is_rate_limited(text: str) -> bool:
    lower = text.lower()
    return any(marker in lower for marker in RATE_LIMIT_MARKERS)


async def is_challenge_visible(page: Page) -> bool:
    if await page.locator(SEL_CHALLENGE).count() > 0:
        return True
    try:
        body = (await page.locator("body").inner_text()).lower()
    except Exception:
        return False
    return any(marker in body for marker in CHALLENGE_MARKERS)


async def is_stop_visible(page: Page) -> bool:
    return await page.locator(SEL_STOP_BUTTON).count() > 0


async def read_response_snapshot(page: Page) -> tuple[int, str]:
    """Return assistant main-content count and text of the latest response."""
    blocks = page.locator(SEL_ASSISTANT_MAIN)
    count = await blocks.count()
    if not count:
        return 0, ""
    last = blocks.nth(count - 1)
    inner = last.locator(".ds-markdown")
    if await inner.count():
        text = (await inner.first.inner_text()).strip()
    else:
        text = (await last.inner_text()).strip()
    return count, text


async def submit_ready(page: Page) -> bool:
    if await page.locator(SEL_PROMPT_INPUT).count() == 0:
        return False
    submit = page.locator(SEL_SUBMIT_BUTTON).first
    if await submit.count() == 0:
        return False
    return not await submit.is_disabled()


async def ensure_new_chat(page: Page) -> None:
    """Click New Chat and wait until no assistant messages remain."""
    btn = page.locator(SEL_NEW_CHAT).first
    if await btn.count() > 0:
        await btn.click()
    count, _ = await read_response_snapshot(page)
    if count == 0:
        return
    # Fallback: hard navigation if button did not clear history
    from ai_router.adapters.deepseek.selectors import DEEPSEEK_URL

    await page.goto(DEEPSEEK_URL, wait_until="domcontentloaded")
    for _ in range(20):
        count, _ = await read_response_snapshot(page)
        if count == 0:
            return
        await page.wait_for_timeout(250)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_deepseek_wait.py -v
```

Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add tests/test_deepseek_wait.py src/ai_router/adapters/deepseek/wait.py
git commit -m "feat(deepseek): add DOM wait helpers and ensure_new_chat"
```

---

### Task 5: Planner (TDD)

**Files:**
- Create: `tests/test_deepseek_planner.py`
- Create: `src/ai_router/adapters/deepseek/planner.py`

**Interfaces:**
- Produces: `DeepSeekPlanner.plan(job, *, recovery=False) -> list[Command]`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_deepseek_planner.py
from ai_router.adapters.deepseek.planner import DeepSeekPlanner
from ai_router.adapters.deepseek.selectors import DEEPSEEK_URL
from ai_router.browser.page_queue import AskJob


def make_job() -> AskJob:
    import asyncio

    loop = asyncio.new_event_loop()
    fut = loop.create_future()
    return AskJob("1", "sess", "hello", "deepseek", fut, 600.0)


def test_plan_opens_fresh_chat():
    cmds = DeepSeekPlanner().plan(make_job())
    ops = [c.op for c in cmds]
    assert ops == [
        "goto",
        "new_chat",
        "wait_idle",
        "clear_input",
        "type",
        "submit",
        "wait_generating",
        "wait_answer",
    ]
    assert cmds[0].args["url"] == DEEPSEEK_URL


def test_recovery_plan_also_starts_fresh():
    cmds = DeepSeekPlanner().plan(make_job(), recovery=True)
    assert cmds[0].op == "goto"
    assert cmds[1].op == "new_chat"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_deepseek_planner.py -v
```

Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement planner**

```python
# src/ai_router/adapters/deepseek/planner.py
from ai_router.adapters.deepseek.selectors import DEEPSEEK_URL
from ai_router.browser.commands import Command
from ai_router.browser.page_queue import AskJob


class DeepSeekPlanner:
    def plan(self, job: AskJob, *, recovery: bool = False) -> list[Command]:
        return [
            Command("goto", {"url": DEEPSEEK_URL}),
            Command("new_chat"),
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

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_deepseek_planner.py -v
```

Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add tests/test_deepseek_planner.py src/ai_router/adapters/deepseek/planner.py
git commit -m "feat(deepseek): add planner with new_chat step"
```

---

### Task 6: Adapter

**Files:**
- Create: `src/ai_router/adapters/deepseek/adapter.py`

**Interfaces:**
- Produces: `DeepSeekAdapter` with `id="deepseek"`, `build_profile(cfg) -> ProviderProfile`

- [ ] **Step 1: Implement DeepSeekAdapter**

```python
# src/ai_router/adapters/deepseek/adapter.py
from __future__ import annotations

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from ai_router.adapters.base import SessionStatus
from ai_router.adapters.deepseek.planner import DeepSeekPlanner
from ai_router.adapters.deepseek.selectors import (
    DEEPSEEK_COMPLETION_RE,
    DEEPSEEK_ERROR_MARKERS,
    DEEPSEEK_URL,
    SEL_LOGIN,
    SEL_PROMPT_INPUT,
    SEL_SUBMIT_BUTTON,
)
from ai_router.adapters.deepseek.stream import parse_stream_done
from ai_router.adapters.deepseek.wait import (
    ensure_new_chat,
    is_challenge_visible,
    is_rate_limited,
    is_stop_visible,
    read_response_snapshot,
    submit_ready,
)
from ai_router.browser.profile import ProviderProfile, ProviderSelectors
from ai_router.config import AppConfig


class DeepSeekAdapter:
    id = "deepseek"
    name = "DeepSeek"
    keywords: list[str] = ["deepseek", "@deepseek"]
    status = "available"

    async def check_session(self, page: Page) -> SessionStatus:
        return await self.ensure_page_ready(page)

    async def ensure_page_ready(self, page: Page) -> SessionStatus:
        await page.goto(DEEPSEEK_URL, wait_until="domcontentloaded")
        if await is_challenge_visible(page):
            return SessionStatus.UNKNOWN
        try:
            await page.wait_for_selector(SEL_PROMPT_INPUT, timeout=15_000)
            return SessionStatus.LOGGED_IN
        except PlaywrightTimeout:
            if await page.locator(SEL_LOGIN).count() > 0:
                return SessionStatus.LOGGED_OUT
            return SessionStatus.UNKNOWN

    async def open_new_chat(self, page: Page) -> None:
        await ensure_new_chat(page)

    def build_profile(self, cfg: AppConfig) -> ProviderProfile:
        return ProviderProfile(
            provider_id=self.id,
            stream_url_re=DEEPSEEK_COMPLETION_RE,
            parse_stream_done=parse_stream_done,
            is_stop_visible=is_stop_visible,
            read_response_snapshot=read_response_snapshot,
            is_rate_limited=is_rate_limited,
            submit_ready=submit_ready,
            planner=DeepSeekPlanner(),
            selectors=ProviderSelectors(
                prompt_input=SEL_PROMPT_INPUT,
                submit_button=SEL_SUBMIT_BUTTON,
            ),
            error_markers=DEEPSEEK_ERROR_MARKERS,
            recoverable_codes=("DEEPSEEK_ERROR",),
            answer_timeout_s=cfg.deepseek_answer_timeout_s,
            parse_ws_frame=None,
            on_new_chat=ensure_new_chat,
            is_challenge_visible=is_challenge_visible,
        )
```

- [ ] **Step 2: Commit**

```bash
git add src/ai_router/adapters/deepseek/adapter.py
git commit -m "feat(deepseek): add DeepSeekAdapter with ProviderProfile wiring"
```

---

### Task 7: Registry and config

**Files:**
- Modify: `src/ai_router/adapters/registry.py`
- Modify: `src/ai_router/config.py`
- Modify: `tests/test_registry.py` (create if missing)
- Modify: `tests/test_config.py`

- [ ] **Step 1: Write failing registry test**

```python
# tests/test_registry.py — append or create
from ai_router.adapters.registry import build_registry


def test_build_registry_includes_deepseek():
    registry = build_registry()
    ids = [a.id for a in registry.list_all()]
    assert "deepseek" in ids


def test_deepseek_adapter_is_available():
    registry = build_registry()
    ds = registry.get("deepseek")
    assert ds.status == "available"
    assert ds.name == "DeepSeek"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_registry.py::test_build_registry_includes_deepseek -v
```

Expected: FAIL — `"deepseek" not in ids`

- [ ] **Step 3: Register adapter**

```python
# src/ai_router/adapters/registry.py
def build_registry() -> ProviderRegistry:
    from ai_router.adapters.claude.adapter import ClaudeAdapter
    from ai_router.adapters.deepseek.adapter import DeepSeekAdapter
    from ai_router.adapters.gemini.adapter import GeminiAdapter

    registry = ProviderRegistry()
    registry.register(GeminiAdapter())
    registry.register(ChatGPTAdapter())
    registry.register(ClaudeAdapter())
    registry.register(DeepSeekAdapter())
    return registry
```

- [ ] **Step 4: Add config defaults**

In `src/ai_router/config.py`:

1. Add to `AppConfig`:
```python
deepseek_answer_timeout_s: float = 600.0
```

2. Add to `_defaults()` providers:
```python
"deepseek": ProviderConfig(url="https://chat.deepseek.com/"),
```

3. YAML loader:
```python
if "deepseek_answer_timeout_s" in raw:
    cfg.deepseek_answer_timeout_s = float(raw["deepseek_answer_timeout_s"])
```

4. Env override:
```python
if v := os.getenv("AI_ROUTER_DEEPSEEK_ANSWER_TIMEOUT_S"):
    cfg.deepseek_answer_timeout_s = float(v)
```

- [ ] **Step 5: Update config test**

```python
# tests/test_config.py — add
def test_load_config_defaults_includes_deepseek(tmp_path):
    cfg = load_config(tmp_path / "missing.yaml")
    assert "deepseek" in cfg.providers
    assert cfg.providers["deepseek"].url == "https://chat.deepseek.com/"
    assert cfg.deepseek_answer_timeout_s == 600.0
```

- [ ] **Step 6: Run tests**

```bash
pytest tests/test_registry.py tests/test_config.py -v
```

Expected: all passed

- [ ] **Step 7: Commit**

```bash
git add src/ai_router/adapters/registry.py src/ai_router/config.py \
  tests/test_registry.py tests/test_config.py
git commit -m "feat(deepseek): register provider and add config defaults"
```

---

### Task 8: Router and ask_multi tests

**Files:**
- Modify: `tests/test_router.py`
- Modify: `tests/test_ask_multi.py`

- [ ] **Step 1: Add router resolve test**

```python
# tests/test_router.py
from ai_router.adapters.deepseek.adapter import DeepSeekAdapter

def test_resolve_deepseek_provider():
    registry = ProviderRegistry(
        [_FakeGemini(), ChatGPTAdapter(), ClaudeAdapter(), DeepSeekAdapter()]
    )
    adapter, reason = resolve_provider(registry, "deepseek", default="gemini")
    assert adapter.id == "deepseek"
    assert reason == "explicit param"
```

- [ ] **Step 2: Update ask_multi default providers test**

In `tests/test_ask_multi.py`, update fixture adapters dict and assertion:

```python
# Add DeepSeekAdapter() to adapters fixture
assert sorted(e["provider"] for e in res["answers"]) == [
    "chatgpt",
    "claude",
    "deepseek",
    "gemini",
]
```

Also add `"deepseek": DeepSeekAdapter()` to any fake adapter maps in the file.

- [ ] **Step 3: Run tests**

```bash
pytest tests/test_router.py tests/test_ask_multi.py -v
```

Expected: all passed

- [ ] **Step 4: Commit**

```bash
git add tests/test_router.py tests/test_ask_multi.py
git commit -m "test: add deepseek provider routing and ask_multi cases"
```

---

### Task 9: README documentation

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update provider mentions**

1. Intro line: add DeepSeek alongside Gemini, ChatGPT, Claude
2. Login examples: add `ai-router browser login --provider deepseek`
3. MCP `ask` table: mention `provider` can be `"deepseek"`
4. CLI help: `gemini|chatgpt|claude|deepseek`
5. Config YAML example:
   ```yaml
     deepseek:
       url: https://chat.deepseek.com/
   ```
6. Env table row: `AI_ROUTER_DEEPSEEK_ANSWER_TIMEOUT_S` default 600
7. Note: 600s default timeout supports long thinking runs

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: add DeepSeek provider to README"
```

---

### Task 10: Full test suite and manual smoke test

**Files:** (verification only)

- [ ] **Step 1: Run full unit test suite**

```bash
pytest -v
```

Expected: all tests pass (no regressions)

- [ ] **Step 2: Manual smoke test (requires DeepSeek account)**

```bash
ai-router browser login --provider deepseek
ai-router browser status --provider deepseek
# Expected: deepseek: logged_in

# MCP ask:
# ask(prompt="Reply with exactly: router working", provider="deepseek")
# Expected: answer contains "router working" (main content only, no thinking)
```

- [ ] **Step 3: Tune selectors if smoke test fails**

Inspect live DOM at `chat.deepseek.com` and update `SEL_NEW_CHAT`, `SEL_PROMPT_INPUT`, `SEL_SUBMIT_BUTTON` in `selectors.py`. Re-run smoke test.

- [ ] **Step 4: Final commit (only if selector fixes needed)**

```bash
git add src/ai_router/adapters/deepseek/selectors.py
git commit -m "fix(deepseek): tune DOM selectors after smoke test"
```

---

## Spec coverage checklist

| Spec requirement | Task |
|------------------|------|
| DOM main-response only | Task 4 (`read_response_snapshot`) |
| Exclude thinking blocks | Task 4 (selector targets main content) |
| SSE FINISHED + close success rule | Task 3 (`parse_stream_done`) |
| Explicit New Chat per ask | Tasks 1, 4, 5 (`new_chat` + `ensure_new_chat`) |
| Challenge fail-fast | Tasks 1, 4 (`is_challenge_visible`) |
| 600s default timeout | Task 7 |
| Shared StateReducer gate | No task (reuse existing — unchanged) |
| Registry registration | Task 7 |
| Session/login | Task 6 (`ensure_page_ready`) + Task 9 |
| Error codes DEEPSEEK_* | Task 6 (`recoverable_codes`) |
| Unit tests | Tasks 1, 3, 4, 5, 7, 8 |
| README | Task 9 |

## Out of scope (do not implement)

- Direct API calls with Bearer token
- Model/thinking toggle via UI
- Multi-turn conversation retention
- SSE RESPONSE fragment answer extraction
- Rolling per-fragment timeout reset
- PoW solver implementation
