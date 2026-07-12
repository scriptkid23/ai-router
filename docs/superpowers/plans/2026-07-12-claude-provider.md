# Claude Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `claude` provider that automates claude.ai web sessions — DOM answer text, SSE `/completion` stream-end signals — following the ChatGPT adapter pattern.

**Architecture:** New `src/ai_router/adapters/claude/` package with `selectors`, `stream`, `wait`, `planner`, and `adapter` modules. Register in `build_registry()`, add config default URL, wire `ProviderProfile` into existing `PageWorker` / `StateReducer` pipeline. No changes to core browser engine.

**Tech Stack:** Python 3.11+, Playwright (CloakBrowser), existing `ProviderProfile` / `Command` / `StateReducer` infrastructure.

**Spec:** `docs/superpowers/specs/2026-07-12-claude-provider-design.md`

---

## File map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/ai_router/adapters/claude/__init__.py` | Create | Package marker |
| `src/ai_router/adapters/claude/selectors.py` | Create | URLs, regex, DOM selectors, error markers |
| `src/ai_router/adapters/claude/stream.py` | Create | SSE `parse_stream_done` |
| `src/ai_router/adapters/claude/wait.py` | Create | DOM wait helpers |
| `src/ai_router/adapters/claude/planner.py` | Create | Command plan per ask |
| `src/ai_router/adapters/claude/adapter.py` | Create | `ClaudeAdapter` + `build_profile` |
| `src/ai_router/adapters/registry.py` | Modify | Register `ClaudeAdapter` |
| `src/ai_router/config.py` | Modify | Default `claude` provider URL + optional timeout |
| `tests/test_claude_stream.py` | Create | Stream parser unit tests |
| `tests/test_claude_wait.py` | Create | Rate-limit helper unit tests |
| `tests/test_claude_planner.py` | Create | Planner unit tests |
| `tests/test_registry.py` | Create | Registry includes claude |
| `tests/test_router.py` | Modify | Resolve `provider=claude` |
| `tests/test_config.py` | Modify | Assert `claude` in default providers |
| `README.md` | Modify | Document Claude provider |

---

### Task 1: Selectors module

**Files:**
- Create: `src/ai_router/adapters/claude/__init__.py`
- Create: `src/ai_router/adapters/claude/selectors.py`

- [ ] **Step 1: Create empty package**

```python
# src/ai_router/adapters/claude/__init__.py
# (empty file)
```

- [ ] **Step 2: Create selectors**

```python
# src/ai_router/adapters/claude/selectors.py
import re

CLAUDE_URL = "https://claude.ai/new"

CLAUDE_COMPLETION_RE = re.compile(
    r"/api/organizations/[^/]+/chat_conversations/[^/]+/completion(?:\?|$)",
    re.I,
)

SEL_PROMPT_INPUT = (
    'div[contenteditable="true"][data-placeholder], '
    'div.ProseMirror[contenteditable="true"], '
    "textarea"
)
SEL_SUBMIT_BUTTON = (
    'button[aria-label*="Send" i], '
    'button[data-testid="send-button"]'
)
SEL_STOP_BUTTON = (
    'button[aria-label*="Stop" i], '
    'div[data-is-streaming="true"]'
)
SEL_ASSISTANT_TURN = 'div[role="article"]'
SEL_ASSISTANT_MESSAGE = 'div[data-last-message="true"]'
SEL_ASSISTANT_TEXT = ".font-claude-response"
SEL_STREAMING = 'div[data-is-streaming="true"]'
SEL_LOGIN = 'a[href*="/login"], button:has-text("Log in")'

RATE_LIMIT_MARKERS = (
    "rate limit",
    "usage limit",
    "too many messages",
    "try again later",
)

CLAUDE_ERROR_MARKERS = (
    "something went wrong",
    "unable to respond",
    "an error occurred",
)
```

- [ ] **Step 3: Commit**

```bash
git add src/ai_router/adapters/claude/
git commit -m "feat(claude): add selectors and completion URL regex"
```

---

### Task 2: Stream parser (TDD)

