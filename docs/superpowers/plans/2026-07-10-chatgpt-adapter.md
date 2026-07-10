# ChatGPT Adapter (SSE Completion Detection) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a working ChatGPT provider that drives chatgpt.com's web UI, detects answer completion by passively parsing the `/backend-api/f/conversation` SSE stream, and reads the answer text from the DOM — while removing the Gemini hardcoding from the browser layer via a `ProviderProfile` abstraction.

**Architecture:** A new `ProviderProfile` dataclass carries all provider-specific pieces (stream URL regex, SSE done-parser, DOM wait helpers, selectors, planner, error markers). `events.py` / `state.py` / `commands.py` / `page_worker.py` consume profiles generically. ChatGPT's SSE parser concludes success only when the assistant message on `channel:"final"` reaches `finished_successfully` + `end_turn:true` (or the `last_token` marker) AND `message_stream_complete` arrives — reasoning/system messages are ignored, so thinking models (o3, gpt-5-thinking) never false-done.

**Tech Stack:** Python 3.11, Playwright (async), Typer, pytest + pytest-asyncio (asyncio_mode=auto), Poetry.

**Spec:** `docs/superpowers/specs/2026-07-10-chatgpt-adapter-design.md`

## Global Constraints

- Run tests with `poetry run pytest` (asyncio_mode=auto — async tests need no decorator). Lint: `poetry run ruff check src tests` (line-length 100).
- NO token forging (sentinel/turnstile/conduit), NO direct API replay via fetch. Passive listening only.
- Answer text comes from the DOM; SSE yields only done/ok/error signals.
- ChatGPT default answer timeout: **300.0 s** (`chatgpt_answer_timeout_s`, env `AI_ROUTER_CHATGPT_ANSWER_TIMEOUT_S`).
- Error kinds: `rate_limit` → `RateLimitedError`; `moderation` → `AiRouterError("CHATGPT_MODERATION")`; `incomplete` → `AiRouterError("CHATGPT_INCOMPLETE")`; DOM markers → `AiRouterError("<ID>_ERROR")`. Recovery (reload + retry once) only for codes in `profile.recoverable_codes`.
- Existing Gemini behavior must not regress: full suite green after every task.
- Commit after every task (small, frequent commits).

---

### Task 1: `ProviderProfile` + `StreamDone` container types

**Files:**
- Create: `src/ai_router/browser/profile.py`
- Test: `tests/test_profile.py`

**Interfaces:**
- Consumes: nothing (leaf module — stdlib + playwright types only; must NOT import other `ai_router` modules to stay cycle-free).
- Produces (used by every later task):
  - `StreamDone(done: bool, ok: bool, error_kind: str | None = None, error_text: str | None = None)` — frozen dataclass. `error_kind ∈ {None, "rate_limit", "moderation", "incomplete", "error"}`.
  - `ProviderSelectors(prompt_input: str, submit_button: str)` — frozen dataclass of CSS selectors.
  - `ProviderProfile` — dataclass with fields exactly: `provider_id: str`, `stream_url_re: re.Pattern[str]`, `parse_stream_done: Callable[[int, str], StreamDone]`, `is_stop_visible: Callable[[Page], Awaitable[bool]]`, `read_response_snapshot: Callable[[Page], Awaitable[tuple[int, str]]]`, `is_rate_limited: Callable[[str], bool]`, `submit_ready: Callable[[Page], Awaitable[bool]]`, `planner: Any`, `selectors: ProviderSelectors`, `error_markers: tuple[str, ...]`, `recoverable_codes: tuple[str, ...]`, `answer_timeout_s: float | None = None`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_profile.py`:

```python
import re

from ai_router.browser.profile import ProviderProfile, ProviderSelectors, StreamDone


def _dummy_parse(status: int, body: str) -> StreamDone:
    return StreamDone(done=False, ok=False)


async def _dummy_bool(page) -> bool:
    return False


async def _dummy_snapshot(page) -> tuple[int, str]:
    return 0, ""


def test_stream_done_defaults():
    d = StreamDone(done=True, ok=True)
    assert d.error_kind is None
    assert d.error_text is None


def test_provider_profile_roundtrip():
    profile = ProviderProfile(
        provider_id="dummy",
        stream_url_re=re.compile(r"/stream"),
        parse_stream_done=_dummy_parse,
        is_stop_visible=_dummy_bool,
        read_response_snapshot=_dummy_snapshot,
        is_rate_limited=lambda text: False,
        submit_ready=_dummy_bool,
        planner=None,
        selectors=ProviderSelectors(prompt_input="#input", submit_button="#send"),
        error_markers=("boom",),
        recoverable_codes=("DUMMY_ERROR",),
    )
    assert profile.answer_timeout_s is None
    assert profile.selectors.prompt_input == "#input"
    assert profile.stream_url_re.search("https://x/stream")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_profile.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ai_router.browser.profile'`

- [ ] **Step 3: Write minimal implementation**

Create `src/ai_router/browser/profile.py`:

```python
from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from playwright.async_api import Page


@dataclass(frozen=True)
class StreamDone:
    """Verdict from parsing one finished provider stream response.

    error_kind: None | "rate_limit" | "moderation" | "incomplete" | "error"
    """

    done: bool
    ok: bool
    error_kind: str | None = None
    error_text: str | None = None


@dataclass(frozen=True)
class ProviderSelectors:
    prompt_input: str
    submit_button: str


@dataclass
class ProviderProfile:
    """Everything provider-specific the browser layer needs, in one place."""

    provider_id: str
    stream_url_re: re.Pattern[str]
    parse_stream_done: Callable[[int, str], StreamDone]
    is_stop_visible: Callable[[Page], Awaitable[bool]]
    read_response_snapshot: Callable[[Page], Awaitable[tuple[int, str]]]
    is_rate_limited: Callable[[str], bool]
    submit_ready: Callable[[Page], Awaitable[bool]]
    planner: Any
    selectors: ProviderSelectors
    error_markers: tuple[str, ...]
    recoverable_codes: tuple[str, ...]
    answer_timeout_s: float | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_profile.py -v`
Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add src/ai_router/browser/profile.py tests/test_profile.py
git commit -m "feat: add ProviderProfile/StreamDone provider abstraction types"
```

---

### Task 2: Gemini profile — `parse_stream_done`, `send_button_ready`, `build_profile`

**Files:**
- Modify: `src/ai_router/adapters/gemini/wait.py`
- Modify: `src/ai_router/adapters/gemini/adapter.py`
- Test: `tests/test_gemini_wait.py` (append), `tests/test_gemini_profile.py` (create)

**Interfaces:**
- Consumes: `StreamDone`, `ProviderProfile`, `ProviderSelectors` from Task 1; existing `is_stream_end`, `is_stop_visible`, `read_response_snapshot`, `is_rate_limited`, `GeminiPlanner`, selectors.
- Produces:
  - `ai_router.adapters.gemini.wait.parse_stream_done(status: int, body: str) -> StreamDone`
  - `ai_router.adapters.gemini.wait.send_button_ready(page: Page) -> bool` (async; logic moved verbatim from `CommandExecutor._send_button_ready`)
  - `GeminiAdapter.build_profile(cfg: AppConfig) -> ProviderProfile` with `provider_id="gemini"`, `recoverable_codes=("GEMINI_ERROR",)`, `answer_timeout_s=None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gemini_wait.py`:

```python
from ai_router.adapters.gemini.wait import parse_stream_done


def test_parse_stream_done_detects_end_tag():
    body = '[["e", 9, null, null, 5347]]'
    result = parse_stream_done(200, body)
    assert result.done is True
    assert result.ok is True
    assert result.error_kind is None


def test_parse_stream_done_not_done_without_tag():
    result = parse_stream_done(200, '["rc_123", "some chunk"]')
    assert result.done is False
```

Create `tests/test_gemini_profile.py`:

```python
from pathlib import Path

from ai_router.adapters.gemini.adapter import GeminiAdapter
from ai_router.adapters.gemini.selectors import SEL_PROMPT_INPUT
from ai_router.config import AppConfig


def make_config() -> AppConfig:
    return AppConfig(
        profile_dir=Path("profile"),
        default_provider="gemini",
        host="127.0.0.1",
        port=0,
        answer_timeout_s=120,
    )


def test_build_profile_wires_gemini_pieces():
    profile = GeminiAdapter().build_profile(make_config())
    assert profile.provider_id == "gemini"
    assert profile.recoverable_codes == ("GEMINI_ERROR",)
    assert profile.answer_timeout_s is None
    assert profile.selectors.prompt_input == SEL_PROMPT_INPUT
    assert profile.stream_url_re.search(
        "https://gemini.google.com/_/BardChatUi/data/"
        "assistant.lamda.BardFrontendService/StreamGenerate"
    )
    assert profile.parse_stream_done(200, '[["e", 9]]').ok is True
    assert profile.planner.plan.__name__ == "plan"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_gemini_wait.py tests/test_gemini_profile.py -v`
Expected: FAIL — `ImportError: cannot import name 'parse_stream_done'` and `AttributeError: 'GeminiAdapter' object has no attribute 'build_profile'`

- [ ] **Step 3: Implement**

In `src/ai_router/adapters/gemini/wait.py`, add ONE import at top (after the existing `ai_router` imports; `SEL_SEND_CONTAINER` is already imported in this file):

```python
from ai_router.browser.profile import StreamDone
```

Append to `src/ai_router/adapters/gemini/wait.py`:

```python
def parse_stream_done(status: int, body: str) -> StreamDone:
    """Gemini StreamGenerate: done when the end-of-turn ["e", ...] tag appears."""
    if is_stream_end(body):
        return StreamDone(done=True, ok=True)
    return StreamDone(done=False, ok=False)


