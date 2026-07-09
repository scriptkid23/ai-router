# Browser Event Queue Design

**Date:** 2026-07-09  
**Status:** Approved  
**Branch:** `python`  
**Project:** ai-router — Redesign browser automation layer: per-page queue + event channel + state-driven commands

**Replaces:** Direct `adapter.ask()` polling model in `GeminiAdapter` and global `BrowserManager.acquire()` lock for ask execution.

**Motivation:** Current flow types into input, submits, then polls DOM with weak generating detection. This causes:
- Prompt text lingering in input during generation (looks like re-injection)
- Early `wait_for_answer_dom` return when `generating==0` flickers between stream chunks
- Double submit (`Enter` + `Send` click) interrupting active generation
- Gemini errors `(1095)` from unstable session state

---

## 1. Summary

Introduce an **event-driven per-page worker** between MCP `handle_ask` and Playwright:

1. **`ask` only enqueues** an `AskJob` onto the page's FIFO queue and **awaits a blocking `Future`**
2. **All Playwright signals** (network, navigation, console, periodic DOM tick) flow into an **`EventChannel`**
3. A **`BrowserState` reducer** derives `idle | submitting | generating | error | closed`
4. **`PageWorker` dequeues only when `idle`** (stable for N consecutive ticks)
5. **`GeminiPlanner` emits a `CommandScript`** — executor runs commands sequentially, waiting for state transitions between steps
6. **Multiple pages run in parallel**; jobs on the same page are strictly sequential

### Decisions (approved)

| # | Decision | Choice |
|---|----------|--------|
| 1 | Queue scope | **Per-page** (map `Mcp-Session-Id` → `Page` → `PageQueue`) |
| 2 | Channel model | **Hybrid** — raw Playwright events + derived `BrowserState` |
| 3 | MCP return | **Blocking `Future`** — no API change for Cursor |
| 4 | Architecture | **`PageWorker`** per page (not global orchestrator) |
| 5 | Submit | **Click Send only** — no `Enter` fallback chain |

---

## 2. Architecture

```
┌─────────────┐
│ handle_ask  │
└──────┬──────┘
       │ AskJob + asyncio.Future
       ▼
┌─────────────┐     ┌──────────────────────────────────────┐
│  PageQueue  │────►│  PageWorker (1 coroutine per Page)    │
│  (FIFO)     │     │                                       │
└─────────────┘     │  ┌─────────────┐    ┌──────────────┐ │
                    │  │ EventChannel│───►│ StateReducer │ │
                    │  │ (async Q)   │    │ BrowserState │ │
                    │  └──────▲──────┘    └──────┬───────┘ │
                    │         │ raw events        │ phase   │
                    │  ┌──────┴──────┐            │         │
                    │  │  Playwright │◄───────────┘         │
                    │  │  Page       │                      │
                    │  └──────▲──────┘                      │
                    │         │ commands                    │
                    │  ┌──────┴──────┐   ┌───────────────┐  │
                    │  │ CommandExec │◄──│ GeminiPlanner │  │
                    │  └─────────────┘   └───────────────┘  │
                    └──────────────────────────────────────┘
       future.set_result(answer)
```

### 2.1 Parallelism model

| Scope | Behavior |
|-------|----------|
| Same `Page` | FIFO — job N+1 waits until job N completes and state returns `idle` |
| Different `Page`s | Independent workers — Page A generating does not block Page B |
| Same browser context | Shared CloakBrowser profile; multiple tabs/pages allowed |

### 2.2 What changes from current design

| Before | After |
|--------|-------|
| `BrowserManager.acquire()` global lock | Removed for ask path; per-page queue serializes |
| `GeminiAdapter._ask_once()` polls DOM directly | `GeminiPlanner` + `CommandExecutor` + state waits |
| `wait_for_answer_dom()` accepts on 2s stable + single `generating==0` | Requires `idle_streak >= 6` after generation |
| `_submit_prompt()` Enter + Send fallback | `submit` command = Send click only |
| `handle_ask` runs adapter synchronously | `handle_ask` enqueues + `await future` |

---

## 3. Components

### 3.1 `BrowserEvent` (raw channel payload)

```python
@dataclass
class BrowserEvent:
    page_id: str
    kind: Literal[
        "request_finished",
        "response",
        "framenavigated",
        "console",
        "pageerror",
        "dom_tick",   # synthetic: emitted every dom_tick_interval_ms
    ]
    payload: dict
    ts: float
```