**Files:**
- Create: `tests/test_claude_stream.py`
- Create: `src/ai_router/adapters/claude/stream.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_claude_stream.py
from ai_router.adapters.claude.stream import parse_stream_done


def _sse(*lines: str) -> str:
    return "\n".join(lines)


def test_message_stop_is_success():
    body = _sse(
        'data: {"type":"content_block_delta","index":1,'
        '"delta":{"type":"text_delta","text":"1 + 1 = 2"}}',
        'data: {"type":"message_stop"}',
    )
    result = parse_stream_done(200, body)
    assert result.done is True
    assert result.ok is True
    assert result.error_kind is None


def test_message_delta_end_turn_is_success():
    body = _sse(
        'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"}}',
    )
    result = parse_stream_done(200, body)
    assert result.done is True
    assert result.ok is True


def test_partial_stream_not_done():
    body = _sse(
        'data: {"type":"content_block_delta","index":1,'
        '"delta":{"type":"text_delta","text":"hello"}}',
    )
    result = parse_stream_done(200, body)
    assert result.done is False
    assert result.ok is False


def test_http_429_is_rate_limit():
    result = parse_stream_done(429, "too many requests")
    assert result.done is True
    assert result.ok is False
    assert result.error_kind == "rate_limit"


def test_message_limit_out_of_quota_is_rate_limit():
    body = _sse(
        'data: {"type":"message_limit","message_limit":{'
        '"type":"over_limit",'
        '"windows":{"5h":{"status":"over_limit"}}}}',
    )
    result = parse_stream_done(200, body)
    assert result.done is True
    assert result.ok is False
    assert result.error_kind == "rate_limit"


def test_message_limit_within_limit_is_not_error():
    body = _sse(
        'data: {"type":"message_limit","message_limit":{'
        '"type":"within_limit",'
        '"windows":{"5h":{"status":"within_limit"}}}}',
        'data: {"type":"message_stop"}',
    )
    result = parse_stream_done(200, body)
    assert result.done is True
    assert result.ok is True
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_claude_stream.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'ai_router.adapters.claude.stream'`

- [ ] **Step 3: Implement stream parser**

```python
# src/ai_router/adapters/claude/stream.py
from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from ai_router.adapters.claude.selectors import RATE_LIMIT_MARKERS
from ai_router.browser.profile import StreamDone

_RATE_LIMIT_STATUSES = (401, 403, 429)


def _iter_data_payloads(body: str) -> Iterator[dict[str, Any]]:
    """Yield each JSON object carried on a `data:` SSE line."""
    for raw in body.splitlines():
        line = raw.strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:") :].strip()
        if not payload:
            continue
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            yield data


def _is_out_of_quota(data: dict[str, Any]) -> bool:
    if data.get("type") != "message_limit":
        return False
    ml = data.get("message_limit")
    if not isinstance(ml, dict):
        return False
    if ml.get("type") not in (None, "within_limit"):
        return True
    windows = ml.get("windows")
    if isinstance(windows, dict):
        for window in windows.values():
            if isinstance(window, dict) and window.get("status") not in (
                None,
                "within_limit",
            ):
                return True
    resolved = ml.get("resolved")
    if isinstance(resolved, dict) and resolved.get("status") not in (None, "ok"):
        return True
    return False


def parse_stream_done(status: int, body: str) -> StreamDone:
    """Classify a finished claude.ai /completion SSE body.

    Success requires message_stop or message_delta with stop_reason end_turn.
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

    saw_end_turn = False
    saw_message_stop = False

    for data in _iter_data_payloads(body):
        if _is_out_of_quota(data):
            return StreamDone(
                done=True,
                ok=False,
                error_kind="rate_limit",
                error_text="Claude usage limit reached",
            )
        dtype = data.get("type")
        if dtype == "message_stop":
            saw_message_stop = True
        elif dtype == "message_delta":
            delta = data.get("delta")
            if isinstance(delta, dict) and delta.get("stop_reason") == "end_turn":
                saw_end_turn = True

    if saw_message_stop or saw_end_turn:
        return StreamDone(done=True, ok=True)
    return StreamDone(done=False, ok=False)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_claude_stream.py -v
```

Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add tests/test_claude_stream.py src/ai_router/adapters/claude/stream.py
git commit -m "feat(claude): add SSE completion stream parser"
```

---

### Task 3: Wait helpers

**Files:**
- Create: `tests/test_claude_wait.py`
- Create: `src/ai_router/adapters/claude/wait.py`

- [ ] **Step 1: Write failing test for rate limit helper**

```python
# tests/test_claude_wait.py
from ai_router.adapters.claude.wait import is_rate_limited


