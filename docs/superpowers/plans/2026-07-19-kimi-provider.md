# Kimi Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `kimi` provider that automates www.kimi.com web sessions — DOM final answer from `.markdown-container .markdown`, Connect RPC `ChatService/Chat` stream-end signal (`MESSAGE_STATUS_COMPLETED`), fresh chat via `?chat_enter_method=new_chat` per ask.

**Architecture:** New `src/ai_router/adapters/kimi/` package. Extend `ProviderProfile` with `read_response_bytes: bool` and `parse_stream_done(status, str | bytes)`. Register in `build_registry()`, default timeout 600s. Reuse existing `StateReducer` hybrid gate unchanged.

**Tech Stack:** Python 3.11+, Playwright (CloakBrowser), existing `ProviderProfile` / `Command` / `StateReducer` infrastructure.

**Spec:** `docs/superpowers/specs/2026-07-19-kimi-provider-design.md`

## Global Constraints

- Integration: browser automation (CloakBrowser) — not direct HTTP API
- Answer source: DOM only — `.markdown-container .markdown` (stream is signal-only)
- Thinking blocks: excluded from returned text
- Chat lifecycle: new chat per `ask` — navigate to `https://www.kimi.com/?chat_enter_method=new_chat`
- Stream completion: `MESSAGE_STATUS_COMPLETED` in Connect RPC response body
- Answer timeout default: **`600.0`** seconds
- Recoverable codes: `("KIMI_ERROR",)` only
- Strip `.table-actions` UI chrome before reading answer text

---

## File map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/ai_router/browser/profile.py` | Modify | `read_response_bytes` flag; widen `parse_stream_done` to `str \| bytes` |
| `src/ai_router/browser/events.py` | Modify | Read `response.body()` when `read_response_bytes=True` |
| `src/ai_router/adapters/kimi/__init__.py` | Create | Package marker |
| `src/ai_router/adapters/kimi/selectors.py` | Create | URLs, regex, DOM selectors, error markers |
| `src/ai_router/adapters/kimi/stream.py` | Create | Connect frame parser + `parse_stream_done` |
| `src/ai_router/adapters/kimi/wait.py` | Create | DOM wait helpers, `ensure_new_chat`, challenge check |
| `src/ai_router/adapters/kimi/planner.py` | Create | Command plan per ask |
| `src/ai_router/adapters/kimi/adapter.py` | Create | `KimiAdapter` + `build_profile` |
| `src/ai_router/adapters/registry.py` | Modify | Register `KimiAdapter` |
| `src/ai_router/config.py` | Modify | Default `kimi` provider URL + 600s timeout |
| `tests/test_events_kimi_bytes.py` | Create | Bytes body routing in `handle_response` |
| `tests/test_kimi_stream.py` | Create | Connect stream parser unit tests |
| `tests/test_kimi_wait.py` | Create | Rate-limit helper tests |
| `tests/test_kimi_planner.py` | Create | Planner unit tests |
| `tests/test_registry.py` | Modify | Registry includes kimi |
| `tests/test_router.py` | Modify | Resolve `provider=kimi` |
| `tests/test_config.py` | Modify | Assert `kimi` in default providers |
| `tests/test_ask_multi.py` | Modify | Include kimi in available providers list |
| `README.md` | Modify | Document Kimi provider |
| `pyproject.toml` | Modify | Add `kimi` to description/keywords |

---

### Task 1: Bytes response hook — `read_response_bytes` + `events.handle_response`

**Files:**
- Modify: `src/ai_router/browser/profile.py`
- Modify: `src/ai_router/browser/events.py`
- Create: `tests/test_events_kimi_bytes.py`

**Interfaces:**
- Produces: `ProviderProfile.read_response_bytes: bool = False`
- Produces: `ProviderProfile.parse_stream_done: Callable[[int, str | bytes], StreamDone]`
- Consumes: existing `handle_response` stream matching via `stream_url_re`

- [ ] **Step 1: Extend ProviderProfile**

```python
# src/ai_router/browser/profile.py
from typing import Any  # if not already imported

@dataclass
class ProviderProfile:
    # ... existing fields ...
    parse_stream_done: Callable[[int, str | bytes], StreamDone]
    # ... existing fields ...
    read_response_bytes: bool = False
```