**Sources:**
- `page.on("requestfinished", ...)` — detect `StreamGenerate`
- `page.on("response", ...)` — optional HTTP status
- `page.on("framenavigated", ...)` — chat URL changes
- `page.on("console", ...)` / `page.on("pageerror", ...)`
- Background `dom_tick` task — polls Stop button, response blocks, error banners

### 3.2 `BrowserState` (derived)

```python
@dataclass
class BrowserState:
    phase: Literal["idle", "submitting", "generating", "error", "closed"]
    generating_streak: int       # consecutive dom_ticks with Stop visible
    idle_streak: int             # consecutive dom_ticks without Stop
    last_stream_at: float | None
    error_text: str | None
    response_count: int
    last_response_text: str
```

**Reducer rules (Gemini v1):**

| Condition | Transition |
|-----------|------------|
| `StreamGenerate` in `request_finished` | set `last_stream_at` |
| Stop button visible | `generating_streak++`, reset `idle_streak`, `phase=generating` |
| Stop absent | `idle_streak++`, reset `generating_streak` |
| `idle_streak >= idle_streak_required` AND never was generating this cycle | `phase=idle` |
| `idle_streak >= idle_streak_required` AND was generating AND response stable | `phase=idle` |
| Body contains `something went wrong` or `(1095)` etc. | `phase=error` |
| Page closed | `phase=closed` |
| Command executor starts submit | `phase=submitting` |

**Dequeue gate:** `PageWorker` only pulls from queue when `phase == idle` AND `idle_streak >= idle_streak_required`.

### 3.3 `AskJob`

```python
@dataclass
class AskJob:
    job_id: str
    mcp_session_id: str
    prompt: str
    provider_id: str
    future: asyncio.Future[str]
    enqueued_at: float
    timeout_s: float
```

### 3.4 `Command` / `CommandScript`

Adapter emits a ordered list of commands. Executor runs them; **each wait command blocks until state condition met**.

```python
@dataclass
class Command:
    op: Literal[
        "wait_idle",
        "clear_input",
        "type",
        "submit",
        "wait_generating",
        "wait_answer",
        "goto",          # recovery: new chat or resume URL
    ]
    args: dict = field(default_factory=dict)
```

**`GeminiPlanner.plan(job)` default script:**

```
1. wait_idle
2. clear_input
3. type(prompt=job.prompt)
4. submit
5. wait_generating
6. wait_answer(before_response_count=N)
```

### 3.5 `CommandExecutor`

- Holds reference to `Page`, `EventChannel`, current `BrowserState`
- `submit`: click `SEL_SUBMIT_BUTTON` once — **no Enter**
- `wait_generating`: block until `generating_streak >= generating_streak_required`
- `wait_answer`: block until:
  - `response_count > before_count`
  - `phase == idle` with `idle_streak >= idle_streak_required`
  - `last_response_text` stable for `answer_stable_ticks` consecutive dom_ticks
  - **Must have been in `generating` phase at least once** (prevents early return before submit)
- `clear_input`: `click` → `Ctrl+A` → `Backspace` (keyboard, not JS innerHTML)

### 3.6 `PageWorker`

One asyncio task per `Page`, started when `SessionManager` creates or restores a page.

```python
async def run(self):
    while not self._stopped:
        await self._state.wait_until(
            phase="idle",
            idle_streak_min=config.idle_streak_required,
        )
        job = await self._queue.get()
        if job.future.cancelled():
            continue
        try:
            script = self._planner.plan(job)
            answer = await self._executor.run(script, job)
            job.future.set_result(answer)
        except Exception as exc:
            job.future.set_exception(exc)
```

### 3.7 `PageQueueRegistry`

- `dict[str, PageWorker]` keyed by `page_id` (Playwright internal id or uuid)
- `SessionManager.get_or_create()` → `registry.ensure_worker(page, adapter)`
- Worker lifecycle tied to page; stopped when page closed

---

## 4. MCP integration

### 4.1 `handle_ask` (unchanged API)

```python
async def handle_ask(state, *, prompt, provider, mcp_session_id):
    session = await state.sessions.get_or_create(...)
    worker = state.page_queues.ensure(session.page, adapter)
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    job = AskJob(...)
    await worker.enqueue(job)
    answer = await asyncio.wait_for(future, timeout=job.timeout_s)
    state.sessions.record_message(mcp_session_id, page_url=session.page.url)
    return {"answer": answer, ...}
```

- **Blocking Future (approved):** MCP handler awaits until worker completes
- On timeout: `future.cancel()` + raise `TimeoutError_`
- On MCP disconnect: task cancellation propagates to `future`

### 4.2 `session_status`