def test_rate_limit_english():
    assert is_rate_limited("You hit the usage limit, try again later") is True


def test_rate_limit_negative():
    assert is_rate_limited("Here is a normal answer about Python") is False
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_claude_wait.py -v
```

Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement wait helpers**

```python
# src/ai_router/adapters/claude/wait.py
from __future__ import annotations

from playwright.async_api import Page

from ai_router.adapters.claude.selectors import (
    RATE_LIMIT_MARKERS,
    SEL_ASSISTANT_MESSAGE,
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
    """True while Claude is still generating."""
    if await page.locator(SEL_STOP_BUTTON).count() > 0:
        return True
    return False


async def read_response_snapshot(page: Page) -> tuple[int, str]:
    """Return assistant turn count and text of the latest assistant message."""
    last_msg = page.locator(SEL_ASSISTANT_MESSAGE)
    if await last_msg.count():
        inner = last_msg.locator(SEL_ASSISTANT_TEXT)
        if await inner.count():
            text = (await inner.first.inner_text()).strip()
        else:
            text = (await last_msg.first.inner_text()).strip()
        return 1, text

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
    if await page.locator(SEL_PROMPT_INPUT).count() == 0:
        return False
    submit = page.locator(SEL_SUBMIT_BUTTON).first
    if await submit.count() == 0:
        return False
    return not await submit.is_disabled()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_claude_wait.py -v
```

Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add tests/test_claude_wait.py src/ai_router/adapters/claude/wait.py
git commit -m "feat(claude): add DOM wait helpers"
```

---

### Task 4: Planner (TDD)

**Files:**
- Create: `tests/test_claude_planner.py`
- Create: `src/ai_router/adapters/claude/planner.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_claude_planner.py
from ai_router.adapters.claude.planner import ClaudePlanner
from ai_router.adapters.claude.selectors import CLAUDE_URL
from ai_router.browser.page_queue import AskJob


def make_job() -> AskJob:
    import asyncio

    loop = asyncio.new_event_loop()
    fut = loop.create_future()
    return AskJob("1", "sess", "hello", "claude", fut, 300.0)


def test_plan_opens_fresh_chat_first():
    cmds = ClaudePlanner().plan(make_job())
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
    assert cmds[0].args["url"] == CLAUDE_URL


def test_recovery_plan_also_opens_fresh_chat():
    cmds = ClaudePlanner().plan(make_job(), recovery=True)
    assert cmds[0].op == "goto"
    assert cmds[0].args["url"] == CLAUDE_URL
    assert cmds[1].op == "wait_idle"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_claude_planner.py -v
```

Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement planner**

```python
# src/ai_router/adapters/claude/planner.py
from ai_router.adapters.claude.selectors import CLAUDE_URL
from ai_router.browser.commands import Command
from ai_router.browser.page_queue import AskJob


class ClaudePlanner:
    def plan(self, job: AskJob, *, recovery: bool = False) -> list[Command]:
        return [
            Command("goto", {"url": CLAUDE_URL}),
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
pytest tests/test_claude_planner.py -v
```

Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add tests/test_claude_planner.py src/ai_router/adapters/claude/planner.py
git commit -m "feat(claude): add planner with fresh-chat-per-ask flow"
```

---

### Task 5: Adapter

**Files:**
- Create: `src/ai_router/adapters/claude/adapter.py`

- [ ] **Step 1: Implement ClaudeAdapter**

```python
# src/ai_router/adapters/claude/adapter.py
from __future__ import annotations

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from ai_router.adapters.base import SessionStatus
from ai_router.adapters.claude.planner import ClaudePlanner
from ai_router.adapters.claude.selectors import (
    CLAUDE_COMPLETION_RE,
    CLAUDE_ERROR_MARKERS,
    CLAUDE_URL,
    SEL_LOGIN,
    SEL_PROMPT_INPUT,
    SEL_SUBMIT_BUTTON,
)
from ai_router.adapters.claude.stream import parse_stream_done
from ai_router.adapters.claude.wait import (
    is_rate_limited,
    is_stop_visible,
    read_response_snapshot,
    submit_ready,
)
from ai_router.browser.profile import ProviderProfile, ProviderSelectors
from ai_router.config import AppConfig


class ClaudeAdapter:
    id = "claude"
    name = "Claude"
    keywords: list[str] = ["claude", "@claude", "anthropic"]
    status = "available"

    async def check_session(self, page: Page) -> SessionStatus:
        return await self.ensure_page_ready(page)

    async def ensure_page_ready(self, page: Page) -> SessionStatus:
        """Open a fresh chat and verify login."""
        await page.goto(CLAUDE_URL, wait_until="domcontentloaded")
        try:
            await page.wait_for_selector(SEL_PROMPT_INPUT, timeout=15_000)
            return SessionStatus.LOGGED_IN
        except PlaywrightTimeout:
            if await page.locator(SEL_LOGIN).count() > 0:
                return SessionStatus.LOGGED_OUT
            return SessionStatus.UNKNOWN

    async def open_new_chat(self, page: Page) -> None:
        await page.goto(CLAUDE_URL, wait_until="domcontentloaded")

    def build_profile(self, cfg: AppConfig) -> ProviderProfile:
        return ProviderProfile(
            provider_id=self.id,
            stream_url_re=CLAUDE_COMPLETION_RE,
            parse_stream_done=parse_stream_done,
            is_stop_visible=is_stop_visible,
            read_response_snapshot=read_response_snapshot,
            is_rate_limited=is_rate_limited,
            submit_ready=submit_ready,
            planner=ClaudePlanner(),
            selectors=ProviderSelectors(
                prompt_input=SEL_PROMPT_INPUT,
                submit_button=SEL_SUBMIT_BUTTON,
            ),
            error_markers=CLAUDE_ERROR_MARKERS,
            recoverable_codes=("CLAUDE_ERROR", "CLAUDE_INCOMPLETE"),
            answer_timeout_s=getattr(cfg, "claude_answer_timeout_s", None),
            parse_ws_frame=None,
        )
```

- [ ] **Step 2: Commit**

```bash
git add src/ai_router/adapters/claude/adapter.py
git commit -m "feat(claude): add ClaudeAdapter with ProviderProfile wiring"
```

---

### Task 6: Registry and config

**Files:**
- Modify: `src/ai_router/adapters/registry.py`
- Modify: `src/ai_router/config.py`
- Create: `tests/test_registry.py`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Write failing registry test**

```python
# tests/test_registry.py
from ai_router.adapters.registry import build_registry


def test_build_registry_includes_claude():
    registry = build_registry()
    ids = [a.id for a in registry.list_all()]
    assert "claude" in ids


def test_claude_adapter_is_available():
    registry = build_registry()
    claude = registry.get("claude")
    assert claude.status == "available"
    assert claude.name == "Claude"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_registry.py -v
```

Expected: FAIL — `"claude" not in ids`

- [ ] **Step 3: Register adapter in registry**

```python
# src/ai_router/adapters/registry.py — update build_registry()
def build_registry() -> ProviderRegistry:
    from ai_router.adapters.claude.adapter import ClaudeAdapter
    from ai_router.adapters.gemini.adapter import GeminiAdapter

    registry = ProviderRegistry()
    registry.register(GeminiAdapter())
    registry.register(ChatGPTAdapter())
    registry.register(ClaudeAdapter())
    return registry
```

- [ ] **Step 4: Add config defaults**

In `src/ai_router/config.py`:

1. Add field to `AppConfig`:
```python
claude_answer_timeout_s: float | None = None
```

2. Add to `_defaults()` providers dict:
```python
"claude": ProviderConfig(url="https://claude.ai/new"),
```

3. Add YAML loader (after `chatgpt_answer_timeout_s` block):
```python
if "claude_answer_timeout_s" in raw:
    cfg.claude_answer_timeout_s = float(raw["claude_answer_timeout_s"])
```

4. Add env override:
```python
if v := os.getenv("AI_ROUTER_CLAUDE_ANSWER_TIMEOUT_S"):
    cfg.claude_answer_timeout_s = float(v)
```

5. Fix adapter to use `cfg.claude_answer_timeout_s` directly (remove `getattr`):
```python
answer_timeout_s=cfg.claude_answer_timeout_s,
```

- [ ] **Step 5: Update config test**

Add to `tests/test_config.py`:
```python
def test_load_config_defaults_includes_claude(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = load_config()
    assert "claude" in cfg.providers
    assert cfg.providers["claude"].url == "https://claude.ai/new"
```

- [ ] **Step 6: Run tests**

```bash
pytest tests/test_registry.py tests/test_config.py -v
```

Expected: all passed

- [ ] **Step 7: Commit**

```bash
git add src/ai_router/adapters/registry.py src/ai_router/config.py \
  tests/test_registry.py tests/test_config.py \
  src/ai_router/adapters/claude/adapter.py
git commit -m "feat(claude): register provider and add config defaults"
```

---

### Task 7: Router resolve test

**Files:**
- Modify: `tests/test_router.py`

- [ ] **Step 1: Add claude resolve test**

```python
# Add import at top:
from ai_router.adapters.claude.adapter import ClaudeAdapter

# Add test:
def test_resolve_claude_provider():
    registry = ProviderRegistry([_FakeGemini(), ChatGPTAdapter(), ClaudeAdapter()])
    adapter, reason = resolve_provider(registry, "claude", default="gemini")
    assert adapter.id == "claude"
    assert reason == "explicit param"
```

- [ ] **Step 2: Run test**

```bash
pytest tests/test_router.py -v
```

Expected: all passed

- [ ] **Step 3: Commit**

```bash
git add tests/test_router.py
git commit -m "test: add claude provider routing case"
```

---

### Task 8: README documentation

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update provider mentions**

Apply these edits:

1. Line ~5: Change "Gemini and ChatGPT" → "Gemini, ChatGPT, and Claude"
2. Line ~18: `gemini/chatgpt: logged_in` → `gemini/chatgpt/claude: logged_in`
3. After chatgpt login example (~74), add:
   ```bash
   ai-router browser login --provider claude
   ```
4. Status example (~85-86): add `# claude: logged_in`
5. MCP `ask` table (~156): mention `provider` can be `"claude"`
6. CLI help (~172-173): `gemini|chatgpt|claude`
7. Config YAML example (~192-195): add:
   ```yaml
     claude:
       url: https://claude.ai/new
   ```
8. Env table: add row:
   | `AI_ROUTER_CLAUDE_ANSWER_TIMEOUT_S` | unset | Per-provider answer timeout override |

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: add Claude provider to README"
```

---

### Task 9: Full test suite and manual smoke test

**Files:** (none — verification only)

- [ ] **Step 1: Run full unit test suite**

```bash
pytest -v
```

Expected: all tests pass (no regressions)

- [ ] **Step 2: Manual smoke test (requires Claude account)**

```bash
ai-router browser login --provider claude
ai-router browser status --provider claude
# Expected: claude: logged_in

# Start MCP server and ask:
# ask(prompt="Reply with exactly: router working", provider="claude")
# Expected: answer contains "router working"
```

- [ ] **Step 3: Tune selectors if smoke test fails**

If prompt input or submit button not found during smoke test, inspect live DOM at `claude.ai/new` and update `SEL_PROMPT_INPUT` / `SEL_SUBMIT_BUTTON` in `selectors.py`. Re-run smoke test. This is the only expected post-merge adjustment.

- [ ] **Step 4: Final commit (only if selector fixes needed)**

```bash
git add src/ai_router/adapters/claude/selectors.py
git commit -m "fix(claude): tune DOM selectors after smoke test"
```

---

## Spec coverage checklist

| Spec requirement | Task |
|------------------|------|
| DOM-only answer | Task 3 (`read_response_snapshot`) |
| SSE signal-only | Task 2 (`parse_stream_done`) |
| New chat per ask | Task 4 (planner `goto` CLAUDE_URL) |
| Account default model | No task (out of scope by design) |
| Full assistant DOM content | Task 3 (no thinking filter) |
| Registry registration | Task 6 |
| Config URL + timeout | Task 6 |
| Session/login | Task 5 (`ensure_page_ready`) + Task 8 (README) |
| Error codes CLAUDE_* | Task 5 (`recoverable_codes`) + existing `commands.py` |
| Unit tests | Tasks 2, 3, 4, 6, 7 |
| README | Task 8 |

## Out of scope (do not implement)

- Model selection via UI
- Multi-turn conversation retention
- SSE `text_delta` answer extraction
- `parse_ws_frame` WebSocket support
- Anthropic API (`api.anthropic.com`)
