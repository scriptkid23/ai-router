# ai-router Python Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python MCP server that routes prompts to Gemini Web via CloakBrowser, exposed over HTTP/SSE for `mcp-remote`, with Typer CLI (`ai serve`, `ai browser login|status`) and extensible provider adapters.

**Architecture:** Monolith Python process on branch `python`. `ai serve` runs FastAPI/uvicorn with MCP Streamable HTTP. `BrowserManager` holds one headed CloakBrowser persistent context. `SessionManager` maps `Mcp-Session-Id` → Playwright Page per Cursor tab. `GeminiAdapter` implements battle-tested network + DOM wait logic. ChatGPT stub registered as `coming_soon`.

**Tech Stack:** Python 3.11+, Typer, cloakbrowser, mcp (Python SDK), uvicorn, pyyaml, pytest, pytest-asyncio

**Spec:** `docs/superpowers/specs/2026-07-09-ai-router-python-design.md`

---

## File Map

| File | Responsibility |
|------|----------------|
| `pyproject.toml` | deps, `[project.scripts] ai = ...`, pytest config |
| `src/ai_router/__init__.py` | package version |
| `src/ai_router/config.py` | load YAML + env overrides |
| `src/ai_router/errors.py` | `AiRouterError` + error codes |
| `src/ai_router/adapters/base.py` | `SessionStatus`, `ProviderAdapter` Protocol |
| `src/ai_router/adapters/registry.py` | register gemini + chatgpt stub |
| `src/ai_router/adapters/gemini/selectors.py` | Gemini DOM constants |
| `src/ai_router/adapters/gemini/wait.py` | network signal + DOM polling helpers |
| `src/ai_router/adapters/gemini/adapter.py` | `GeminiAdapter` |
| `src/ai_router/adapters/chatgpt/adapter.py` | stub `ChatGPTAdapter` |
| `src/ai_router/router/resolve.py` | resolve provider id → adapter |
| `src/ai_router/browser/manager.py` | CloakBrowser launch, mutex, lifecycle |
| `src/ai_router/session/manager.py` | MCP session → Page map |
| `src/ai_router/mcp/tools.py` | ask, list_providers, session_status handlers |
| `src/ai_router/mcp/server.py` | FastMCP app + tool registration |
| `src/ai_router/cli/main.py` | Typer root `ai` |
| `src/ai_router/cli/serve.py` | `ai serve` |
| `src/ai_router/cli/browser.py` | `ai browser login|status` |
| `tests/test_config.py` | config load tests |
| `tests/test_router.py` | router tests |
| `tests/test_gemini_wait.py` | brace balance, rate-limit helpers |
| `README.md` | setup, Cursor config, workflow |

---

### Task 1: Project scaffold

**Files:**
- Create: `pyproject.toml`
- Create: `src/ai_router/__init__.py`
- Modify: `.gitignore` (already has Python entries — verify)
- Modify: `README.md` (stub only; full README in Task 12)

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "ai-router"
version = "0.1.0"
description = "MCP server routing prompts to web AI providers via CloakBrowser"
requires-python = ">=3.11"
dependencies = [
    "typer[all]>=0.12",
    "cloakbrowser>=0.1",
    "mcp>=1.6",
    "uvicorn>=0.30",
    "pyyaml>=6.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "ruff>=0.6",
]

[project.scripts]
ai = "ai_router.cli.main:app"