- [ ] **Step 2: Write failing test**

```python
# tests/test_events_kimi_bytes.py
import re
from unittest.mock import AsyncMock, MagicMock

import pytest

from ai_router.browser.events import handle_response
from ai_router.browser.profile import ProviderProfile, ProviderSelectors, StreamDone


@pytest.mark.asyncio
async def test_handle_response_uses_body_when_read_response_bytes():
    captured = {}

    def parse_stream_done(status, body):
        captured["status"] = status
        captured["body"] = body
        return StreamDone(done=True, ok=True)

    profile = ProviderProfile(
        provider_id="kimi",
        stream_url_re=re.compile(r"ChatService/Chat"),
        parse_stream_done=parse_stream_done,
        is_stop_visible=AsyncMock(return_value=False),
        read_response_snapshot=AsyncMock(return_value=(0, "")),
        is_rate_limited=lambda t: False,
        submit_ready=AsyncMock(return_value=True),
        planner=MagicMock(),
        selectors=ProviderSelectors(prompt_input="#in", submit_button="#btn"),
        error_markers=(),
        recoverable_codes=(),
        read_response_bytes=True,
    )
    channel = MagicMock()
    channel.emit = AsyncMock()

    response = MagicMock()
    response.url = "https://www.kimi.com/apiv2/kimi.gateway.chat.v1.ChatService/Chat"
    response.status = 200
    response.finished = AsyncMock()
    response.body = AsyncMock(return_value=b"\x00\x00\x00\x00\x05{}")
    response.text = AsyncMock(side_effect=AssertionError("text() must not be called"))

    await handle_response(response, channel, [profile])

    assert captured["status"] == 200
    assert captured["body"] == b"\x00\x00\x00\x00\x05{}"
    channel.emit.assert_awaited_once()
```

- [ ] **Step 3: Run test — expect FAIL**

Run: `pytest tests/test_events_kimi_bytes.py -v`
Expected: FAIL — `read_response_bytes` missing or body not passed

- [ ] **Step 4: Implement events + profile changes**

```python
# src/ai_router/browser/events.py — inside handle_response, replace body fetch:
        await response.finished()
        status = response.status
        if profile.read_response_bytes:
            body = await response.body()
        else:
            body = await response.text()
```

Update trace `body_len` to handle bytes: `len(body)`.

- [ ] **Step 5: Run test — expect PASS**

Run: `pytest tests/test_events_kimi_bytes.py -v`
Expected: PASS

- [ ] **Step 6: Run full suite**

Run: `pytest -q`
Expected: all existing tests still pass

- [ ] **Step 7: Commit**

```bash
git add src/ai_router/browser/profile.py src/ai_router/browser/events.py tests/test_events_kimi_bytes.py
git commit -m "feat: support bytes response bodies for Connect RPC providers"
```

---

### Task 2: Kimi selectors + Connect stream parser

**Files:**
- Create: `src/ai_router/adapters/kimi/__init__.py`
- Create: `src/ai_router/adapters/kimi/selectors.py`
- Create: `src/ai_router/adapters/kimi/stream.py`
- Create: `tests/test_kimi_stream.py`

**Interfaces:**
- Produces: `KIMI_CHAT_RE`, `KIMI_NEW_CHAT_URL`, `FAILURE_STATUSES`, `RATE_LIMIT_MARKERS`
- Produces: `iter_connect_json_frames(raw: bytes) -> Iterator[dict]`
- Produces: `parse_stream_done(status: int, body: str | bytes) -> StreamDone`

- [ ] **Step 1: Write failing stream tests**