async def send_button_ready(page: Page) -> bool:
    """True when Gemini's Send control is present and enabled."""
    container = page.locator(SEL_SEND_CONTAINER).last
    if await container.count() == 0:
        return False
    wrapper = container.locator("gem-icon-button.send-button.submit").first
    if await wrapper.count() > 0:
        return await wrapper.get_attribute("aria-disabled") != "true"
    submit = container.locator('button[aria-label="Send message"]').first
    if await submit.count() == 0:
        return False
    return not await submit.is_disabled()
```

Replace `src/ai_router/adapters/gemini/adapter.py` with:

```python
from __future__ import annotations

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from ai_router.adapters.base import SessionStatus
from ai_router.adapters.gemini.planner import GeminiPlanner
from ai_router.adapters.gemini.selectors import (
    GEMINI_ERROR_MARKERS,
    GEMINI_URL,
    SEL_PROMPT_INPUT,
    SEL_SIGN_IN,
    SEL_SUBMIT_BUTTON,
    STREAM_GENERATE_RE,
)
from ai_router.adapters.gemini.wait import (
    is_rate_limited,
    is_stop_visible,
    parse_stream_done,
    read_response_snapshot,
    send_button_ready,
)
from ai_router.browser.profile import ProviderProfile, ProviderSelectors
from ai_router.config import AppConfig


class GeminiAdapter:
    id = "gemini"
    name = "Gemini"
    keywords = ["gemini", "@gemini", "google gemini"]
    status = "available"

    async def check_session(self, page: Page) -> SessionStatus:
        return await self.ensure_page_ready(page)

    async def ensure_page_ready(self, page: Page) -> SessionStatus:
        """Open a fresh chat and verify login."""
        await page.goto(GEMINI_URL, wait_until="domcontentloaded")
        try:
            await page.wait_for_selector(SEL_PROMPT_INPUT, timeout=15_000)
            return SessionStatus.LOGGED_IN
        except PlaywrightTimeout:
            if await page.locator(SEL_SIGN_IN).count() > 0:
                return SessionStatus.LOGGED_OUT
            return SessionStatus.UNKNOWN

    async def open_new_chat(self, page: Page) -> None:
        await page.goto(GEMINI_URL, wait_until="domcontentloaded")

    def build_profile(self, cfg: AppConfig) -> ProviderProfile:
        return ProviderProfile(
            provider_id=self.id,
            stream_url_re=STREAM_GENERATE_RE,
            parse_stream_done=parse_stream_done,
            is_stop_visible=is_stop_visible,
            read_response_snapshot=read_response_snapshot,
            is_rate_limited=is_rate_limited,
            submit_ready=send_button_ready,
            planner=GeminiPlanner(),
            selectors=ProviderSelectors(
                prompt_input=SEL_PROMPT_INPUT,
                submit_button=SEL_SUBMIT_BUTTON,
            ),
            error_markers=GEMINI_ERROR_MARKERS,
            recoverable_codes=("GEMINI_ERROR",),
            answer_timeout_s=None,
        )
```

- [ ] **Step 4: Run tests**

Run: `poetry run pytest tests/test_gemini_wait.py tests/test_gemini_profile.py -v`
Expected: all PASS

Run: `poetry run pytest`
Expected: full suite PASS (nothing else touched yet)

- [ ] **Step 5: Commit**

```bash
git add src/ai_router/adapters/gemini tests/test_gemini_wait.py tests/test_gemini_profile.py
git commit -m "feat: gemini exposes ProviderProfile (parse_stream_done, send_button_ready, build_profile)"
```

---

### Task 3: `StateReducer` — generic stream regexes + stream-error path

**Files:**
- Modify: `src/ai_router/browser/state.py`
- Modify: `tests/test_state_reducer.py` (rewrite), `tests/test_command_waits.py` (rewrite)

**Interfaces:**
- Consumes: nothing new (regex list passed in).
- Produces:
  - `StateReducer.__init__(*, page_id, stream_url_res: Sequence[re.Pattern[str]], idle_streak_required, generating_streak_required, answer_stable_ticks, stream_quiet_s, error_markers)` — NEW required kwarg `stream_url_res`; no more gemini import.
  - `StateReducer.apply_stream_end(*, url: str = "", ok: bool = True, error_kind: str | None = None, error_text: str | None = None)` — `ok=False` sets `state.error_kind`, `state.error_text`, phase `"error"`, and does NOT set `saw_stream_end_this_job`.
  - `BrowserState.error_kind: str | None = None` — new field; cleared (with `error_text`) in `reset_job_cycle`.

- [ ] **Step 1: Write the failing tests (rewrite both test files with a shared helper)**

Replace `tests/test_state_reducer.py` with:

```python
import time

from ai_router.adapters.gemini.selectors import STREAM_GENERATE_RE
from ai_router.browser.state import StateReducer

GEMINI_STREAM_URL = (
    "https://gemini.google.com/_/BardChatUi/data/"
    "assistant.lamda.BardFrontendService/StreamGenerate"
)


def make_reducer(**overrides) -> StateReducer:
    kwargs = dict(
        page_id="test",
        stream_url_res=[STREAM_GENERATE_RE],
        idle_streak_required=3,
        generating_streak_required=2,
        answer_stable_ticks=2,
        stream_quiet_s=1.5,
        error_markers=(),
    )
    kwargs.update(overrides)
    return StateReducer(**kwargs)


def test_idle_after_quiet_dom_ticks():
    r = make_reducer()
    for _ in range(3):
        r.apply_dom_tick(generating=False, response_count=0, response_text="", error_text=None)
    assert r.state.phase == "idle"
    assert r.state.idle_streak == 3


def test_generating_when_stop_visible():
    r = make_reducer()
    r.apply_dom_tick(generating=True, response_count=0, response_text="", error_text=None)
    r.apply_dom_tick(generating=True, response_count=0, response_text="", error_text=None)
    assert r.state.phase == "generating"
    assert r.state.generating_streak == 2


def test_error_on_1095_marker():
    r = make_reducer(error_markers=("something went wrong",))
    r.apply_dom_tick(
        generating=False,
        response_count=0,
        response_text="",
        error_text="Something went wrong (1095)",
    )
    assert r.state.phase == "error"


def test_stream_generate_sets_timestamp():
    r = make_reducer()
    r.mark_submitting()
    before = time.time()
    r.apply_request_finished(GEMINI_STREAM_URL)
    assert r.state.last_stream_at is not None
    assert r.state.last_stream_at >= before
    assert r.state.saw_generating_this_job is True


def test_stream_end_ignored_without_submit():
    r = make_reducer()
    r.apply_stream_end()
    assert r.state.saw_stream_end_this_job is False


def test_stream_end_reset_when_stream_resumes():
    r = make_reducer(stream_quiet_s=5.0)
    r.mark_submitting()
    r.apply_request_finished(GEMINI_STREAM_URL)
    r.apply_stream_end()
    assert r.state.saw_stream_end_this_job is True
    time.sleep(0.01)
    r.apply_request_finished(GEMINI_STREAM_URL)
    assert r.state.saw_stream_end_this_job is False
    assert r.state.stream_ended_at is None


def test_stream_end_requires_stream_after_submit():
    r = make_reducer()
    r.mark_submitting()
    r.apply_stream_end()
    assert r.state.saw_stream_end_this_job is False
    r.apply_request_finished(GEMINI_STREAM_URL)
    r.apply_stream_end()
    assert r.state.saw_stream_end_this_job is True


def test_request_finished_ignores_non_stream_urls():
    r = make_reducer()
    r.mark_submitting()
    r.apply_request_finished("https://gemini.google.com/_/BardChatUi/other")
    assert r.state.last_stream_at is None


def test_stream_end_error_sets_error_phase():
    r = make_reducer()
    r.mark_submitting()
    r.apply_request_finished(GEMINI_STREAM_URL)
    r.apply_stream_end(ok=False, error_kind="moderation", error_text="blocked")
    assert r.state.phase == "error"
    assert r.state.error_kind == "moderation"
    assert r.state.error_text == "blocked"
    assert r.state.saw_stream_end_this_job is False


def test_stream_end_error_ignored_when_stale():
    r = make_reducer()
    r.apply_stream_end(ok=False, error_kind="error", error_text="boom")
    assert r.state.phase == "idle"
    assert r.state.error_kind is None


def test_reset_job_cycle_clears_error():
    r = make_reducer()
    r.mark_submitting()
    r.apply_request_finished(GEMINI_STREAM_URL)
    r.apply_stream_end(ok=False, error_kind="incomplete", error_text="cut off")
    r.reset_job_cycle()
    assert r.state.error_kind is None
    assert r.state.error_text is None
```

Replace `tests/test_command_waits.py` with:

```python
from ai_router.adapters.gemini.selectors import STREAM_GENERATE_RE
from ai_router.browser.state import StateReducer