- **Does not enqueue jobs**
- Read-only: attach temporary listener OR read cached `BrowserState` from existing worker
- Must not type into input or acquire page queue

---

## 5. Gemini-specific details

### 5.1 Selectors (unchanged constants, stricter usage)

```python
SEL_GENERATING = 'button[aria-label*="Stop"], button[aria-label*="Dừng"], ...'  # expand after inspect
SEL_SUBMIT_BUTTON = '...'  # Send only, exclude Stop
```

**Action item during implementation:** Inspect real Stop button `aria-label` in user's Gemini UI and extend `SEL_GENERATING`.

### 5.2 Error markers

```python
GEMINI_ERROR_MARKERS = (
    "something went wrong",
    "(1095)", "(1096)", "(1097)",
)
```

On `phase=error`: planner triggers recovery script:
```
goto(new_chat=True) → wait_idle → re-run original script (once)
```

### 5.3 Chat URL persistence

Unchanged from current `SessionManager`:
- Save `chat_url` slug after successful job
- On page recreate: `resume_chat(chat_url)` before worker resumes

---

## 6. Configuration

```python
# defaults in config.py
idle_streak_required: int = 6          # 3s at 500ms tick
generating_streak_required: int = 2      # 1s confirming generate started
answer_stable_ticks: int = 4             # 2s stable text
dom_tick_interval_ms: int = 500
stream_wait_timeout_s: float = 30
answer_timeout_s: float = 120
max_pages: int = 10                    # cap concurrent page workers
```

---

## 7. File layout

```
src/ai_router/
  browser/
    manager.py          # KEEP: context launch, new_page (remove acquire for ask)
    events.py           # NEW: BrowserEvent, EventChannel, Playwright listeners
    state.py            # NEW: BrowserState, StateReducer
    page_queue.py       # NEW: AskJob, PageQueue, PageQueueRegistry
    page_worker.py      # NEW: PageWorker coroutine loop
    commands.py         # NEW: Command, CommandExecutor
  adapters/gemini/
    planner.py          # NEW: GeminiPlanner → CommandScript
    selectors.py        # KEEP + extend SEL_GENERATING
    adapter.py          # SLIM: session check, open/resume chat only
    wait.py             # DEPRECATE polling helpers; logic moves to state.py
  session/manager.py    # start/stop PageWorker on page create/close
  mcp/tools.py          # handle_ask: enqueue + await future
```

---

## 8. Error handling

| Event | Behavior |
|-------|----------|
| `phase=error` (1095 etc.) | Retry once with fresh chat; then `GEMINI_ERROR` |
| `phase=closed` | Recreate page, `resume_chat` if `chat_url` set, re-enqueue job |
| Job timeout | `TimeoutError_` on future |
| Future cancelled (MCP disconnect) | Worker skips job, does not submit |
| `SUBMIT_FAILED` | Send click did not reach `generating` within timeout |
| Queue full (optional) | `BROWSER_BUSY` if `max_pages` exceeded |

---

## 9. Testing

| Test file | Coverage |
|-----------|----------|
| `test_state_reducer.py` | Event sequences → phase transitions, idle_streak, generating_streak |
| `test_page_queue.py` | FIFO ordering, future resolve/exception, cancel skip |
| `test_gemini_planner.py` | Command script shape per job type |
| `test_commands.py` | wait_answer does not return before generating phase |

No E2E browser tests in v1 of this redesign (same as current project).

---

## 10. Out of scope

- Persisting `AskJob` queue to disk across server restart
- Per-provider reducers beyond Gemini (interface ready, impl later)
- `session_status` via worker queue
- Changing MCP tool API (`ask` still returns raw text)
- ChatGPT adapter implementation

---

## 11. Migration plan (high level)

1. Add `events.py`, `state.py`, `page_queue.py` with unit tests
2. Add `commands.py`, `page_worker.py`
3. Add `gemini/planner.py`; slim `adapter.py`
4. Wire `SessionManager` → start worker
5. Change `handle_ask` → enqueue + await
6. Remove `BrowserManager.acquire()` from ask path
7. Delete `_submit_prompt`, `_poll_generating`, direct `wait_for_answer_dom` usage from adapter
8. Manual E2E: single ask, follow-up ask, parallel tabs, browser close + resume

---

## 12. Success criteria

1. **No text re-injection during generation** — executor does not run `type` until `idle`
2. **No partial answer return** — `wait_answer` requires full generate → idle cycle
3. **No double submit** — single Send click per job
4. **Follow-up in same Cursor tab** works without new Gemini chat
5. **Parallel Cursor tabs** do not block each other
6. **1095 recovery** — auto-retry once with new chat