```python
# tests/test_kimi_stream.py
import json

from ai_router.adapters.kimi.stream import parse_stream_done


def _connect(*frames: dict, end_stream: bool = False) -> bytes:
    out = bytearray()
    for i, obj in enumerate(frames):
        payload = json.dumps(obj).encode("utf-8")
        flags = 0x80 if end_stream and i == len(frames) - 1 else 0x00
        out.append(flags)
        out.extend(len(payload).to_bytes(4, "big"))
        out.extend(payload)
    return bytes(out)


def test_completed_status_is_success():
    body = _connect({"status": "MESSAGE_STATUS_COMPLETED"})
    result = parse_stream_done(200, body)
    assert result.done is True
    assert result.ok is True


def test_nested_message_status_completed():
    body = _connect({"message": {"status": "MESSAGE_STATUS_COMPLETED"}})
    result = parse_stream_done(200, body)
    assert result.done is True
    assert result.ok is True


def test_failed_status_is_error():
    body = _connect({"status": "MESSAGE_STATUS_FAILED"})
    result = parse_stream_done(200, body)
    assert result.done is True
    assert result.ok is False
    assert result.error_kind == "error"


def test_partial_stream_not_done():
    body = _connect({"status": "MESSAGE_STATUS_RUNNING"})
    result = parse_stream_done(200, body)
    assert result.done is False


def test_http_429_is_rate_limit():
    result = parse_stream_done(429, b"too many requests")
    assert result.done is True
    assert result.ok is False
    assert result.error_kind == "rate_limit"


def test_substring_fallback_on_plain_text():
    text = b'{"status":"MESSAGE_STATUS_COMPLETED"}'
    result = parse_stream_done(200, text)
    assert result.done is True
    assert result.ok is True
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `pytest tests/test_kimi_stream.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement selectors + stream parser**

```python
# src/ai_router/adapters/kimi/__init__.py
# empty

# src/ai_router/adapters/kimi/selectors.py
import re

KIMI_URL = "https://www.kimi.com/"
KIMI_NEW_CHAT_URL = "https://www.kimi.com/?chat_enter_method=new_chat"

KIMI_CHAT_RE = re.compile(
    r"/apiv2/kimi\.gateway\.chat\.v1\.ChatService/Chat(?:\?|$)",
    re.I,
)

SEL_NEW_CHAT = (
    'button[aria-label*="New chat" i], '
    'a[aria-label*="New chat" i], '
    'button:has-text("New chat")'
)
SEL_PROMPT_INPUT = (
    'textarea:not([aria-hidden="true"]):visible, '
    'div[contenteditable="true"]:visible'
)
SEL_SUBMIT_BUTTON = (
    'button[aria-label*="Send" i], '
    'button[type="submit"]'
)
SEL_STOP_BUTTON = 'button[aria-label*="Stop" i]'
SEL_ASSISTANT_MAIN = ".segment-content-box"
SEL_ASSISTANT_TEXT = ".markdown-container .markdown"
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

KIMI_ERROR_MARKERS = (
    "something went wrong",
    "unable to respond",
    "an error occurred",
)

FAILURE_STATUSES = frozenset({
    "MESSAGE_STATUS_FAILED",
    "MESSAGE_STATUS_CANCELLED",
    "MESSAGE_STATUS_ERROR",
})

COMPLETED_STATUS = "MESSAGE_STATUS_COMPLETED"
```