[tool.hatch.build.targets.wheel]
packages = ["src/ai_router"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
line-length = 100
src = ["src"]
```

- [ ] **Step 2: Create package init**

```python
# src/ai_router/__init__.py
__version__ = "0.1.0"
```

- [ ] **Step 3: Install editable**

```bash
pip install -e ".[dev]"
```

Expected: installs without error.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml src/ai_router/__init__.py
git commit -m "chore: scaffold Python project with pyproject.toml"
```

---

### Task 2: Config and errors

**Files:**
- Create: `src/ai_router/errors.py`
- Create: `src/ai_router/config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write failing config test**

```python
# tests/test_config.py
import os
from pathlib import Path

from ai_router.config import load_config


def test_load_config_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("AI_ROUTER_DEFAULT_PROVIDER", raising=False)
    cfg = load_config()
    assert cfg.default_provider == "gemini"
    assert cfg.port == 8087
    assert cfg.host == "127.0.0.1"
    assert cfg.answer_timeout_s == 120
    assert "gemini" in cfg.providers


def test_env_override_default_provider(monkeypatch):
    monkeypatch.setenv("AI_ROUTER_DEFAULT_PROVIDER", "gemini")
    cfg = load_config()
    assert cfg.default_provider == "gemini"


def test_env_override_port(monkeypatch):
    monkeypatch.setenv("AI_ROUTER_PORT", "9090")
    cfg = load_config()
    assert cfg.port == 9090
```

- [ ] **Step 2: Run test — expect FAIL**

```bash
pytest tests/test_config.py -v
```

Expected: `ModuleNotFoundError` or `ImportError`

- [ ] **Step 3: Implement errors**

```python
# src/ai_router/errors.py
from dataclasses import dataclass


@dataclass
class AiRouterError(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


class NotLoggedInError(AiRouterError):
    def __init__(self, message: str = "Not logged in. Run: ai browser login"):
        super().__init__("NOT_LOGGED_IN", message)


class ProviderNotReadyError(AiRouterError):
    def __init__(self, provider: str):
        super().__init__(
            "PROVIDER_NOT_READY",
            f"Provider '{provider}' is not implemented yet",
        )


class BrowserBusyError(AiRouterError):
    def __init__(self):
        super().__init__("BROWSER_BUSY", "Browser is busy with another request")


class TimeoutError_(AiRouterError):
    def __init__(self, message: str = "Answer did not arrive in time"):
        super().__init__("TIMEOUT", message)


class RateLimitedError(AiRouterError):
    def __init__(self, message: str = "Rate limit reached. Try again later"):
        super().__init__("RATE_LIMITED", message)
```

- [ ] **Step 4: Implement config**

```python
# src/ai_router/config.py
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

CONFIG_DIR = Path.home() / ".ai-router"
DEFAULT_CONFIG_PATH = CONFIG_DIR / "config.yaml"


@dataclass
class ProviderConfig:
    url: str


@dataclass
class AppConfig:
    profile_dir: Path
    default_provider: str
    host: str
    port: int
    answer_timeout_s: int
    providers: dict[str, ProviderConfig] = field(default_factory=dict)


def _expand(path: str) -> Path:
    return Path(path).expanduser().resolve()


def _defaults() -> AppConfig:
    return AppConfig(
        profile_dir=CONFIG_DIR / "profile",
        default_provider="gemini",
        host="127.0.0.1",
        port=8087,
        answer_timeout_s=120,
        providers={
            "gemini": ProviderConfig(url="https://gemini.google.com/app"),
        },
    )


def load_config(path: Path | None = None) -> AppConfig:
    cfg = _defaults()
    config_path = path or DEFAULT_CONFIG_PATH
    if config_path.exists():
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if "profile_dir" in raw:
            cfg.profile_dir = _expand(raw["profile_dir"])
        if "default_provider" in raw:
            cfg.default_provider = raw["default_provider"]
        if "host" in raw:
            cfg.host = raw["host"]
        if "port" in raw:
            cfg.port = int(raw["port"])
        if "answer_timeout_s" in raw:
            cfg.answer_timeout_s = int(raw["answer_timeout_s"])
        if "providers" in raw:
            for pid, pdata in raw["providers"].items():
                cfg.providers[pid] = ProviderConfig(url=pdata["url"])

    if v := os.getenv("AI_ROUTER_PROFILE_DIR"):
        cfg.profile_dir = _expand(v)
    if v := os.getenv("AI_ROUTER_DEFAULT_PROVIDER"):
        cfg.default_provider = v
    if v := os.getenv("AI_ROUTER_HOST"):
        cfg.host = v
    if v := os.getenv("AI_ROUTER_PORT"):
        cfg.port = int(v)
    if v := os.getenv("AI_ROUTER_ANSWER_TIMEOUT_S"):
        cfg.answer_timeout_s = int(v)

    return cfg
```

- [ ] **Step 5: Run tests — expect PASS**

```bash
pytest tests/test_config.py -v
```

- [ ] **Step 6: Commit**

```bash
git add src/ai_router/errors.py src/ai_router/config.py tests/test_config.py
git commit -m "feat: add config loader and error types"
```

---

### Task 3: Adapter base, registry, ChatGPT stub

**Files:**
- Create: `src/ai_router/adapters/base.py`
- Create: `src/ai_router/adapters/registry.py`
- Create: `src/ai_router/adapters/chatgpt/adapter.py`
- Create: `src/ai_router/adapters/gemini/__init__.py`
- Create: `src/ai_router/adapters/chatgpt/__init__.py`

- [ ] **Step 1: Create adapter base**

```python
# src/ai_router/adapters/base.py
from __future__ import annotations

from enum import Enum
from typing import Literal, Protocol

from playwright.async_api import Page


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
    async def ask(self, page: Page, prompt: str, *, timeout_s: int) -> str: ...
```

- [ ] **Step 2: Create ChatGPT stub**

```python
# src/ai_router/adapters/chatgpt/adapter.py
from __future__ import annotations

from playwright.async_api import Page

from ai_router.adapters.base import SessionStatus
from ai_router.errors import ProviderNotReadyError


class ChatGPTAdapter:
    id = "chatgpt"
    name = "ChatGPT"
    keywords: list[str] = ["chatgpt", "gpt", "@chatgpt"]
    status = "coming_soon"

    async def check_session(self, page: Page) -> SessionStatus:
        return SessionStatus.UNKNOWN

    async def open_new_chat(self, page: Page) -> None:
        raise ProviderNotReadyError(self.id)

    async def ask(self, page: Page, prompt: str, *, timeout_s: int) -> str:
        raise ProviderNotReadyError(self.id)
```

- [ ] **Step 3: Create registry (gemini import deferred to Task 6)**

```python
# src/ai_router/adapters/registry.py
from __future__ import annotations

from ai_router.adapters.base import ProviderAdapter
from ai_router.adapters.chatgpt.adapter import ChatGPTAdapter
from ai_router.errors import AiRouterError


class ProviderRegistry:
    def __init__(self, adapters: list[ProviderAdapter] | None = None) -> None:
        self._adapters: dict[str, ProviderAdapter] = {}
        for adapter in adapters or []:
            self.register(adapter)

    def register(self, adapter: ProviderAdapter) -> None:
        self._adapters[adapter.id] = adapter

    def get(self, provider_id: str) -> ProviderAdapter:
        adapter = self._adapters.get(provider_id)
        if not adapter:
            raise AiRouterError("UNKNOWN_PROVIDER", f"Unknown provider: {provider_id}")
        return adapter

    def list_all(self) -> list[ProviderAdapter]:
        return list(self._adapters.values())


def build_registry() -> ProviderRegistry:
    from ai_router.adapters.gemini.adapter import GeminiAdapter

    registry = ProviderRegistry()
    registry.register(GeminiAdapter())
    registry.register(ChatGPTAdapter())
    return registry
```

Note: `build_registry` imports Gemini lazily so Tasks 4–5 can run before Task 6 if needed. For Task 3 commit, create a minimal `GeminiAdapter` placeholder:

```python
# src/ai_router/adapters/gemini/adapter.py  (placeholder until Task 6)
class GeminiAdapter:
    id = "gemini"
    name = "Gemini"
    keywords = ["gemini", "@gemini"]
    status = "available"
    async def check_session(self, page): ...
    async def open_new_chat(self, page): ...
    async def ask(self, page, prompt, *, timeout_s): return ""
```

- [ ] **Step 4: Commit**

```bash
git add src/ai_router/adapters/
git commit -m "feat: add provider adapter protocol, registry, and ChatGPT stub"
```

---

### Task 4: Router

**Files:**
- Create: `src/ai_router/router/resolve.py`
- Create: `tests/test_router.py`

- [ ] **Step 1: Write failing router tests**

```python
# tests/test_router.py
import pytest

from ai_router.adapters.registry import ProviderRegistry
from ai_router.adapters.chatgpt.adapter import ChatGPTAdapter
from ai_router.errors import AiRouterError
from ai_router.router.resolve import resolve_provider


class _FakeGemini:
    id = "gemini"
    name = "Gemini"
    keywords: list[str] = []
    status = "available"


def test_resolve_default_provider():
    registry = ProviderRegistry([_FakeGemini(), ChatGPTAdapter()])
    adapter, reason = resolve_provider(registry, None, default="gemini")
    assert adapter.id == "gemini"
    assert reason == "default provider"


def test_resolve_explicit_provider():
    registry = ProviderRegistry([_FakeGemini(), ChatGPTAdapter()])
    adapter, reason = resolve_provider(registry, "gemini", default="gemini")
    assert adapter.id == "gemini"
    assert reason == "explicit param"


def test_resolve_unknown_provider():
    registry = ProviderRegistry([_FakeGemini()])
    with pytest.raises(AiRouterError) as exc:
        resolve_provider(registry, "unknown", default="gemini")
    assert exc.value.code == "UNKNOWN_PROVIDER"
```

- [ ] **Step 2: Run — expect FAIL**

```bash
pytest tests/test_router.py -v
```

- [ ] **Step 3: Implement router**

```python
# src/ai_router/router/resolve.py
from __future__ import annotations

from ai_router.adapters.base import ProviderAdapter
from ai_router.adapters.registry import ProviderRegistry


def resolve_provider(
    registry: ProviderRegistry,
    provider: str | None,
    *,
    default: str,
) -> tuple[ProviderAdapter, str]:
    if provider:
        return registry.get(provider), "explicit param"
    return registry.get(default), "default provider"
```

- [ ] **Step 4: Run — expect PASS**

```bash
pytest tests/test_router.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/ai_router/router/resolve.py tests/test_router.py
git commit -m "feat: add provider router"
```

---

### Task 5: Gemini wait helpers (unit-tested)

**Files:**
- Create: `src/ai_router/adapters/gemini/selectors.py`
- Create: `src/ai_router/adapters/gemini/wait.py`
- Create: `tests/test_gemini_wait.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_gemini_wait.py
from ai_router.adapters.gemini.wait import braces_balanced, is_rate_limited


def test_braces_balanced_true():
    assert braces_balanced('{"a": 1}') is True


def test_braces_balanced_false():
    assert braces_balanced('{"a": 1') is False


def test_braces_balanced_no_braces():
    assert braces_balanced("hello world") is True


def test_rate_limit_english():
    assert is_rate_limited("Too many requests, try again later") is True


def test_rate_limit_vietnamese():
    assert is_rate_limited("Bạn đã đạt đến giới hạn, thử lại sau") is True


def test_rate_limit_negative():
    assert is_rate_limited("Here is a normal answer about Python") is False
```

- [ ] **Step 2: Run — expect FAIL**

```bash
pytest tests/test_gemini_wait.py -v
```

- [ ] **Step 3: Implement selectors**

```python
# src/ai_router/adapters/gemini/selectors.py
import re

GEMINI_URL = "https://gemini.google.com/app"

SEL_PROMPT_INPUT = (
    'div.ql-editor[contenteditable="true"], '
    'rich-textarea div[contenteditable="true"]'
)
SEL_RESPONSE_BLOCK = "model-response, .model-response-text, message-content"
SEL_GENERATING = 'button[aria-label*="Stop"], button[aria-label*="Dừng"]'
SEL_SIGN_IN = (
    'a[href*="accounts.google.com/ServiceLogin"], '
    'a[href*="accounts.google.com/signin"]'
)

STREAM_GENERATE_RE = re.compile(
    r"assistant\.lamda\.BardFrontendService/StreamGenerate", re.I
)

RATE_LIMIT_MARKERS = (
    "too many requests",
    "try again later",
    "you've reached your limit",
    "quá nhiều yêu cầu",
    "đã đạt đến giới hạn",
    "thử lại sau",
)
```

- [ ] **Step 4: Implement wait helpers**

```python
# src/ai_router/adapters/gemini/wait.py
from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from ai_router.adapters.gemini.selectors import (
    RATE_LIMIT_MARKERS,
    SEL_GENERATING,
    SEL_RESPONSE_BLOCK,
    STREAM_GENERATE_RE,
)
from ai_router.errors import RateLimitedError, TimeoutError_

if TYPE_CHECKING:
    from playwright.async_api import Page


def braces_balanced(text: str) -> bool:
    if "{" not in text:
        return True
    return text.count("{") == text.count("}")


def is_rate_limited(text: str) -> bool:
    lower = text.lower()
    return any(marker in lower for marker in RATE_LIMIT_MARKERS)


async def wait_for_stream(page: Page, timeout_s: float) -> bool:
    loop = asyncio.get_running_loop()
    done = loop.create_future()

    def on_finished(request) -> None:
        if not done.done() and STREAM_GENERATE_RE.search(request.url):
            done.set_result(True)

    page.on("requestfinished", on_finished)
    try:
        await asyncio.wait_for(done, timeout=timeout_s)
        return True
    except asyncio.TimeoutError:
        return False
    finally:
        page.remove_listener("requestfinished", on_finished)


async def wait_for_answer_dom(
    page: Page,
    *,
    before_count: int,
    timeout_s: float,
    poll_interval_s: float = 0.5,
    stable_polls: int = 4,
) -> str:
    deadline = time.monotonic() + timeout_s
    last_text = ""
    stable_streak = 0

    while time.monotonic() < deadline:
        blocks = page.locator(SEL_RESPONSE_BLOCK)
        count = await blocks.count()
        generating = await page.locator(SEL_GENERATING).count()

        if count > before_count and generating == 0:
            text = (await blocks.nth(count - 1).inner_text()).strip()
            if text and braces_balanced(text):
                if text == last_text:
                    stable_streak += 1
                    if stable_streak >= stable_polls:
                        if is_rate_limited(text):
                            raise RateLimitedError(text[:200])
                        return text
                else:
                    last_text = text
                    stable_streak = 1

        await asyncio.sleep(poll_interval_s)

    raise TimeoutError_("DOM polling timed out waiting for stable answer")
```

- [ ] **Step 5: Run — expect PASS**

```bash
pytest tests/test_gemini_wait.py -v
```

- [ ] **Step 6: Commit**

```bash
git add src/ai_router/adapters/gemini/selectors.py src/ai_router/adapters/gemini/wait.py tests/test_gemini_wait.py
git commit -m "feat: add Gemini selectors and wait helpers with unit tests"
```

---

### Task 6: Gemini adapter

**Files:**
- Modify: `src/ai_router/adapters/gemini/adapter.py` (replace placeholder)

- [ ] **Step 1: Implement full GeminiAdapter**

```python
# src/ai_router/adapters/gemini/adapter.py
from __future__ import annotations

import asyncio

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from ai_router.adapters.base import SessionStatus
from ai_router.adapters.gemini.selectors import (
    GEMINI_URL,
    SEL_PROMPT_INPUT,
    SEL_RESPONSE_BLOCK,
    SEL_SIGN_IN,
)
from ai_router.adapters.gemini.wait import (
    is_rate_limited,
    wait_for_answer_dom,
    wait_for_stream,
)
from ai_router.errors import AiRouterError, NotLoggedInError, RateLimitedError


class GeminiAdapter:
    id = "gemini"
    name = "Gemini"
    keywords = ["gemini", "@gemini", "google gemini"]
    status = "available"

    async def check_session(self, page: Page) -> SessionStatus:
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

    async def ask(self, page: Page, prompt: str, *, timeout_s: int) -> str:
        last_err: Exception | None = None
        for attempt in range(2):
            try:
                return await self._ask_once(page, prompt, timeout_s=timeout_s)
            except RuntimeError as exc:
                # humanized click race
                last_err = exc
                if attempt == 0:
                    await asyncio.sleep(2)
                    await page.reload(wait_until="domcontentloaded")
                    continue
                raise AiRouterError("ADAPTER_ERROR", str(exc)) from exc
        raise AiRouterError("ADAPTER_ERROR", str(last_err or "unknown"))

    async def _ask_once(self, page: Page, prompt: str, *, timeout_s: int) -> str:
        box = page.locator(SEL_PROMPT_INPUT).first
        await box.wait_for(state="visible", timeout=15_000)
        await box.click()
        await page.keyboard.insert_text(prompt)

        before_count = await page.locator(SEL_RESPONSE_BLOCK).count()

        # Attach stream listener BEFORE Enter
        stream_task = asyncio.create_task(wait_for_stream(page, timeout_s))

        await page.keyboard.press("Enter")
        stream_ok = await stream_task

        if not stream_ok:
            pass  # fall through to DOM polling with full timeout

        answer = await wait_for_answer_dom(
            page,
            before_count=before_count,
            timeout_s=timeout_s,
        )

        if is_rate_limited(answer):
            raise RateLimitedError(answer[:200])

        return answer
```

- [ ] **Step 2: Verify imports**

```bash
python -c "from ai_router.adapters.registry import build_registry; print([a.id for a in build_registry().list_all()])"
```

Expected: `['gemini', 'chatgpt']`

- [ ] **Step 3: Commit**

```bash
git add src/ai_router/adapters/gemini/adapter.py
git commit -m "feat: implement Gemini adapter with network and DOM wait"
```

---

### Task 7: BrowserManager

**Files:**
- Create: `src/ai_router/browser/manager.py`

- [ ] **Step 1: Implement BrowserManager**

```python
# src/ai_router/browser/manager.py
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

from cloakbrowser import launch_persistent_context_async
from playwright.async_api import BrowserContext, Page

from ai_router.config import AppConfig
from ai_router.errors import BrowserBusyError


class BrowserManager:
    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._ctx: BrowserContext | None = None
        self._lock = asyncio.Lock()
        self._busy = False

    async def ensure_context(self) -> BrowserContext:
        if self._ctx is None:
            self._config.profile_dir.mkdir(parents=True, exist_ok=True)
            self._ctx = await launch_persistent_context_async(
                str(self._config.profile_dir),
                headless=False,
                humanize=True,
            )
        return self._ctx

    async def new_page(self) -> Page:
        ctx = await self.ensure_context()
        if ctx.pages:
            return ctx.pages[0]
        return await ctx.new_page()

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[BrowserContext]:
        async with self._lock:
            if self._busy:
                raise BrowserBusyError()
            self._busy = True
            try:
                yield await self.ensure_context()
            finally:
                self._busy = False

    async def close(self) -> None:
        if self._ctx:
            await self._ctx.close()
            self._ctx = None
```

- [ ] **Step 2: Smoke import**

```bash
python -c "from ai_router.browser.manager import BrowserManager; from ai_router.config import load_config; print(BrowserManager(load_config()))"
```

- [ ] **Step 3: Commit**

```bash
git add src/ai_router/browser/manager.py
git commit -m "feat: add BrowserManager with CloakBrowser and mutex"
```

---

### Task 8: SessionManager

**Files:**
- Create: `src/ai_router/session/manager.py`

- [ ] **Step 1: Implement SessionManager**

```python
# src/ai_router/session/manager.py
from __future__ import annotations

import time
from dataclasses import dataclass, field

from playwright.async_api import BrowserContext, Page

from ai_router.adapters.base import ProviderAdapter
from ai_router.browser.manager import BrowserManager


@dataclass
class ChatSession:
    mcp_session_id: str
    page: Page
    provider_id: str
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    message_count: int = 0


class SessionManager:
    def __init__(self, browser: BrowserManager) -> None:
        self._browser = browser
        self._sessions: dict[str, ChatSession] = {}

    async def get_or_create(
        self,
        mcp_session_id: str,
        adapter: ProviderAdapter,
        ctx: BrowserContext,
    ) -> ChatSession:
        existing = self._sessions.get(mcp_session_id)
        if existing:
            existing.last_activity = time.time()
            return existing

        page = await ctx.new_page()
        await adapter.open_new_chat(page)
        session = ChatSession(
            mcp_session_id=mcp_session_id,
            page=page,
            provider_id=adapter.id,
        )
        self._sessions[mcp_session_id] = session
        return session

    def record_message(self, mcp_session_id: str) -> None:
        session = self._sessions.get(mcp_session_id)
        if session:
            session.message_count += 1
            session.last_activity = time.time()
```

- [ ] **Step 2: Commit**

```bash
git add src/ai_router/session/manager.py
git commit -m "feat: add SessionManager for MCP session to Page mapping"
```

---

### Task 9: MCP tool handlers

**Files:**
- Create: `src/ai_router/mcp/tools.py`

- [ ] **Step 1: Implement tool handlers**

```python
# src/ai_router/mcp/tools.py
from __future__ import annotations

from dataclasses import dataclass

from ai_router.adapters.base import SessionStatus
from ai_router.adapters.registry import ProviderRegistry, build_registry
from ai_router.browser.manager import BrowserManager
from ai_router.config import AppConfig, load_config
from ai_router.errors import AiRouterError, NotLoggedInError, ProviderNotReadyError
from ai_router.router.resolve import resolve_provider
from ai_router.session.manager import SessionManager


@dataclass
class AppState:
    config: AppConfig
    registry: ProviderRegistry
    browser: BrowserManager
    sessions: SessionManager


def create_app_state(config: AppConfig | None = None) -> AppState:
    cfg = config or load_config()
    browser = BrowserManager(cfg)
    return AppState(
        config=cfg,
        registry=build_registry(),
        browser=browser,
        sessions=SessionManager(browser),
    )


async def handle_ask(
    state: AppState,
    *,
    prompt: str,
    provider: str | None,
    mcp_session_id: str | None,
) -> dict:
    if not mcp_session_id:
        raise AiRouterError("MISSING_SESSION", "Mcp-Session-Id header required")

    adapter, routing_reason = resolve_provider(
        state.registry, provider, default=state.config.default_provider
    )
    if adapter.status == "coming_soon":
        raise ProviderNotReadyError(adapter.id)

    async with state.browser.acquire() as ctx:
        session = await state.sessions.get_or_create(mcp_session_id, adapter, ctx)
        status = await adapter.check_session(session.page)
        if status == SessionStatus.LOGGED_OUT:
            raise NotLoggedInError()
        answer = await adapter.ask(
            session.page,
            prompt,
            timeout_s=state.config.answer_timeout_s,
        )
        state.sessions.record_message(mcp_session_id)

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
    async with state.browser.acquire() as ctx:
        page = await ctx.new_page() if not ctx.pages else ctx.pages[0]
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
```

- [ ] **Step 2: Commit**

```bash
git add src/ai_router/mcp/tools.py
git commit -m "feat: add MCP tool handlers for ask, list_providers, session_status"
```

---

### Task 10: MCP HTTP server

**Files:**
- Create: `src/ai_router/mcp/server.py`

- [ ] **Step 1: Implement MCP server with FastMCP**

```python
# src/ai_router/mcp/server.py
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from ai_router.config import load_config
from ai_router.errors import AiRouterError
from ai_router.mcp.tools import create_app_state, handle_ask, handle_list_providers, handle_session_status

mcp = FastMCP("ai-router")
_state = create_app_state()


def _mcp_session_id(ctx) -> str | None:
    # FastMCP passes request context; read Mcp-Session-Id from meta if available
    meta = getattr(ctx, "request_context", None)
    if meta and hasattr(meta, "request"):
        return meta.request.headers.get("mcp-session-id")
    return None


@mcp.tool()
async def ask(prompt: str, provider: str | None = None) -> dict:
    """Send a prompt to a web AI provider and return the raw text answer."""
    try:
        return await handle_ask(
            _state,
            prompt=prompt,
            provider=provider,
            mcp_session_id=_mcp_session_id(mcp.get_context()),
        )
    except AiRouterError as exc:
        raise RuntimeError(f"[{exc.code}] {exc.message}") from exc


@mcp.tool()
async def list_providers() -> dict:
    """List registered AI providers and their availability status."""
    return await handle_list_providers(_state)


@mcp.tool()
async def session_status(provider: str | None = None) -> dict:
    """Check whether providers have an active logged-in browser session."""
    try:
        return await handle_session_status(_state, provider=provider)
    except AiRouterError as exc:
        raise RuntimeError(f"[{exc.code}] {exc.message}") from exc


def run_server(host: str | None = None, port: int | None = None) -> None:
    cfg = load_config()
    mcp.run(
        transport="streamable-http",
        host=host or cfg.host,
        port=port or cfg.port,
    )
```

**Note for implementer:** If `FastMCP.get_context()` API differs in installed `mcp` version, read the installed SDK docs and wire `Mcp-Session-Id` from the Starlette request in the streamable-http handler. The session id MUST be extracted from the HTTP header — do not add a `session_id` tool param.

- [ ] **Step 2: Smoke test server starts**

```bash
ai serve &
sleep 3
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8087/mcp
```

Expected: HTTP response (not connection refused). Kill background process after.

- [ ] **Step 3: Commit**

```bash
git add src/ai_router/mcp/server.py
git commit -m "feat: add MCP HTTP server with ask, list_providers, session_status"
```

---

### Task 11: Typer CLI

**Files:**
- Create: `src/ai_router/cli/main.py`
- Create: `src/ai_router/cli/serve.py`
- Create: `src/ai_router/cli/browser.py`

- [ ] **Step 1: Create CLI root**

```python
# src/ai_router/cli/main.py
import typer

from ai_router.cli.browser import browser_app
from ai_router.cli.serve import serve_cmd

app = typer.Typer(name="ai", help="ai-router — web AI provider automation")
app.command("serve")(serve_cmd)
app.add_typer(browser_app, name="browser")

if __name__ == "__main__":
    app()
```

- [ ] **Step 2: Create serve command**

```python
# src/ai_router/cli/serve.py
from __future__ import annotations

import typer

from ai_router.config import load_config
from ai_router.mcp.server import run_server

def serve_cmd(
    host: str | None = typer.Option(None, help="Bind host"),
    port: int | None = typer.Option(None, help="Bind port"),
) -> None:
    """Start the MCP HTTP/SSE server."""
    cfg = load_config()
    typer.echo(f"Starting ai-router on {host or cfg.host}:{port or cfg.port}")
    run_server(host=host, port=port)
```

- [ ] **Step 3: Create browser commands**

```python
# src/ai_router/cli/browser.py
from __future__ import annotations

import asyncio

import typer
from cloakbrowser import launch_persistent_context_async
from playwright.async_api import Error as PlaywrightError

from ai_router.adapters.registry import build_registry
from ai_router.browser.manager import BrowserManager
from ai_router.config import load_config

browser_app = typer.Typer(help="Browser login and session management")


async def _login(provider: str | None) -> None:
    cfg = load_config()
    cfg.profile_dir.mkdir(parents=True, exist_ok=True)
    registry = build_registry()
    targets = (
        [registry.get(provider)]
        if provider
        else [a for a in registry.list_all() if a.status == "available"]
    )

    ctx = await launch_persistent_context_async(
        str(cfg.profile_dir),
        headless=False,
        humanize=True,
    )
    try:
        for adapter in targets:
            page = await ctx.new_page()
            url = cfg.providers[adapter.id].url
            typer.echo(f"Opening {adapter.name}: {url}")
            await page.goto(url, wait_until="domcontentloaded")
        typer.echo("Log in to each provider, then close all browser windows...")
        while ctx.pages:
            await asyncio.sleep(0.5)
    finally:
        try:
            await ctx.close()
        except PlaywrightError:
            pass


@browser_app.command("login")
def login(
    provider: str | None = typer.Option(None, help="Provider id (default: all available)"),
) -> None:
    """Open headed browser for manual login. Close window when done."""
    asyncio.run(_login(provider))


@browser_app.command("status")
def status(
    provider: str | None = typer.Option(None, help="Provider id (default: all)"),
) -> None:
    """Check login status for provider(s)."""
    from ai_router.mcp.tools import create_app_state, handle_session_status

    async def _run() -> dict:
        state = create_app_state()
        return await handle_session_status(state, provider=provider)

    result = asyncio.run(_run())
    for pid, st in result.items():
        typer.echo(f"{pid}: {st}")
```

- [ ] **Step 4: Verify CLI help**

```bash
ai --help
ai browser --help
```

Expected: shows `serve`, `browser login`, `browser status`.

- [ ] **Step 5: Commit**

```bash
git add src/ai_router/cli/
git commit -m "feat: add Typer CLI with ai serve and ai browser commands"
```

---

### Task 12: README and manual verification

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Write README**

Include:
- Prerequisites: Python 3.11+, Chrome, Node.js (for mcp-remote)
- Install: `pip install -e ".[dev]"`
- Login: `ai browser login`
- Serve: `ai serve`
- Cursor `mcp.json` config pointing to `http://127.0.0.1:8087/mcp`
- Security note: profile dir contains live sessions
- Manual test checklist from spec §14

- [ ] **Step 2: Run all unit tests**

```bash
pytest -v
```

Expected: all PASS

- [ ] **Step 3: Manual smoke (document results in commit message or PR)**

1. `ai browser login` → login Gemini → close window
2. `ai serve` → connect Cursor via mcp-remote
3. `ask` twice same tab → follow-up context preserved
4. New Cursor tab → `ask` → new chat
5. `session_status` → `logged_in`
6. `list_providers` → gemini available, chatgpt coming_soon

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: add README with setup and manual test checklist"
```

---

## Spec Coverage Check

| Spec section | Task |
|--------------|------|
| MCP tools: ask, list_providers, session_status | Task 9, 10 |
| CLI: ai serve, ai browser login/status | Task 11 |
| No MCP login tool | Task 9 (login CLI only) |
| Mcp-Session-Id auto mapping | Task 8, 9, 10 |
| Headed + humanize always | Task 7, 11 |
| Gemini network + DOM wait | Task 5, 6 |
| ChatGPT stub coming_soon | Task 3 |
| Config YAML + env | Task 2 |
| Error codes | Task 2, 6, 9 |
| Raw text answer only | Task 9 |
| Router default + explicit provider | Task 4 |

## Out of Scope (confirmed not in plan)

- JSON parse/repair, batch pipeline, keyword routing, NotebookLM, headless mode, multi-account, session TTL

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-09-ai-router-python.md`. Two execution options:

**1. Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
