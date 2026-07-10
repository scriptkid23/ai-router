# Stateless Ask Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every `ask` opens a fresh chat on the provider (Gemini) — no chat-session persistence; the browser context and Google login stay untouched.

**Architecture:** Delete the `SessionManager`/`ChatSession` layer. `handle_ask` grabs the shared page from `BrowserManager`, calls `adapter.ensure_page_ready(page)` (which for Gemini navigates to gemini.google.com = new chat and verifies login), then enqueues the prompt on the existing `PageWorker` queue. Concurrent asks stay serialized on one tab.

**Tech Stack:** Python 3.11+, Playwright async, pytest + pytest-asyncio. Spec: `docs/superpowers/specs/2026-07-10-stateless-ask-design.md`.

## Global Constraints

- Browser stays a single persistent context using `config.profile_dir` (login preserved) — do not change `BrowserManager`.
- `PageWorker` / `PageQueueRegistry` serialization is unchanged.
- Error mapping unchanged: `LOGGED_OUT` → `NotLoggedInError`, closed browser → `BrowserClosedError`, timeout → `TimeoutError_`.
- `mcp_session_id` parameter stays on `handle_ask` for trace/log only — it must no longer be required.
- Run tests with: `python -m pytest tests -v` from repo root `d:\1hoodlabs\ai-router`.

---

### Task 1: Make `handle_ask` stateless (new chat per ask, no Mcp-Session-Id required)

**Files:**
- Modify: `src/ai_router/mcp/tools.py`
- Create: `tests/test_tools_stateless.py`

**Interfaces:**
- Consumes: `BrowserManager.new_page()`, `adapter.ensure_page_ready(page) -> SessionStatus`, `PageWorker.enqueue(job)`.
- Produces: `handle_ask(state, *, prompt, provider, mcp_session_id)` that works with `mcp_session_id=None`; `AppState` without a `sessions` field; `create_app_state` without `SessionManager`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tools_stateless.py`:

```python
from __future__ import annotations

import pytest

from ai_router.adapters.base import SessionStatus
from ai_router.config import AppConfig
from ai_router.errors import NotLoggedInError
from ai_router.mcp import tools
from ai_router.mcp.tools import create_app_state, handle_ask


class FakePage:
    url = "https://gemini.google.com/app"

    def is_closed(self) -> bool:
        return False


class FakeBrowser:
    def __init__(self, page: FakePage) -> None:
        self._page = page

    async def new_page(self) -> FakePage:
        return self._page


class FakeAdapter:
    id = "gemini"
    name = "Gemini"
    status = "available"

    def __init__(self, session_status: SessionStatus = SessionStatus.LOGGED_IN) -> None:
        self.session_status = session_status
        self.ensure_calls: list[FakePage] = []

    async def ensure_page_ready(self, page: FakePage) -> SessionStatus:
        self.ensure_calls.append(page)
        return self.session_status


class FakeWorker:
    def __init__(self, page, queue, config) -> None:
        self.jobs = []

    def start(self) -> None:
        pass

    async def enqueue(self, job) -> None:
        self.jobs.append(job)
        job.future.set_result("fake answer")


@pytest.fixture()
def state(monkeypatch):
    monkeypatch.setattr(tools, "PageWorker", FakeWorker)
    st = create_app_state(AppConfig())
    page = FakePage()
    st.browser = FakeBrowser(page)
    return st


@pytest.fixture()
def adapter(state, monkeypatch):
    fake = FakeAdapter()
    monkeypatch.setattr(
        tools,
        "resolve_provider",
        lambda registry, provider, default: (fake, "default"),
    )
    return fake


@pytest.mark.asyncio
async def test_ask_without_mcp_session_id(state, adapter) -> None:
    result = await handle_ask(
        state, prompt="hi", provider=None, mcp_session_id=None
    )
    assert result["answer"] == "fake answer"


@pytest.mark.asyncio
async def test_each_ask_opens_new_chat(state, adapter) -> None:
    await handle_ask(state, prompt="one", provider=None, mcp_session_id="s1")
    await handle_ask(state, prompt="two", provider=None, mcp_session_id="s1")
    # ensure_page_ready runs on every ask — a fresh chat each time,
    # never a resume of a prior conversation
    assert len(adapter.ensure_calls) == 2