```python
# src/ai_router/adapters/kimi/stream.py
from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from ai_router.adapters.kimi.selectors import (
    COMPLETED_STATUS,
    FAILURE_STATUSES,
    RATE_LIMIT_MARKERS,
)
from ai_router.browser.profile import StreamDone

_RATE_LIMIT_STATUSES = (401, 403, 429)


def _as_bytes(body: str | bytes) -> bytes:
    if isinstance(body, bytes):
        return body
    return body.encode("utf-8", errors="replace")


def iter_connect_json_frames(raw: bytes) -> Iterator[dict[str, Any]]:
    offset = 0
    while offset + 5 <= len(raw):
        length = int.from_bytes(raw[offset + 1 : offset + 5], "big")
        offset += 5
        if offset + length > len(raw):
            break
        payload = raw[offset : offset + length]
        offset += length
        try:
            obj = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(obj, dict):
            yield obj


def _status_values(obj: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for key in ("status",):
        val = obj.get(key)
        if isinstance(val, str):
            out.append(val)
    for nested in ("message", "result"):
        inner = obj.get(nested)
        if isinstance(inner, dict):
            val = inner.get("status")
            if isinstance(val, str):
                out.append(val)
    return out


def _scan_objects(objects: list[dict[str, Any]]) -> StreamDone | None:
    saw_completed = False
    for obj in objects:
        for status in _status_values(obj):
            if status in FAILURE_STATUSES:
                return StreamDone(
                    done=True,
                    ok=False,
                    error_kind="error",
                    error_text=f"Stream status: {status}",
                )
            if status == COMPLETED_STATUS:
                saw_completed = True
    if saw_completed:
        return StreamDone(done=True, ok=True)
    return None


def parse_stream_done(status: int, body: str | bytes) -> StreamDone:
    if status >= 400:
        text = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else body
        lower = text.lower()
        if status in _RATE_LIMIT_STATUSES or any(m in lower for m in RATE_LIMIT_MARKERS):
            return StreamDone(
                done=True,
                ok=False,
                error_kind="rate_limit",
                error_text=f"HTTP {status}: {text[:200]}",
            )
        return StreamDone(
            done=True,
            ok=False,
            error_kind="error",
            error_text=f"HTTP {status}: {text[:200]}",
        )

    raw = _as_bytes(body)
    objects = list(iter_connect_json_frames(raw))
    if objects:
        verdict = _scan_objects(objects)
        if verdict is not None:
            return verdict
        return StreamDone(done=False, ok=False)

    text = raw.decode("utf-8", errors="replace")
    if COMPLETED_STATUS in text:
        return StreamDone(done=True, ok=True)
    for fail in FAILURE_STATUSES:
        if fail in text:
            return StreamDone(
                done=True,
                ok=False,
                error_kind="error",
                error_text=f"Stream status: {fail}",
            )
    return StreamDone(done=False, ok=False)
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `pytest tests/test_kimi_stream.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ai_router/adapters/kimi/ tests/test_kimi_stream.py
git commit -m "feat: add Kimi Connect RPC stream parser and selectors"
```

---

### Task 3: Kimi wait helpers + planner

**Files:**
- Create: `src/ai_router/adapters/kimi/wait.py`
- Create: `src/ai_router/adapters/kimi/planner.py`
- Create: `tests/test_kimi_wait.py`
- Create: `tests/test_kimi_planner.py`

**Interfaces:**
- Produces: `read_response_snapshot(page) -> tuple[int, str]`
- Produces: `is_stop_visible(page) -> bool`
- Produces: `is_rate_limited(text) -> bool`
- Produces: `submit_ready(page) -> bool`
- Produces: `ensure_new_chat(page) -> None`
- Produces: `is_challenge_visible(page) -> bool`
- Produces: `KimiPlanner.plan(job) -> list[Command]`

- [ ] **Step 1: Write planner failing test**

```python
# tests/test_kimi_planner.py
from ai_router.adapters.kimi.planner import KimiPlanner
from ai_router.adapters.kimi.selectors import KIMI_NEW_CHAT_URL
from ai_router.browser.page_queue import AskJob


def make_job() -> AskJob:
    import asyncio

    loop = asyncio.new_event_loop()
    fut = loop.create_future()
    return AskJob("1", "sess", "hello", "kimi", fut, 600.0)


def test_plan_opens_fresh_chat_url():
    cmds = KimiPlanner().plan(make_job())
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
    assert cmds[0].args["url"] == KIMI_NEW_CHAT_URL
    assert "new_chat" not in ops
```

- [ ] **Step 2: Write wait helper test**

```python
# tests/test_kimi_wait.py
from ai_router.adapters.kimi.wait import is_rate_limited


def test_is_rate_limited_short_error_text():
    assert is_rate_limited("Rate limit exceeded") is True


def test_is_rate_limited_ignores_long_discussion():
    text = "rate limit " + ("x" * 500)
    assert is_rate_limited(text) is False
```

- [ ] **Step 3: Run tests — expect FAIL**

Run: `pytest tests/test_kimi_planner.py tests/test_kimi_wait.py -v`
Expected: FAIL

- [ ] **Step 4: Implement wait + planner**

```python
# src/ai_router/adapters/kimi/planner.py
from ai_router.adapters.kimi.selectors import KIMI_NEW_CHAT_URL
from ai_router.browser.commands import Command
from ai_router.browser.page_queue import AskJob