GEMINI_STREAM_URL = (
    "https://gemini.google.com/_/BardChatUi/data/"
    "assistant.lamda.BardFrontendService/StreamGenerate"
)


def make_reducer(**overrides) -> StateReducer:
    kwargs = dict(
        page_id="test",
        stream_url_res=[STREAM_GENERATE_RE],
        idle_streak_required=3,
        generating_streak_required=2,
        answer_stable_ticks=2,
        stream_quiet_s=1.5,
        error_markers=(),
    )
    kwargs.update(overrides)
    return StateReducer(**kwargs)


def test_answer_not_ready_without_generating_phase():
    r = make_reducer(error_markers=("something went wrong",))
    for _ in range(3):
        r.apply_dom_tick(generating=False, response_count=1, response_text="hi", error_text=None)
    assert r.answer_ready(before_count=0) is False


def test_answer_ready_after_full_cycle():
    r = make_reducer(idle_streak_required=2, generating_streak_required=1, stream_quiet_s=0.0)
    r.apply_dom_tick(generating=True, response_count=0, response_text="", error_text=None)
    r.apply_dom_tick(generating=False, response_count=1, response_text="answer", error_text=None)
    r.apply_dom_tick(generating=False, response_count=1, response_text="answer", error_text=None)
    assert r.answer_ready(before_count=0) is True


def test_answer_not_ready_with_stream_end_while_stop_visible():
    r = make_reducer(idle_streak_required=6, stream_quiet_s=0.0)
    r.apply_dom_tick(generating=True, response_count=0, response_text="", error_text=None)
    r.mark_submitting()
    r.apply_request_finished(GEMINI_STREAM_URL)
    r.apply_stream_end()
    r.apply_dom_tick(generating=True, response_count=1, response_text="answer", error_text=None)
    r.apply_dom_tick(generating=True, response_count=1, response_text="answer", error_text=None)
    checks = r.answer_ready_checks(before_count=0, generating=True)
    assert r.answer_ready(before_count=0, generating=True) is False
    assert checks["stream_end"] is True
    assert checks["stream_quiet"] is False