@pytest.mark.asyncio
async def test_logged_out_raises(state, adapter) -> None:
    adapter.session_status = SessionStatus.LOGGED_OUT
    with pytest.raises(NotLoggedInError):
        await handle_ask(state, prompt="hi", provider=None, mcp_session_id=None)


def test_app_state_has_no_sessions() -> None:
    st = create_app_state(AppConfig())
    assert not hasattr(st, "sessions")
```

Note: if `AppConfig()` requires arguments, mirror how existing tests construct it (check `tests/` for an existing config fixture and reuse that pattern).

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_tools_stateless.py -v`
Expected: FAIL — `test_ask_without_mcp_session_id` raises `AiRouterError("MISSING_SESSION", ...)`, `test_app_state_has_no_sessions` fails on the `sessions` attribute.

- [ ] **Step 3: Rewrite `handle_ask` and `AppState` in `src/ai_router/mcp/tools.py`**

Remove the imports of `SessionManager`; remove `sessions` from `AppState` and `create_app_state`; make `handle_ask` stateless:

```python
@dataclass
class AppState:
    config: AppConfig
    registry: ProviderRegistry
    browser: BrowserManager
    page_queues: PageQueueRegistry
    page_workers: dict[str, PageWorker]


def create_app_state(config: AppConfig | None = None) -> AppState:
    cfg = config or load_config()
    return AppState(
        config=cfg,
        registry=build_registry(),
        browser=BrowserManager(cfg),
        page_queues=PageQueueRegistry(),
        page_workers={},
    )
```

```python
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
        job = AskJob(
            job_id=str(uuid.uuid4()),
            mcp_session_id=mcp_session_id,
            prompt=prompt,
            provider_id=adapter.id,
            future=future,
            timeout_s=float(state.config.answer_timeout_s),
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
```

Also delete the now-unused import `from ai_router.session.manager import SessionManager`. Check whether `AskJob.mcp_session_id` is typed `str` in `page_queue.py`; if so, widen it to `str | None`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_tools_stateless.py -v`
Expected: 4 passed.

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest tests -v`
Expected: everything passes except possibly `tests/test_session.py` (deleted in Task 2 — if it still passes here, that's fine).

- [ ] **Step 6: Commit**

```bash
git add src/ai_router/mcp/tools.py src/ai_router/browser/page_queue.py tests/test_tools_stateless.py
git commit -m "feat: stateless ask — new chat per request, drop session lookup"
```

### Task 2: Delete the session layer and `resume_chat`/`preserve_chat`

**Files:**
- Delete: `src/ai_router/session/manager.py`, `src/ai_router/session/__init__.py`, `tests/test_session.py`
- Modify: `src/ai_router/adapters/gemini/adapter.py:22-45`

**Interfaces:**
- Consumes: Task 1's `handle_ask`, which calls `ensure_page_ready(page)` with no `preserve_chat` argument.
- Produces: `GeminiAdapter.ensure_page_ready(page) -> SessionStatus` (no keyword), no `resume_chat` method, no `ai_router.session` package.

- [ ] **Step 1: Confirm nothing else references the session layer**

Run: `git grep -n "session.manager\|SessionManager\|resume_chat\|preserve_chat" -- src tests`
Expected: only `src/ai_router/session/*`, `src/ai_router/adapters/gemini/adapter.py`, `tests/test_session.py`. If anything else appears, update it in this task.

- [ ] **Step 2: Delete files**

```bash
git rm -r src/ai_router/session tests/test_session.py
```

- [ ] **Step 3: Simplify the Gemini adapter**

In `src/ai_router/adapters/gemini/adapter.py`, replace `ensure_page_ready` and delete `resume_chat`:

```python
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
```

(Keep the existing body's exact fallback lines if they differ — only remove the `preserve_chat` branch and parameter. Delete the `resume_chat` method entirely.)

- [ ] **Step 4: Run the full suite**

Run: `python -m pytest tests -v`
Expected: all pass; no import errors from the deleted package.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor: remove session layer and resume_chat/preserve_chat"
```