class KimiPlanner:
    def plan(self, job: AskJob, *, recovery: bool = False) -> list[Command]:
        return [
            Command("goto", {"url": KIMI_NEW_CHAT_URL}),
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

```python
# src/ai_router/adapters/kimi/wait.py
from __future__ import annotations

from playwright.async_api import Page

from ai_router.adapters.kimi.selectors import (
    CHALLENGE_MARKERS,
    KIMI_NEW_CHAT_URL,
    RATE_LIMIT_MARKERS,
    SEL_ASSISTANT_MAIN,
    SEL_ASSISTANT_TEXT,
    SEL_CHALLENGE,
    SEL_NEW_CHAT,
    SEL_PROMPT_INPUT,
    SEL_STOP_BUTTON,
)

_STRIP_UI_JS = """(el) => {
    const clone = el.cloneNode(true);
    clone.querySelectorAll(
        '.table-actions, .icon-button, .kimi-tooltip'
    ).forEach(n => n.remove());
    return clone.innerText.trim();
}"""


def is_rate_limited(text: str) -> bool:
    lower = text.strip().lower()
    if len(lower) > 400:
        return False
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


async def submit_ready(page: Page) -> bool:
    loc = page.locator(SEL_PROMPT_INPUT).first
    if await loc.count() == 0:
        return False
    try:
        return await loc.is_visible() and await loc.is_editable()
    except Exception:
        return False


async def read_response_snapshot(page: Page) -> tuple[int, str]:
    segments = page.locator(SEL_ASSISTANT_MAIN)
    count = await segments.count()
    if count == 0:
        return 0, ""
    last = segments.nth(count - 1)
    markdown = last.locator(SEL_ASSISTANT_TEXT).first
    if await markdown.count() == 0:
        return count, ""
    try:
        text = await markdown.evaluate(_STRIP_UI_JS)
    except Exception:
        text = ""
    return count, text or ""


async def ensure_new_chat(page: Page) -> None:
    await page.goto(KIMI_NEW_CHAT_URL, wait_until="domcontentloaded")
    count, _ = await read_response_snapshot(page)
    if count == 0:
        return
    btn = page.locator(SEL_NEW_CHAT).first
    if await btn.count() > 0:
        await btn.click()
        await page.wait_for_timeout(500)
```

- [ ] **Step 5: Run tests — expect PASS**

Run: `pytest tests/test_kimi_planner.py tests/test_kimi_wait.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/ai_router/adapters/kimi/wait.py src/ai_router/adapters/kimi/planner.py tests/test_kimi_planner.py tests/test_kimi_wait.py
git commit -m "feat: add Kimi planner and DOM wait helpers"
```

---

### Task 4: KimiAdapter + registry + config

**Files:**
- Create: `src/ai_router/adapters/kimi/adapter.py`
- Modify: `src/ai_router/adapters/registry.py`
- Modify: `src/ai_router/config.py`
- Modify: `tests/test_registry.py`
- Modify: `tests/test_router.py`
- Modify: `tests/test_config.py`
- Modify: `tests/test_ask_multi.py`

**Interfaces:**
- Produces: `KimiAdapter` with `id="kimi"`, `build_profile(cfg) -> ProviderProfile`
- Produces: `build_registry()` includes `KimiAdapter()`
- Produces: `AppConfig.kimi_answer_timeout_s: float = 600.0`
- Produces: default provider URL `https://www.kimi.com/?chat_enter_method=new_chat`

- [ ] **Step 1: Write failing registry test**

```python
# tests/test_registry.py — add:
def test_build_registry_includes_kimi():
    registry = build_registry()
    assert "kimi" in {a.id for a in registry.list_all()}
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `pytest tests/test_registry.py::test_build_registry_includes_kimi -v`
Expected: FAIL

- [ ] **Step 3: Implement adapter + wire registry + config**

Mirror `DeepSeekAdapter` structure. Key `build_profile` fields:

```python
ProviderProfile(
    provider_id="kimi",
    stream_url_re=KIMI_CHAT_RE,
    parse_stream_done=parse_stream_done,
    is_stop_visible=is_stop_visible,
    read_response_snapshot=read_response_snapshot,
    is_rate_limited=is_rate_limited,
    submit_ready=submit_ready,
    planner=KimiPlanner(),
    selectors=ProviderSelectors(
        prompt_input=SEL_PROMPT_INPUT,
        submit_button=SEL_SUBMIT_BUTTON,
    ),
    error_markers=KIMI_ERROR_MARKERS,
    recoverable_codes=("KIMI_ERROR",),
    answer_timeout_s=cfg.kimi_answer_timeout_s,
    read_response_bytes=True,
    on_new_chat=ensure_new_chat,
    is_challenge_visible=is_challenge_visible,
)
```

`config.py` changes:
- Add `kimi_answer_timeout_s: float = 600.0`
- Default providers entry: `"kimi": ProviderConfig(url="https://www.kimi.com/?chat_enter_method=new_chat")`
- YAML key `kimi_answer_timeout_s`
- Env `AI_ROUTER_KIMI_ANSWER_TIMEOUT_S`

`registry.py`: `registry.register(KimiAdapter())`

- [ ] **Step 4: Update router/config/ask_multi tests**

```python
# tests/test_router.py
def test_resolve_kimi_provider():
    registry = build_registry()
    adapter, reason = resolve_provider(registry, "kimi", default="gemini")
    assert adapter.id == "kimi"

# tests/test_config.py
def test_load_config_defaults_includes_kimi(tmp_path):
    cfg = load_config(tmp_path / "missing.yaml")
    assert "kimi" in cfg.providers
    assert cfg.providers["kimi"].url == "https://www.kimi.com/?chat_enter_method=new_chat"
    assert cfg.kimi_answer_timeout_s == 600.0

# tests/test_ask_multi.py — add FakeAdapter("kimi") to fakes dicts
```

- [ ] **Step 5: Run targeted tests — expect PASS**

Run: `pytest tests/test_registry.py tests/test_router.py tests/test_config.py tests/test_ask_multi.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/ai_router/adapters/kimi/adapter.py src/ai_router/adapters/registry.py src/ai_router/config.py tests/
git commit -m "feat: register Kimi provider with config and adapter wiring"
```

---

### Task 5: Documentation + full verification

**Files:**
- Modify: `README.md`
- Modify: `pyproject.toml`

- [ ] **Step 1: Update README**

Add Kimi to:
- intro paragraph (supported providers list)
- `ai-router browser login --provider kimi` example
- providers table
- note 600s default timeout for thinking models

- [ ] **Step 2: Update pyproject.toml**

Add `kimi` to `description` and `keywords`.

- [ ] **Step 3: Run full test suite**

Run: `pytest -q`
Expected: all tests PASS

- [ ] **Step 4: Run linter**

Run: `ruff check src tests`
Expected: no errors

- [ ] **Step 5: Commit**

```bash
git add README.md pyproject.toml
git commit -m "docs: document Kimi provider support"
```

---

## Manual verification (post-implementation)

After unit tests pass, verify with a logged-in browser session:

```bash
ai-router browser login --provider kimi
ai-router browser status
# Expect kimi: logged_in

# MCP or CLI ask smoke test (requires live session):
# ask(provider=kimi, prompt="Reply with exactly: kimi router working")
```

Confirm:
1. Navigation lands on fresh chat URL
2. Connect stream emits `MESSAGE_STATUS_COMPLETED`
3. Returned text excludes table Copy/Download chrome
4. Thinking blocks excluded when enabled on account

---

## Spec coverage checklist

| Spec requirement | Task |
|------------------|------|
| Browser automation | Task 4 adapter |
| `KIMI_NEW_CHAT_URL` per ask | Task 3 planner |
| Connect frame parser | Task 2 stream |
| `read_response_bytes` | Task 1 events |
| DOM `.markdown-container .markdown` | Task 3 wait |
| Strip `.table-actions` | Task 3 wait JS |
| 600s timeout | Task 4 config |
| Registry + tests | Task 4 |
| README | Task 5 |