def test_answer_ready_after_stream_end_and_stop_gone():
    r = make_reducer(idle_streak_required=6, stream_quiet_s=0.0)
    r.apply_dom_tick(generating=True, response_count=0, response_text="", error_text=None)
    r.mark_submitting()
    r.apply_request_finished(GEMINI_STREAM_URL)
    r.apply_stream_end()
    r.apply_dom_tick(generating=False, response_count=1, response_text="answer", error_text=None)
    r.apply_dom_tick(generating=False, response_count=1, response_text="answer", error_text=None)
    checks = r.answer_ready_checks(before_count=0, generating=False)
    assert r.answer_ready(before_count=0, generating=False) is True
    assert checks["stream_end"] is True
    assert checks["stream_quiet"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_state_reducer.py tests/test_command_waits.py -v`
Expected: FAIL — `TypeError: StateReducer.__init__() got an unexpected keyword argument 'stream_url_res'`

- [ ] **Step 3: Implement reducer changes**

In `src/ai_router/browser/state.py`:

1. Replace the imports block at top:

```python
from __future__ import annotations

import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from ai_router.logger import trace
```

(The `from ai_router.adapters.gemini.selectors import STREAM_GENERATE_RE` line is deleted.)

2. Add field to `BrowserState` (after `error_text: str | None = None`):

```python
    error_kind: str | None = None
```

3. Change `__init__` signature and store the regex list:

```python
    def __init__(
        self,
        *,
        page_id: str,
        stream_url_res: Sequence[re.Pattern[str]],
        idle_streak_required: int,
        generating_streak_required: int,
        answer_stable_ticks: int,
        stream_quiet_s: float,
        error_markers: tuple[str, ...],
    ) -> None:
        self._page_id = page_id
        self._stream_url_res = tuple(stream_url_res)
        self._job_id: str | None = None
        self._idle_required = idle_streak_required
        self._gen_required = generating_streak_required
        self._answer_stable = answer_stable_ticks
        self._stream_quiet_s = stream_quiet_s
        self._error_markers = error_markers
        self.state = BrowserState()
```

4. Add a match helper and use it in `apply_request_finished` (replace the `STREAM_GENERATE_RE.search(url)` check):

```python
    def _matches_stream(self, url: str) -> bool:
        return any(rx.search(url) for rx in self._stream_url_res)

    def apply_request_finished(self, url: str) -> None:
        if not self._matches_stream(url):
            return
        # ... rest unchanged
```

5. Extend `reset_job_cycle` — add these two lines at the end of the method:

```python
        st.error_text = None
        st.error_kind = None
```

6. Replace `apply_stream_end` with:

```python
    def apply_stream_end(
        self,
        *,
        url: str = "",
        ok: bool = True,
        error_kind: str | None = None,
        error_text: str | None = None,
    ) -> None:
        st = self.state
        if st.submitted_at is None:
            trace(
                "stream_end_ignored",
                page_id=self._page_id,
                job_id=self._job_id,
                reason="not_submitted",
            )
            return
        if not self._stream_belongs_to_job():
            trace(
                "stream_end_ignored",
                page_id=self._page_id,
                job_id=self._job_id,
                reason="stale_stream",
            )
            return
        if not ok:
            st.error_kind = error_kind or "error"
            st.error_text = error_text or "Provider stream error"
            self._set_phase("error", reason=f"stream_{st.error_kind}")
            return
        st.saw_stream_end_this_job = True
        st.stream_ended_at = time.time()
        st.saw_generating_this_job = True
        trace(
            "stream_end",
            page_id=self._page_id,
            job_id=self._job_id,
            url=url[:80] if url else None,
        )
```

Note: `test_stream_end_error_ignored_when_stale` exercises the `submitted_at is None` guard — an error verdict from a stale stream must not poison the state.

- [ ] **Step 4: Run tests**

Run: `poetry run pytest tests/test_state_reducer.py tests/test_command_waits.py -v`
Expected: all PASS

Run: `poetry run pytest`
Expected: FAIL is NOT allowed — but `page_worker.py` still constructs `StateReducer` without `stream_url_res`. Fix forward immediately: in `src/ai_router/browser/page_worker.py`, inside `PageWorker.__init__`, add the argument using the still-imported gemini regex (temporary until Task 6):

```python
from ai_router.adapters.gemini.selectors import GEMINI_ERROR_MARKERS, STREAM_GENERATE_RE
...
        self._reducer = StateReducer(
            page_id=self._page_id,
            stream_url_res=[STREAM_GENERATE_RE],
            idle_streak_required=cfg.idle_streak_required,
            ...
        )
```

Then run: `poetry run pytest`
Expected: full suite PASS

- [ ] **Step 5: Commit**

```bash
git add src/ai_router/browser/state.py src/ai_router/browser/page_worker.py tests/test_state_reducer.py tests/test_command_waits.py
git commit -m "refactor: StateReducer takes stream regex list; stream_end carries ok/error verdict"
```

---

### Task 4: `events.py` — profile-dispatched response inspection

**Files:**
- Modify: `src/ai_router/browser/events.py`
- Test: `tests/test_events.py` (create)

**Interfaces:**
- Consumes: `ProviderProfile` (Task 1), `GeminiAdapter.build_profile` (Task 2).
- Produces:
  - `handle_response(response, channel: EventChannel, profiles: Sequence[ProviderProfile]) -> None` (async, module-level, testable): finds the first profile whose `stream_url_re` matches `response.url`; awaits `response.finished()`; calls `profile.parse_stream_done(response.status, body)`; if `.done`, emits `stream_end` with payload `url, ok, error_kind, error_text`.
  - `attach_listeners(page: Page, channel: EventChannel, profiles: Sequence[ProviderProfile]) -> None` — NEW third required arg.

- [ ] **Step 1: Write the failing test**

Create `tests/test_events.py`:

```python
from pathlib import Path

from ai_router.adapters.gemini.adapter import GeminiAdapter
from ai_router.browser.events import EventChannel, handle_response
from ai_router.config import AppConfig

STREAM_URL = (
    "https://gemini.google.com/_/BardChatUi/data/"
    "assistant.lamda.BardFrontendService/StreamGenerate"
)


def make_profile():
    cfg = AppConfig(
        profile_dir=Path("profile"),
        default_provider="gemini",
        host="127.0.0.1",
        port=0,
        answer_timeout_s=120,
    )
    return GeminiAdapter().build_profile(cfg)


class FakeResponse:
    def __init__(self, url: str, status: int, body: str) -> None:
        self.url = url
        self.status = status
        self._body = body

    async def finished(self) -> None:
        return None

    async def text(self) -> str:
        return self._body


async def test_handle_response_emits_stream_end_on_done():
    channel = EventChannel("p1")
    resp = FakeResponse(STREAM_URL, 200, '[["e", 9, null, null, 5347]]')
    await handle_response(resp, channel, [make_profile()])
    ev = channel.try_get_nowait()
    assert ev is not None
    assert ev.kind == "stream_end"
    assert ev.payload["ok"] is True
    assert ev.payload["error_kind"] is None


async def test_handle_response_silent_when_not_done():
    channel = EventChannel("p1")
    resp = FakeResponse(STREAM_URL, 200, '["rc_1", "partial chunk"]')
    await handle_response(resp, channel, [make_profile()])
    assert channel.try_get_nowait() is None


async def test_handle_response_ignores_unmatched_url():
    channel = EventChannel("p1")
    resp = FakeResponse("https://gemini.google.com/other", 200, '[["e", 9]]')
    await handle_response(resp, channel, [make_profile()])
    assert channel.try_get_nowait() is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_events.py -v`
Expected: FAIL — `ImportError: cannot import name 'handle_response'`

- [ ] **Step 3: Implement**

In `src/ai_router/browser/events.py`:

1. Replace the two gemini imports with profile import:

```python
from collections.abc import Awaitable, Callable, Sequence
...
from ai_router.browser.profile import ProviderProfile
```

(Delete `from ai_router.adapters.gemini.selectors import STREAM_GENERATE_RE` and `from ai_router.adapters.gemini.wait import is_stream_end`. Note `Sequence` joins the existing `collections.abc` import.)

2. Add module-level `handle_response` and rework `attach_listeners`:

```python
async def handle_response(
    response: Any, channel: EventChannel, profiles: Sequence[ProviderProfile]
) -> None:
    profile = next(
        (p for p in profiles if p.stream_url_re.search(response.url)), None
    )
    if profile is None:
        return
    try:
        await response.finished()
        status = response.status
        body = await response.text()
    except Exception:
        return
    result = profile.parse_stream_done(status, body)
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

    page.on("requestfinished", on_request_finished)
    page.on("response", on_response)
    page.on("framenavigated", on_framenavigated)
```

3. Temporary fix-forward in `src/ai_router/browser/page_worker.py` (until Task 6 removes it): at top add `from ai_router.adapters.gemini.adapter import GeminiAdapter`; at the end of `PageWorker.__init__` add:

```python
        self._profile = GeminiAdapter().build_profile(cfg)  # temporary — Task 6 injects profiles
```

and in `_run()` replace `attach_listeners(self._page, self._channel)` with:

```python
        attach_listeners(self._page, self._channel, [self._profile])
```

- [ ] **Step 4: Run tests**

Run: `poetry run pytest tests/test_events.py -v` → 3 PASS
Run: `poetry run pytest` → full suite PASS

- [ ] **Step 5: Commit**

```bash
git add src/ai_router/browser/events.py src/ai_router/browser/page_worker.py tests/test_events.py
git commit -m "refactor: events dispatch stream responses through ProviderProfile"
```

---

### Task 5: `CommandExecutor` — profile injection + provider error mapping

**Files:**
- Modify: `src/ai_router/browser/commands.py`
- Test: `tests/test_command_errors.py` (create)

**Interfaces:**
- Consumes: `ProviderProfile` (Task 1), gemini profile (Task 2), reducer `error_kind` (Task 3).
- Produces:
  - `CommandExecutor.__init__(page, reducer, *, profile: ProviderProfile, job_id, page_id, answer_timeout_s, idle_streak_required)` — `profile` replaces all gemini imports.
  - `CommandExecutor._provider_error() -> AiRouterError` — maps `state.error_kind`: `"rate_limit"` → `RateLimitedError(error_text)`; `"moderation"` → `AiRouterError("<ID>_MODERATION", ...)`; `"incomplete"` → `AiRouterError("<ID>_INCOMPLETE", ...)`; anything else → `AiRouterError("<ID>_ERROR", ...)` where `<ID>` is `profile.provider_id.upper()` (so gemini keeps raising `GEMINI_ERROR`).

- [ ] **Step 1: Write the failing test**

Create `tests/test_command_errors.py`:

```python
from pathlib import Path

import pytest

from ai_router.adapters.gemini.adapter import GeminiAdapter
from ai_router.adapters.gemini.selectors import STREAM_GENERATE_RE
from ai_router.browser.commands import CommandExecutor
from ai_router.browser.state import StateReducer
from ai_router.config import AppConfig
from ai_router.errors import RateLimitedError


def make_executor() -> CommandExecutor:
    cfg = AppConfig(
        profile_dir=Path("profile"),
        default_provider="gemini",
        host="127.0.0.1",
        port=0,
        answer_timeout_s=5,
    )
    reducer = StateReducer(
        page_id="p",
        stream_url_res=[STREAM_GENERATE_RE],
        idle_streak_required=1,
        generating_streak_required=1,
        answer_stable_ticks=1,
        stream_quiet_s=0.0,
        error_markers=(),
    )
    return CommandExecutor(
        None,
        reducer,
        profile=GeminiAdapter().build_profile(cfg),
        job_id="j1",
        page_id="p",
        answer_timeout_s=5.0,
        idle_streak_required=1,
    )


def test_rate_limit_maps_to_rate_limited_error():
    ex = make_executor()
    ex._reducer.state.error_kind = "rate_limit"
    ex._reducer.state.error_text = "HTTP 429"
    err = ex._provider_error()
    assert isinstance(err, RateLimitedError)
    assert "429" in err.message


def test_moderation_maps_to_provider_moderation_code():
    ex = make_executor()
    ex._reducer.state.error_kind = "moderation"
    ex._reducer.state.error_text = "blocked"
    err = ex._provider_error()
    assert err.code == "GEMINI_MODERATION"


def test_incomplete_maps_to_provider_incomplete_code():
    ex = make_executor()
    ex._reducer.state.error_kind = "incomplete"
    err = ex._provider_error()
    assert err.code == "GEMINI_INCOMPLETE"


def test_default_maps_to_provider_error_code():
    ex = make_executor()
    ex._reducer.state.error_kind = None
    ex._reducer.state.error_text = "something went wrong"
    err = ex._provider_error()
    assert err.code == "GEMINI_ERROR"
    assert err.message == "something went wrong"


def test_pytest_can_instantiate_without_page_calls():
    # ctor must not touch the page — page interactions happen in run()
    assert make_executor() is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_command_errors.py -v`
Expected: FAIL — `TypeError: CommandExecutor.__init__() got an unexpected keyword argument 'profile'`

- [ ] **Step 3: Implement**

In `src/ai_router/browser/commands.py`:

1. Replace the gemini imports at top with:

```python
from ai_router.browser.profile import ProviderProfile
```

(Delete both `from ai_router.adapters.gemini.selectors import ...` and `from ai_router.adapters.gemini.wait import ...` blocks.)

2. New ctor:

```python
    def __init__(
        self,
        page: Page,
        reducer: StateReducer,
        *,
        profile: ProviderProfile,
        job_id: str,
        page_id: str,
        answer_timeout_s: float,
        idle_streak_required: int,
    ) -> None:
        self._page = page
        self._reducer = reducer
        self._profile = profile
        self._job_id = job_id
        self._page_id = page_id
        self._answer_timeout_s = answer_timeout_s
        self._idle_streak_required = idle_streak_required
        self._last_prompt_len = 0
        self._response_count_at_submit = 0
```

3. Add the error mapper method:

```python
    def _provider_error(self) -> AiRouterError:
        st = self._reducer.state
        prefix = self._profile.provider_id.upper()
        if st.error_kind == "rate_limit":
            return RateLimitedError(st.error_text or "Rate limited")
        code = {
            "moderation": f"{prefix}_MODERATION",
            "incomplete": f"{prefix}_INCOMPLETE",
        }.get(st.error_kind or "", f"{prefix}_ERROR")
        return AiRouterError(code, st.error_text or "Provider error")
```

4. Mechanical substitutions throughout the class:
   - `read_response_snapshot(self._page)` → `self._profile.read_response_snapshot(self._page)` (3 sites: `run`, `submit` branch, `_verify_submitted`, `_wait_answer` poll logging, `_wait_answer` timeout block — replace ALL).
   - `is_stop_visible(self._page)` → `self._profile.is_stop_visible(self._page)` (in `_verify_submitted`, `_generating_started`, `_wait_answer`, `_wait_idle` — replace ALL).
   - `is_rate_limited(answer)` → `self._profile.is_rate_limited(answer)` (in `_wait_answer`).
   - `SEL_PROMPT_INPUT` → `self._profile.selectors.prompt_input` (in `_clear_input`, `_type`, `_input_text`, `_try_enter_submit`).
   - The three `raise AiRouterError("GEMINI_ERROR", self._reducer.state.error_text or "Gemini error")` sites (in `_wait_generating_started`, `_wait_answer`, `_wait_idle`) → `raise self._provider_error()`.

5. Replace `_send_button_ready` + `_try_send_click` with a selector/profile-driven version (delete `_send_button_ready` entirely):

```python
    async def _try_send_click(self) -> bool:
        submit = self._page.locator(self._profile.selectors.submit_button).last
        try:
            await submit.wait_for(state="visible", timeout=5000)
        except Exception:
            trace(
                "submit_no_button",
                page_id=self._page_id,
                job_id=self._job_id,
            )
            return False

        for _ in range(50):
            if await self._profile.submit_ready(self._page):
                break
            await asyncio.sleep(0.1)
        else:
            trace(
                "submit_disabled",
                page_id=self._page_id,
                job_id=self._job_id,
                action="not_ready",
            )
            return False

        await submit.click(force=True)
        trace(
            "submit_click",
            page_id=self._page_id,
            job_id=self._job_id,
            disabled=False,
        )
        return True
```

6. Fix-forward the construction site in `src/ai_router/browser/page_worker.py` `_execute_job` (temporary until Task 6 — reuse the `self._profile` that Task 4's fix already stores in `__init__`):

```python
        executor = CommandExecutor(
            self._page,
            self._reducer,
            profile=self._profile,
            job_id=job.job_id,
            page_id=self._page_id,
            answer_timeout_s=job.timeout_s,
            idle_streak_required=self._cfg.idle_streak_required,
        )
```

- [ ] **Step 4: Run tests**

Run: `poetry run pytest tests/test_command_errors.py -v` → 5 PASS
Run: `poetry run pytest` → full suite PASS

- [ ] **Step 5: Commit**

```bash
git add src/ai_router/browser/commands.py src/ai_router/browser/page_worker.py tests/test_command_errors.py
git commit -m "refactor: CommandExecutor consumes ProviderProfile; provider-scoped error codes"
```

---

### Task 6: `PageWorker` + `mcp/tools.py` — profiles wired end-to-end

**Files:**
- Modify: `src/ai_router/browser/page_worker.py`
- Modify: `src/ai_router/mcp/tools.py`
- Modify: `src/ai_router/adapters/base.py`
- Modify: `tests/test_tools_stateless.py`

**Interfaces:**
- Consumes: everything from Tasks 1–5.
- Produces:
  - `PageWorker.__init__(page, queue, cfg, profiles: dict[str, ProviderProfile], default_provider: str)` — worker holds `self._profiles`; `self._profile` is the active one (default provider's at start, switched per job in `_execute_job`).
  - `AppState.profiles: dict[str, ProviderProfile]` — built in `create_app_state` from `adapter.build_profile(cfg)` for every `status == "available"` adapter that has `build_profile`.
  - `handle_ask` uses `profile.answer_timeout_s or config.answer_timeout_s` for `AskJob.timeout_s`.
  - `ProviderAdapter` protocol gains `def build_profile(self, cfg: AppConfig) -> ProviderProfile: ...`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_tools_stateless.py`, replace `FakeWorker` and add a timeout test at the end:

```python
class FakeWorker:
    def __init__(self, page, queue, config, profiles, default_provider) -> None:
        self.jobs = []

    def start(self) -> None:
        pass

    async def enqueue(self, job) -> None:
        self.jobs.append(job)
        job.future.set_result("fake answer")
```

Append:

```python
async def test_ask_uses_provider_timeout_override(state, adapter, monkeypatch) -> None:
    profile = state.profiles["gemini"]
    monkeypatch.setattr(profile, "answer_timeout_s", 300.0)
    await handle_ask(state, prompt="hi", provider=None, mcp_session_id=None)
    worker = next(iter(state.page_workers.values()))
    assert worker.jobs[0].timeout_s == 300.0


async def test_ask_uses_config_timeout_by_default(state, adapter) -> None:
    await handle_ask(state, prompt="hi", provider=None, mcp_session_id=None)
    worker = next(iter(state.page_workers.values()))
    assert worker.jobs[0].timeout_s == 5.0


def test_app_state_builds_available_profiles() -> None:
    st = create_app_state(make_config())
    assert "gemini" in st.profiles
    assert st.profiles["gemini"].provider_id == "gemini"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_tools_stateless.py -v`
Expected: FAIL — `AttributeError: 'AppState' object has no attribute 'profiles'` (and/or FakeWorker signature mismatch)

- [ ] **Step 3: Implement**

**`src/ai_router/adapters/base.py`** — extend the protocol:

```python
from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Literal, Protocol

from playwright.async_api import Page

if TYPE_CHECKING:
    from ai_router.browser.profile import ProviderProfile
    from ai_router.config import AppConfig


class SessionStatus(str, Enum):
    LOGGED_IN = "logged_in"
    LOGGED_OUT = "logged_out"
    UNKNOWN = "unknown"


ProviderStatus = Literal["available", "coming_soon"]


class ProviderAdapter(Protocol):
    id: str
    name: str
    keywords: list[str]
    status: ProviderStatus

    async def check_session(self, page: Page) -> SessionStatus: ...
    async def open_new_chat(self, page: Page) -> None: ...
    def build_profile(self, cfg: AppConfig) -> ProviderProfile: ...
```

**`src/ai_router/browser/page_worker.py`** — remove ALL gemini imports (including the Task 4/5 temporary `GeminiAdapter` import) and rewrite init/dispatch:

Imports block becomes:

```python
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
```

`__init__` becomes:

```python
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
            stream_quiet_s=cfg.stream_quiet_s,
            error_markers=tuple({m for p in profiles.values() for m in p.error_markers}),
        )
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._running_job_id: str | None = None
```

In `_run()`, the listener line becomes:

```python
        attach_listeners(self._page, self._channel, list(self._profiles.values()))
```

`_pump_events` stream_end branch becomes:

```python
            elif ev.kind == "stream_end":
                self._reducer.apply_stream_end(
                    url=ev.payload.get("url", ""),
                    ok=ev.payload.get("ok", True),
                    error_kind=ev.payload.get("error_kind"),
                    error_text=ev.payload.get("error_text"),
                )
```

`_dom_snapshot` becomes profile-driven:

```python
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
```

`_execute_job` becomes:

```python
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
```

**`src/ai_router/mcp/tools.py`**:

1. Add import: `from ai_router.browser.profile import ProviderProfile`.
2. `AppState` gains field `profiles: dict[str, ProviderProfile]`.
3. `create_app_state` becomes:

```python
def create_app_state(config: AppConfig | None = None) -> AppState:
    cfg = config or load_config()
    registry = build_registry()
    profiles = {
        a.id: a.build_profile(cfg)
        for a in registry.list_all()
        if a.status == "available" and hasattr(a, "build_profile")
    }
    return AppState(
        config=cfg,
        registry=registry,
        browser=BrowserManager(cfg),
        page_queues=PageQueueRegistry(),
        page_workers={},
        profiles=profiles,
    )
```

4. `ensure_worker` construction line becomes:

```python
        worker = PageWorker(
            page, queue, state.config, state.profiles, state.config.default_provider
        )
```

5. In `handle_ask`, replace the `AskJob(... timeout_s=float(state.config.answer_timeout_s))` with:

```python
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
```

- [ ] **Step 4: Run tests**

Run: `poetry run pytest tests/test_tools_stateless.py -v` → all PASS
Run: `poetry run pytest` → full suite PASS
Run: `poetry run ruff check src tests` → clean (gemini imports fully gone from browser layer)
Verify decoupling: `grep -r "gemini" src/ai_router/browser/` → no matches

- [ ] **Step 5: Commit**

```bash
git add src/ai_router/browser/page_worker.py src/ai_router/mcp/tools.py src/ai_router/adapters/base.py tests/test_tools_stateless.py
git commit -m "refactor: browser layer fully generic over ProviderProfile"
```

---

### Task 7: ChatGPT SSE parser (`chatgpt/stream.py`) — the core deliverable

**Files:**
- Create: `src/ai_router/adapters/chatgpt/stream.py`
- Test: `tests/test_chatgpt_stream.py`

**Interfaces:**
- Consumes: `StreamDone` (Task 1).
- Produces: `parse_stream_done(status: int, body: str) -> StreamDone` — signature identical to gemini's (Task 2), pluggable into `ProviderProfile.parse_stream_done`.

**Decision table (from spec):**

| Evidence in stream | Verdict |
|---|---|
| HTTP status ≥ 400 with rate-limit signature (429/403/401 or `rate_limit`/`too many requests` in body) | `done, !ok, rate_limit` |
| HTTP status ≥ 400 otherwise | `done, !ok, error` |
| Moderation block event | `done, !ok, moderation` |
| `message_stream_complete` + final-channel success (`finished_successfully`+`end_turn` patch/message, or `last_token`/`event:"last"` marker) | `done, ok` |
| `message_stream_complete` without final success | `done, !ok, incomplete` |
| Non-null `error`/`error_code` on any event, no completion | `done, !ok, error` |
| None of the above (stream cut mid-flight) | `not done` (DOM hybrid + timeout decide) |

- [ ] **Step 1: Write the failing tests**

Create `tests/test_chatgpt_stream.py`:

```python
from ai_router.adapters.chatgpt.stream import parse_stream_done

# Trimmed from a real o3 "hi" capture: system/reasoning messages, final-channel
# message, append deltas, closing patch, markers, stream_complete.
SSE_SUCCESS = """event: delta_encoding
data: "v1"

data: {"type": "resume_conversation_token", "kind": "topic", "token": "xxx", "conversation_id": "conv-1"}

data: {"type": "input_message", "input_message": {"id": "m-user", "author": {"role": "user"}, "content": {"content_type": "text", "parts": ["hi"]}, "status": "finished_successfully"}, "conversation_id": "conv-1"}

event: delta
data: {"p": "", "o": "add", "v": {"message": {"id": "m-sys", "author": {"role": "system"}, "content": {"content_type": "text", "parts": [""]}, "status": "finished_successfully", "end_turn": true, "metadata": {"is_visually_hidden_from_conversation": true}, "channel": null}, "conversation_id": "conv-1", "error": null}, "c": 0}

event: delta
data: {"v": {"message": {"id": "m-cot", "author": {"role": "assistant"}, "content": {"content_type": "reasoning_recap", "content": "Worked for 4s"}, "status": "finished_successfully", "end_turn": false, "channel": null}, "conversation_id": "conv-1", "error": null}, "c": 5}

event: delta
data: {"v": {"message": {"id": "m-final", "author": {"role": "assistant"}, "content": {"content_type": "text", "parts": [""]}, "status": "in_progress", "end_turn": null, "channel": "final"}, "conversation_id": "conv-1", "error": null}, "c": 6}

data: {"type": "message_marker", "conversation_id": "conv-1", "message_id": "m-final", "marker": "user_visible_token", "event": "first"}

event: delta
data: {"p": "/message/content/parts/0", "o": "append", "v": "Xin chao! Minh"}

event: delta
data: {"v": " co the giup gi cho"}

event: delta
data: {"p": "", "o": "patch", "v": [{"p": "/message/content/parts/0", "o": "append", "v": " nay?"}, {"p": "/message/status", "o": "replace", "v": "finished_successfully"}, {"p": "/message/end_turn", "o": "replace", "v": true}]}

data: {"type": "message_marker", "conversation_id": "conv-1", "message_id": "m-final", "marker": "last_token", "event": "last"}

data: {"type": "message_stream_complete", "conversation_id": "conv-1"}

data: [DONE]
"""


def _without(fixture: str, needle: str) -> str:
    return "\n".join(line for line in fixture.splitlines() if needle not in line)


def test_success_stream_is_done_ok():
    result = parse_stream_done(200, SSE_SUCCESS)
    assert result.done is True
    assert result.ok is True
    assert result.error_kind is None


def test_marker_alone_still_succeeds_without_patch():
    # Drop the closing patch line; the last_token marker still proves success.
    body = _without(SSE_SUCCESS, '"o": "patch"')
    result = parse_stream_done(200, body)
    assert result.done is True
    assert result.ok is True


def test_reasoning_only_prefix_is_not_done():
    # Cut the stream right after the reasoning recap — no final channel yet.
    cut = SSE_SUCCESS.index('"channel": "final"')
    result = parse_stream_done(200, SSE_SUCCESS[:cut])
    assert result.done is False


def test_stream_complete_without_final_success_is_incomplete():
    body = _without(_without(SSE_SUCCESS, '"o": "patch"'), '"marker": "last_token"')
    result = parse_stream_done(200, body)
    assert result.done is True
    assert result.ok is False
    assert result.error_kind == "incomplete"


def test_moderation_block_detected():
    body = SSE_SUCCESS.replace(
        'data: {"type": "message_stream_complete", "conversation_id": "conv-1"}',
        'data: {"type": "moderation", "conversation_id": "conv-1", '
        '"message_id": "m-final", "moderation_response": {"blocked": true, "flagged": true}}\n\n'
        'data: {"type": "message_stream_complete", "conversation_id": "conv-1"}',
    )
    result = parse_stream_done(200, body)
    assert result.done is True
    assert result.ok is False
    assert result.error_kind == "moderation"


def test_error_field_detected():
    body = (
        'data: {"v": {"message": {"id": "m-1", "author": {"role": "assistant"}, '
        '"channel": "final", "status": "in_progress"}}, '
        '"error": "Something went wrong", "error_code": "server_error"}\n'
    )
    result = parse_stream_done(200, body)
    assert result.done is True
    assert result.ok is False
    assert result.error_kind == "error"
    assert "Something went wrong" in result.error_text


def test_http_429_is_rate_limit():
    result = parse_stream_done(429, '{"detail": {"code": "rate_limit_exceeded"}}')
    assert result.done is True
    assert result.ok is False
    assert result.error_kind == "rate_limit"


def test_http_500_is_error():
    result = parse_stream_done(500, "internal server error")
    assert result.done is True
    assert result.ok is False
    assert result.error_kind == "error"


def test_answer_mentioning_rate_limits_is_not_flagged():
    # Body markers must only apply to HTTP >= 400 — an answer ABOUT rate
    # limiting streamed over HTTP 200 must not false-positive.
    body = SSE_SUCCESS.replace("Xin chao! Minh", "too many requests means rate_limit")
    result = parse_stream_done(200, body)
    assert result.ok is True


def test_empty_body_not_done():
    assert parse_stream_done(200, "").done is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_chatgpt_stream.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ai_router.adapters.chatgpt.stream'`

- [ ] **Step 3: Implement**

Create `src/ai_router/adapters/chatgpt/stream.py`:

```python
from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from ai_router.browser.profile import StreamDone

_RATE_LIMIT_STATUSES = (401, 403, 429)
_RATE_LIMIT_BODY_MARKERS = ("rate_limit", "too many requests")


def _iter_data_payloads(body: str) -> Iterator[dict[str, Any]]:
    """Yield each JSON object carried on a `data:` SSE line."""
    for raw in body.splitlines():
        line = raw.strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:") :].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            yield data


def _message_of(data: dict[str, Any]) -> dict[str, Any] | None:
    v = data.get("v")
    if isinstance(v, dict) and isinstance(v.get("message"), dict):
        return v["message"]
    return None


def _patch_ops(data: dict[str, Any]) -> list[dict[str, Any]]:
    if data.get("o") == "patch" and isinstance(data.get("v"), list):
        return [op for op in data["v"] if isinstance(op, dict)]
    return []


def _moderation_blocked(data: dict[str, Any]) -> bool:
    response = data.get("moderation_response")
    return isinstance(response, dict) and bool(response.get("blocked"))


def parse_stream_done(status: int, body: str) -> StreamDone:
    """Classify a finished /backend-api/f/conversation SSE body.

    Success requires message_stream_complete AND proof the final-channel
    assistant message ended well (finished_successfully + end_turn patch,
    or the last_token marker). Reasoning/system messages never count —
    they also carry finished_successfully but live outside channel "final".
    """
    if status >= 400:
        lower = body.lower()
        if status in _RATE_LIMIT_STATUSES or any(
            m in lower for m in _RATE_LIMIT_BODY_MARKERS
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

    stream_complete = False
    saw_final_channel = False
    final_status_finished = False
    final_end_turn = False
    last_token_marker = False
    moderation = False
    error_text: str | None = None

    for data in _iter_data_payloads(body):
        dtype = data.get("type")
        if dtype == "message_stream_complete":
            stream_complete = True
            continue
        if dtype == "message_marker":
            if data.get("marker") == "last_token" and data.get("event") == "last":
                last_token_marker = True
            continue
        if dtype == "moderation":
            if _moderation_blocked(data):
                moderation = True
            continue
        if data.get("error") or data.get("error_code"):
            error_text = str(data.get("error") or data.get("error_code"))[:200]

        msg = _message_of(data)
        if msg is not None:
            author = msg.get("author") or {}
            if msg.get("channel") == "final" and author.get("role") == "assistant":
                saw_final_channel = True
                if msg.get("status") == "finished_successfully":
                    final_status_finished = True
                if msg.get("end_turn") is True:
                    final_end_turn = True
            continue

        if saw_final_channel:
            for op in _patch_ops(data):
                if (
                    op.get("p") == "/message/status"
                    and op.get("v") == "finished_successfully"
                ):
                    final_status_finished = True
                elif op.get("p") == "/message/end_turn" and op.get("v") is True:
                    final_end_turn = True

    if moderation:
        return StreamDone(
            done=True,
            ok=False,
            error_kind="moderation",
            error_text=error_text or "Blocked by moderation",
        )

    final_success = (final_status_finished and final_end_turn) or last_token_marker
    if stream_complete and final_success:
        return StreamDone(done=True, ok=True)
    if stream_complete:
        return StreamDone(
            done=True,
            ok=False,
            error_kind="incomplete",
            error_text=error_text or "Stream completed without a successful final message",
        )
    if error_text:
        return StreamDone(done=True, ok=False, error_kind="error", error_text=error_text)
    return StreamDone(done=False, ok=False)
```

- [ ] **Step 4: Run tests**

Run: `poetry run pytest tests/test_chatgpt_stream.py -v`
Expected: 10 PASS

- [ ] **Step 5: Commit**

```bash
git add src/ai_router/adapters/chatgpt/stream.py tests/test_chatgpt_stream.py
git commit -m "feat: ChatGPT SSE parser detects final-channel completion and error kinds"
```

---

### Task 8: ChatGPT selectors + DOM wait helpers

**Files:**
- Create: `src/ai_router/adapters/chatgpt/selectors.py`
- Create: `src/ai_router/adapters/chatgpt/wait.py`
- Test: `tests/test_chatgpt_wait.py`

**Interfaces:**
- Consumes: nothing new.
- Produces (consumed by Tasks 9–10):
  - `selectors.py`: `CHATGPT_URL = "https://chatgpt.com/"`, `CHATGPT_CONVERSATION_RE` (matches `/backend-api/f/conversation`), `SEL_PROMPT_INPUT`, `SEL_SUBMIT_BUTTON`, `SEL_STOP_BUTTON`, `SEL_ASSISTANT_TURN`, `SEL_ASSISTANT_TEXT`, `SEL_LOGIN`, `RATE_LIMIT_MARKERS`, `CHATGPT_ERROR_MARKERS`.
  - `wait.py`: async `is_stop_visible(page) -> bool`, async `read_response_snapshot(page) -> tuple[int, str]`, `is_rate_limited(text) -> bool`, async `submit_ready(page) -> bool`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_chatgpt_wait.py`:

```python
from ai_router.adapters.chatgpt.selectors import CHATGPT_CONVERSATION_RE
from ai_router.adapters.chatgpt.wait import is_rate_limited


def test_conversation_url_matches():
    assert CHATGPT_CONVERSATION_RE.search(
        "https://chatgpt.com/backend-api/f/conversation"
    )


def test_conversation_url_rejects_metadata_endpoints():
    assert not CHATGPT_CONVERSATION_RE.search(
        "https://chatgpt.com/backend-api/f/conversation/init"
    )


def test_rate_limit_detects_usage_cap():
    assert is_rate_limited("You've reached your usage cap for GPT-5.") is True


def test_rate_limit_detects_too_many_requests():
    assert is_rate_limited("Too many requests. Please try again later.") is True


def test_rate_limit_negative_on_normal_answer():
    assert is_rate_limited("Here is an overview of Python decorators") is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_chatgpt_wait.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ai_router.adapters.chatgpt.selectors'`

- [ ] **Step 3: Implement**

Create `src/ai_router/adapters/chatgpt/selectors.py`:

```python
import re

CHATGPT_URL = "https://chatgpt.com/"

# Match the send/stream endpoint only — not /conversation/init or
# /conversation/<id> metadata calls.
CHATGPT_CONVERSATION_RE = re.compile(r"/backend-api/f/conversation(?:\?|$)", re.I)

# Starting points — verify against the live DOM during the smoke test (Task 11).
SEL_PROMPT_INPUT = "#prompt-textarea"
SEL_SUBMIT_BUTTON = (
    'button[data-testid="send-button"], '
    'button[aria-label*="Send" i]'
)
SEL_STOP_BUTTON = (
    'button[data-testid="stop-button"], '
    'button[aria-label*="Stop" i]'
)
SEL_ASSISTANT_TURN = '[data-message-author-role="assistant"]'
SEL_ASSISTANT_TEXT = ".markdown"
SEL_LOGIN = (
    'button[data-testid="login-button"], '
    'a[href*="/auth/login"]'
)

RATE_LIMIT_MARKERS = (
    "too many requests",
    "you've reached your",
    "usage cap",
    "try again later",
)

CHATGPT_ERROR_MARKERS = (
    "something went wrong",
    "network error",
    "an error occurred",
)
```

Create `src/ai_router/adapters/chatgpt/wait.py`:

```python
from __future__ import annotations

from playwright.async_api import Page

from ai_router.adapters.chatgpt.selectors import (
    RATE_LIMIT_MARKERS,
    SEL_ASSISTANT_TEXT,
    SEL_ASSISTANT_TURN,
    SEL_PROMPT_INPUT,
    SEL_STOP_BUTTON,
    SEL_SUBMIT_BUTTON,
)


def is_rate_limited(text: str) -> bool:
    lower = text.lower()
    return any(marker in lower for marker in RATE_LIMIT_MARKERS)


async def is_stop_visible(page: Page) -> bool:
    """True while ChatGPT is still generating (Stop control visible)."""
    return await page.locator(SEL_STOP_BUTTON).count() > 0


async def read_response_snapshot(page: Page) -> tuple[int, str]:
    """Return assistant turn count and text of the latest assistant turn."""
    turns = page.locator(SEL_ASSISTANT_TURN)
    count = await turns.count()
    if not count:
        return 0, ""
    last = turns.nth(count - 1)
    inner = last.locator(SEL_ASSISTANT_TEXT)
    if await inner.count():
        text = (await inner.first.inner_text()).strip()
    else:
        text = (await last.inner_text()).strip()
    return count, text


async def submit_ready(page: Page) -> bool:
    """True when the composer send button exists and is enabled."""
    submit = page.locator(SEL_SUBMIT_BUTTON).first
    if await submit.count() == 0:
        return False
    return not await submit.is_disabled()


async def input_ready(page: Page) -> bool:
    return await page.locator(SEL_PROMPT_INPUT).count() > 0
```

- [ ] **Step 4: Run tests**

Run: `poetry run pytest tests/test_chatgpt_wait.py -v`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add src/ai_router/adapters/chatgpt/selectors.py src/ai_router/adapters/chatgpt/wait.py tests/test_chatgpt_wait.py
git commit -m "feat: ChatGPT selectors and DOM wait helpers"
```

---

### Task 9: ChatGPT planner

**Files:**
- Create: `src/ai_router/adapters/chatgpt/planner.py`
- Test: `tests/test_chatgpt_planner.py`

**Interfaces:**
- Consumes: `Command` from `ai_router.browser.commands`, `AskJob` from `ai_router.browser.page_queue`, `CHATGPT_URL` (Task 8).
- Produces: `ChatGPTPlanner.plan(job: AskJob, *, recovery: bool = False) -> list[Command]` — same protocol as `GeminiPlanner`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_chatgpt_planner.py`:

```python
import asyncio

from ai_router.adapters.chatgpt.planner import ChatGPTPlanner
from ai_router.adapters.chatgpt.selectors import CHATGPT_URL
from ai_router.browser.page_queue import AskJob


def make_job() -> AskJob:
    loop = asyncio.new_event_loop()
    fut = loop.create_future()
    return AskJob("1", "sess", "hello", "chatgpt", fut, 300.0)


def test_plan_returns_six_commands():
    cmds = ChatGPTPlanner().plan(make_job())
    ops = [c.op for c in cmds]
    assert ops == [
        "wait_idle",
        "clear_input",
        "type",
        "submit",
        "wait_generating",
        "wait_answer",
    ]


def test_recovery_prepends_goto_chatgpt():
    cmds = ChatGPTPlanner().plan(make_job(), recovery=True)
    assert cmds[0].op == "goto"
    assert cmds[0].args["url"] == CHATGPT_URL
    assert cmds[1].op == "wait_idle"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_chatgpt_planner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ai_router.adapters.chatgpt.planner'`

- [ ] **Step 3: Implement**

Create `src/ai_router/adapters/chatgpt/planner.py`:

```python
from ai_router.adapters.chatgpt.selectors import CHATGPT_URL
from ai_router.browser.commands import Command
from ai_router.browser.page_queue import AskJob


class ChatGPTPlanner:
    def plan(self, job: AskJob, *, recovery: bool = False) -> list[Command]:
        if recovery:
            return [
                Command("goto", {"url": CHATGPT_URL}),
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

- [ ] **Step 4: Run tests**

Run: `poetry run pytest tests/test_chatgpt_planner.py -v`
Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add src/ai_router/adapters/chatgpt/planner.py tests/test_chatgpt_planner.py
git commit -m "feat: ChatGPT command planner"
```

---

### Task 10: ChatGPT adapter goes live + config

**Files:**
- Modify: `src/ai_router/adapters/chatgpt/adapter.py` (replace stub)
- Modify: `src/ai_router/config.py`
- Test: `tests/test_chatgpt_adapter.py` (create), `tests/test_config.py` (append)

**Interfaces:**
- Consumes: everything from Tasks 7–9, `ProviderProfile` (Task 1).
- Produces:
  - `ChatGPTAdapter` with `status = "available"`, `check_session`, `ensure_page_ready`, `open_new_chat`, `build_profile(cfg) -> ProviderProfile` (`provider_id="chatgpt"`, `recoverable_codes=("CHATGPT_ERROR", "CHATGPT_INCOMPLETE")`, `answer_timeout_s=cfg.chatgpt_answer_timeout_s`).
  - `AppConfig.chatgpt_answer_timeout_s: float = 300.0` — yaml key `chatgpt_answer_timeout_s`, env `AI_ROUTER_CHATGPT_ANSWER_TIMEOUT_S`; default provider map gains `"chatgpt": ProviderConfig(url="https://chatgpt.com/")`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_chatgpt_adapter.py`:

```python
from pathlib import Path

from ai_router.adapters.chatgpt.adapter import ChatGPTAdapter
from ai_router.adapters.chatgpt.selectors import SEL_PROMPT_INPUT
from ai_router.config import AppConfig
from ai_router.mcp.tools import create_app_state


def make_config() -> AppConfig:
    return AppConfig(
        profile_dir=Path("profile"),
        default_provider="gemini",
        host="127.0.0.1",
        port=0,
        answer_timeout_s=120,
    )


def test_chatgpt_is_available():
    assert ChatGPTAdapter().status == "available"


def test_build_profile_wires_chatgpt_pieces():
    profile = ChatGPTAdapter().build_profile(make_config())
    assert profile.provider_id == "chatgpt"
    assert profile.recoverable_codes == ("CHATGPT_ERROR", "CHATGPT_INCOMPLETE")
    assert profile.answer_timeout_s == 300.0
    assert profile.selectors.prompt_input == SEL_PROMPT_INPUT
    assert profile.stream_url_re.search(
        "https://chatgpt.com/backend-api/f/conversation"
    )
    assert profile.parse_stream_done(429, "rate_limit_exceeded").error_kind == "rate_limit"


def test_app_state_includes_chatgpt_profile():
    st = create_app_state(make_config())
    assert "chatgpt" in st.profiles
    assert st.profiles["chatgpt"].answer_timeout_s == 300.0
```

Append to `tests/test_config.py`:

```python
def test_chatgpt_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("AI_ROUTER_CHATGPT_ANSWER_TIMEOUT_S", raising=False)
    cfg = load_config()
    assert cfg.chatgpt_answer_timeout_s == 300.0
    assert "chatgpt" in cfg.providers
    assert cfg.providers["chatgpt"].url == "https://chatgpt.com/"


def test_env_override_chatgpt_timeout(monkeypatch):
    monkeypatch.setenv("AI_ROUTER_CHATGPT_ANSWER_TIMEOUT_S", "600")
    cfg = load_config()
    assert cfg.chatgpt_answer_timeout_s == 600.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_chatgpt_adapter.py tests/test_config.py -v`
Expected: FAIL — `AttributeError: 'AppConfig' object has no attribute 'chatgpt_answer_timeout_s'` / `'ChatGPTAdapter' object has no attribute 'build_profile'` / status is `"coming_soon"`

- [ ] **Step 3: Implement**

**`src/ai_router/config.py`**:

1. Add field to `AppConfig` (after `dom_tick_interval_ms`):

```python
    chatgpt_answer_timeout_s: float = 300.0
```

2. Add `chatgpt` to `_defaults()` providers:

```python
        providers={
            "gemini": ProviderConfig(url="https://gemini.google.com/app"),
            "chatgpt": ProviderConfig(url="https://chatgpt.com/"),
        },
```

3. In `load_config`, after the `stream_quiet_s` yaml block add:

```python
        if "chatgpt_answer_timeout_s" in raw:
            cfg.chatgpt_answer_timeout_s = float(raw["chatgpt_answer_timeout_s"])
```

and after the `AI_ROUTER_STREAM_QUIET_S` env block add:

```python
    if v := os.getenv("AI_ROUTER_CHATGPT_ANSWER_TIMEOUT_S"):
        cfg.chatgpt_answer_timeout_s = float(v)
```

**`src/ai_router/adapters/chatgpt/adapter.py`** — replace entirely:

```python
from __future__ import annotations

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from ai_router.adapters.base import SessionStatus
from ai_router.adapters.chatgpt.planner import ChatGPTPlanner
from ai_router.adapters.chatgpt.selectors import (
    CHATGPT_CONVERSATION_RE,
    CHATGPT_ERROR_MARKERS,
    CHATGPT_URL,
    SEL_LOGIN,
    SEL_PROMPT_INPUT,
    SEL_SUBMIT_BUTTON,
)
from ai_router.adapters.chatgpt.stream import parse_stream_done
from ai_router.adapters.chatgpt.wait import (
    is_rate_limited,
    is_stop_visible,
    read_response_snapshot,
    submit_ready,
)
from ai_router.browser.profile import ProviderProfile, ProviderSelectors
from ai_router.config import AppConfig


class ChatGPTAdapter:
    id = "chatgpt"
    name = "ChatGPT"
    keywords: list[str] = ["chatgpt", "gpt", "@chatgpt"]
    status = "available"

    async def check_session(self, page: Page) -> SessionStatus:
        return await self.ensure_page_ready(page)

    async def ensure_page_ready(self, page: Page) -> SessionStatus:
        """Open a fresh chat and verify login."""
        await page.goto(CHATGPT_URL, wait_until="domcontentloaded")
        try:
            await page.wait_for_selector(SEL_PROMPT_INPUT, timeout=15_000)
            return SessionStatus.LOGGED_IN
        except PlaywrightTimeout:
            if await page.locator(SEL_LOGIN).count() > 0:
                return SessionStatus.LOGGED_OUT
            return SessionStatus.UNKNOWN

    async def open_new_chat(self, page: Page) -> None:
        await page.goto(CHATGPT_URL, wait_until="domcontentloaded")

    def build_profile(self, cfg: AppConfig) -> ProviderProfile:
        return ProviderProfile(
            provider_id=self.id,
            stream_url_re=CHATGPT_CONVERSATION_RE,
            parse_stream_done=parse_stream_done,
            is_stop_visible=is_stop_visible,
            read_response_snapshot=read_response_snapshot,
            is_rate_limited=is_rate_limited,
            submit_ready=submit_ready,
            planner=ChatGPTPlanner(),
            selectors=ProviderSelectors(
                prompt_input=SEL_PROMPT_INPUT,
                submit_button=SEL_SUBMIT_BUTTON,
            ),
            error_markers=CHATGPT_ERROR_MARKERS,
            recoverable_codes=("CHATGPT_ERROR", "CHATGPT_INCOMPLETE"),
            answer_timeout_s=cfg.chatgpt_answer_timeout_s,
        )
```

- [ ] **Step 4: Run tests**

Run: `poetry run pytest tests/test_chatgpt_adapter.py tests/test_config.py -v` → all PASS
Run: `poetry run pytest` → full suite PASS (note: `handle_ask`'s `coming_soon` guard no longer trips for chatgpt; router tests unaffected — resolve is explicit/default only)

- [ ] **Step 5: Commit**

```bash
git add src/ai_router/adapters/chatgpt/adapter.py src/ai_router/config.py tests/test_chatgpt_adapter.py tests/test_config.py
git commit -m "feat: ChatGPT adapter live — profile, config, 300s thinking-model timeout"
```

---

### Task 11: Full verification + live smoke test

**Files:**
- No new files (selector fixes in `src/ai_router/adapters/chatgpt/selectors.py` if the live DOM differs).

**Interfaces:**
- Consumes: the whole feature.
- Produces: verified working software.

- [ ] **Step 1: Full automated verification**

```bash
poetry run pytest -v
poetry run ruff check src tests
grep -r "gemini" src/ai_router/browser/
```

Expected: all tests PASS; ruff clean; grep prints NOTHING (browser layer fully provider-agnostic).

- [ ] **Step 2: Live login**

```bash
poetry run ai browser login --provider chatgpt
```

Log in to chatgpt.com in the opened window, then close the browser.

- [ ] **Step 3: Selector verification against the live DOM**

Before the ask smoke test, with a logged-in headed browser open on chatgpt.com, verify in DevTools that each selector in `selectors.py` matches exactly the intended element:
- `#prompt-textarea` → composer input
- `button[data-testid="send-button"]` → send
- `button[data-testid="stop-button"]` → visible ONLY while generating
- `[data-message-author-role="assistant"]` → one per assistant turn
- `.markdown` inside the turn → answer text container

If any differ, update `selectors.py` (and only it), re-run `poetry run pytest`, commit the fix as `fix: align ChatGPT selectors with live DOM`.

- [ ] **Step 4: End-to-end ask smoke test**

Terminal 1: `poetry run ai serve`

Terminal 2 — call the MCP tool over streamable HTTP:

```bash
npx @modelcontextprotocol/inspector --cli http://127.0.0.1:8087/mcp --method tools/call --tool-name ask --tool-arg prompt="What is 2+2? Answer with just the number." --tool-arg provider=chatgpt
```

Expected: JSON result with `"answer"` containing `4`, `"provider": "chatgpt"`. Watch Terminal 1 traces: `stream_end` (with `ok=True`) must appear before `wait_answer_ready`.

Also verify Gemini did not regress:

```bash
npx @modelcontextprotocol/inspector --cli http://127.0.0.1:8087/mcp --method tools/call --tool-name ask --tool-arg prompt="What is 3+3? Answer with just the number." --tool-arg provider=gemini
```

Expected: answer contains `6`.

- [ ] **Step 5: Thinking-model check (manual, best-effort)**

In the ChatGPT web UI set the model to a thinking model (e.g. gpt-5-thinking), then repeat the chatgpt ask with a prompt that triggers reasoning (e.g. "How many r's are in strawberry? Think carefully."). Expected: no premature `wait_answer_ready` during the reasoning phase; answer returns after reasoning ends (within 300 s).

- [ ] **Step 6: Final commit (if smoke fixes were needed)**

```bash
git add -A
git commit -m "chore: ChatGPT adapter verified end-to-end"
```

---

## Self-Review Notes

- **Spec coverage:** ProviderProfile abstraction (Tasks 1–6), SSE parser incl. thinking-model scoping (Task 7), DOM profile (Task 8), planner (Task 9), adapter+config+timeout (Task 10), error taxonomy (Tasks 3, 5, 7), recovery-once (Task 6 via `recoverable_codes`), tests-per-spec (each task), live verification (Task 11). WebSocket fallback edge: covered by design — parser returns `done=False` on unrecognized bodies and the DOM hybrid + timeout path decides (no extra code needed).
- **Type consistency:** `parse_stream_done(status: int, body: str) -> StreamDone` identical for both providers; `build_profile(cfg: AppConfig) -> ProviderProfile` on both adapters; `StateReducer(stream_url_res=...)`; `CommandExecutor(profile=...)`; `PageWorker(page, queue, cfg, profiles, default_provider)` — used consistently in Tasks 3–6 and 10.
- **Known temporary states:** Task 3 Step 4 and Task 4 Step 3 leave a gemini import inside `page_worker.py` that Task 6 removes; the suite stays green at every commit.
