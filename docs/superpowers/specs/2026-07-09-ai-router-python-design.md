# ai-router Python MCP Design

**Date:** 2026-07-09  
**Status:** Draft — pending user review  
**Branch:** `python`  
**Project:** ai-router — Python rewrite: MCP server routing prompts to web AI providers via CloakBrowser

---

## 1. Summary

ai-router is a local MCP server (Python) that lets Cursor Agent send prompts to web-based AI providers using the user's existing browser sessions. Sessions are established once via `ai browser login` (headed manual login), persisted with CloakBrowser, and reused for subsequent `ask` calls over HTTP/SSE.

**v1 ships Gemini fully.** ChatGPT is registered in `list_providers` as `coming_soon`. Adapter interface is designed for extension without rewriting core.

**Key decisions:**

| Decision | Choice |
|----------|--------|
| Stack | Python 3.11+, Typer CLI, FastAPI/uvicorn, `mcp` SDK, `cloakbrowser` |
| Interface | MCP tools: `ask`, `list_providers`, `session_status` |
| Login | CLI only: `ai browser login` (not an MCP tool) |
| Transport | HTTP/SSE server + `mcp-remote` bridge |
| Browser | CloakBrowser `launch_persistent_context_async`, always `headless=False`, `humanize=True` |
| Conversation | Same MCP session = same provider chat; new Cursor tab = new MCP session = new chat |
| Session mapping | Automatic from `Mcp-Session-Id` header (no `session_id` param on `ask`) |
| Ask output | Raw text only (no JSON parse/repair in v1) |
| Providers v1 | Gemini implemented; ChatGPT stub |

---

## 2. Architecture

```
┌─────────────┐  mcp-remote   ┌──────────────────────────────────────┐
│ Cursor Agent│◄────────────►│  ai serve (Typer → uvicorn/FastAPI)  │
└─────────────┘  HTTP/SSE     │  MCP: ask | list_providers |         │
                              │       session_status                 │
                              └──────────────┬───────────────────────┘
                                             │
                    ┌────────────────────────┼────────────────────────┐
                    ▼                        ▼                        ▼
             ToolHandlers            SessionManager            ProviderRegistry
          (thin, validate)     MCP session → Page/Chat      gemini | chatgpt*
                    │                        │
                    └────────────┬───────────┘
                                 ▼
                          BrowserManager
                    CloakBrowser persistent ctx
                    headed=True, humanize=True
                    mutex: one ask at a time
                                 │
                                 ▼
                          ProviderAdapter (Protocol)
                          ┌─────────┬──────────┐
                          │ Gemini  │ ChatGPT* │  *stub v1
                          └─────────┴──────────┘
```

### 2.1 Monolith (recommended)

Single Python process: MCP HTTP/SSE server and browser share `BrowserManager`. CLI commands (`ai browser *`) call the same core modules — no duplicated logic.

**Rejected alternatives:**

- **CLI-as-HTTP-client:** unnecessary for local tool; complicates headed login
- **Process-per-MCP-session:** multiple Chrome windows, slow, contradicts "one browser context" model

### 2.2 Session storage

- Profile path: `~/.ai-router/profile/` (CloakBrowser `userDataDir`)
- Config: `~/.ai-router/config.yaml`
- All provider cookies/localStorage in one Chrome profile (same as TS design)

### 2.3 BrowserManager

- Wraps `launch_persistent_context_async` from `cloakbrowser`
- Always `headless=False`, `humanize=True`
- Mutex: reject concurrent `ask` with `BROWSER_BUSY`
- Browser launched once when first `ask` or `session_status` needs it; kept alive for server lifetime
- Use `asyncio.sleep()` — never `page.wait_for_timeout()` (CDP traffic, reCAPTCHA signal)
- Avoid `page.evaluate` / JS injection unless unavoidable

---

## 3. Session mapping & conversation model

### 3.1 MCP session → provider chat

```
MCP initialize → server returns Mcp-Session-Id
       │
       ▼
ask (first call with session id X)
  → SessionManager.get_or_create("mcp:X")
  → not found? ctx.new_page() + adapter.open_new_chat(page)
  → store: mcp_session_id → ChatSession (page, provider, last_activity)
       │
       ▼
ask (subsequent calls, same session id X)
  → reuse same Page/tab
  → adapter.ask() continues in current chat (no goto /app)
       │
       ▼
New Cursor tab → new Mcp-Session-Id Y
  → new Page/tab + new provider chat
```

### 3.2 ChatSession

```python
@dataclass
class ChatSession:
    mcp_session_id: str
    page: Page
    provider_id: str
    created_at: float
    last_activity: float
    message_count: int
```

v1: in-memory map only. Sessions live until server restart. Future: TTL cleanup for idle sessions.

### 3.3 Mutex scope

One `ask` at a time across all MCP sessions (single CloakBrowser context). Concurrent asks from different Cursor tabs queue or return `BROWSER_BUSY`.

---

## 4. Provider adapter interface

```python
class SessionStatus(str, Enum):
    LOGGED_IN = "logged_in"
    LOGGED_OUT = "logged_out"
    UNKNOWN = "unknown"

class ProviderAdapter(Protocol):
    id: str
    name: str
    keywords: list[str]
    status: Literal["available", "coming_soon"]

    async def check_session(self, page: Page) -> SessionStatus
    async def open_new_chat(self, page: Page) -> None
    async def ask(self, page: Page, prompt: str, *, timeout_s: int) -> str
```

**Responsibility split:**

| Layer | Owns |
|-------|------|
| SessionManager | MCP session → Page mapping, calls `open_new_chat` on new session |
| Adapter | Provider-specific DOM, network signals, selectors |
| Router | Resolve `provider` param → adapter instance |

### 4.1 Router

- Optional `provider` on `ask` and `session_status`
- Default: `config.default_provider` (`gemini`)
- v1: keyword routing deferred (YAGNI); explicit param + default only

### 4.2 ChatGPT stub (v1)

- Registered in `ProviderRegistry`
- `list_providers` returns `status: "coming_soon"`
- `ask` with `provider=chatgpt` → `PROVIDER_NOT_READY` error

---

## 5. Gemini adapter (from battle-tested spec)

Port proven mechanics from Gemini Web automation spec (Jul 2026). **Not ported to MCP v1:** JSON parse/repair, schema validation, batch delays, transient/reject record classification.

### 5.1 Selectors (single constants block)

```python
SEL_PROMPT_INPUT   = 'div.ql-editor[contenteditable="true"], rich-textarea div[contenteditable="true"]'
SEL_RESPONSE_BLOCK = "model-response, .model-response-text, message-content"
SEL_GENERATING     = 'button[aria-label*="Stop"], button[aria-label*="Dừng"]'
SEL_SIGN_IN        = 'a[href*="accounts.google.com/ServiceLogin"], a[href*="accounts.google.com/signin"]'

STREAM_GENERATE_RE = re.compile(
    r"assistant\.lamda\.BardFrontendService/StreamGenerate", re.I
)

RATE_LIMIT_MARKERS = (
    "too many requests", "try again later", "you've reached your limit",
    "quá nhiều yêu cầu", "đã đạt đến giới hạn", "thử lại sau",
)
```

### 5.2 Login check

```python
await page.goto(GEMINI_URL, wait_until="domcontentloaded")
try:
    await page.wait_for_selector(SEL_PROMPT_INPUT, timeout=15_000)
except TimeoutError:
    if await page.locator(SEL_SIGN_IN).count() > 0:
        raise NotLoggedInError("run: ai browser login")
    raise NotLoggedInError("prompt input not found")
```

### 5.3 open_new_chat

`goto https://gemini.google.com/app` — equivalent to "New chat". Called only when SessionManager creates a new MCP session (new Page), not on follow-up asks.

### 5.4 Sending prompt

```python
box = page.locator(SEL_PROMPT_INPUT).first
await box.wait_for(state="visible", timeout=15_000)
await box.click()
await page.keyboard.insert_text(prompt)   # NOT humanized char-by-char
await page.keyboard.press("Enter")
```

Wrap goto → wait → click → insert_text in retry-once (sleep 2s + reload) for humanized click race (`RuntimeError: Element not found while scrolling into view`). Browser closed/crash: no retry, propagate.

### 5.5 Wait for answer — two layers

**Primary: network signal**

Listen `requestfinished` for `StreamGenerate` endpoint **before** pressing Enter:

```python
stream_done = loop.create_future()

def on_request_finished(request):
    if not stream_done.done() and STREAM_GENERATE_RE.search(request.url):
        stream_done.set_result(True)

page.on("requestfinished", on_request_finished)
try:
    await page.keyboard.press("Enter")
    await asyncio.wait_for(stream_done, timeout=answer_timeout_s)
except asyncio.TimeoutError:
    pass  # fall through to DOM layer
finally:
    page.remove_listener("requestfinished", on_request_finished)
```

Do NOT use `expect_response` + `response.finished()` — causes `Target closed` task exceptions on browser shutdown.

**Fallback: DOM polling**

Poll every 0.5s. Accept when ALL true:

1. Response block count increased since before submit
2. `SEL_GENERATING` count == 0
3. Text stable for 4 consecutive polls (2 seconds)
4. Balanced braces: if text contains `{`, then `count("{") == count("}")`

Total timeout: `answer_timeout_s` (default 120).

### 5.6 Read answer

```python
blocks = page.locator(SEL_RESPONSE_BLOCK)
answer = (await blocks.nth(await blocks.count() - 1).inner_text()).strip()
```

Network = timing signal; DOM = content source.

### 5.7 Rate limit

After answer, scan for `RATE_LIMIT_MARKERS` → raise `RATE_LIMITED`.

---

## 6. MCP tools

### 6.1 `ask`

**Input:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `prompt` | string | yes | Question/prompt to send |
| `provider` | string | no | `gemini` or `chatgpt`; default from config |

**Output:**

```json
{
  "answer": "raw text from provider",
  "provider": "gemini",
  "routing_reason": "explicit param" | "default provider"
}
```

Uses `Mcp-Session-Id` from request header automatically. Agent does not pass session id.

### 6.2 `list_providers`

**Output:**

```json
{
  "providers": [
    { "id": "gemini", "name": "Gemini", "status": "available" },
    { "id": "chatgpt", "name": "ChatGPT", "status": "coming_soon" }
  ]
}
```

### 6.3 `session_status`

**Input:**

| Field | Type | Required |
|-------|------|----------|
| `provider` | string | no — omit for all |

**Output:**

```json
{
  "gemini": "logged_in",
  "chatgpt": "unknown"
}
```

---

## 7. CLI (Typer)

Root app: `ai`

```text
ai serve [--port 8087] [--host 127.0.0.1]
ai browser login [--provider gemini]
ai browser status [--provider gemini]
```

| Command | Behavior |
|---------|----------|
| `ai serve` | Start HTTP/SSE MCP server; browser ready on first ask |
| `ai browser login` | Headed browser, navigate provider URLs, wait for user to close window, save profile |
| `ai browser status` | Check login state per provider (CLI, shares BrowserManager logic) |

**Login flow (`ai browser login`):**

```python
ctx = await launch_persistent_context_async(profile_dir, headless=False, humanize=True)
page = ctx.pages[0] if ctx.pages else await ctx.new_page()
await page.goto("https://gemini.google.com/app")
print("Log in, then close the browser window...")
while ctx.pages:
    await asyncio.sleep(0.5)
await ctx.close()
```

Default: open all configured provider URLs (tabs) unless `--provider` specified.

**Entry point (`pyproject.toml`):**

```toml
[project.scripts]
ai = "ai_router.cli.main:app"
```

---

## 8. MCP transport & Cursor config

| Setting | Value |
|---------|-------|
| Start | `ai serve` |
| Bind | `127.0.0.1` only (never `0.0.0.0`) |
| Default port | `8087` |
| Override | `--port`, `--host`, env `AI_ROUTER_PORT`, `AI_ROUTER_HOST` |

**Cursor `mcp.json`:**

```json
{
  "mcpServers": {
    "ai-router": {
      "command": "npx",
      "args": ["-y", "mcp-remote@latest", "http://127.0.0.1:8087/mcp"]
    }
  }
}
```

Server must be running before Cursor connects.

---

## 9. Configuration

**Path:** `~/.ai-router/config.yaml`

| Key | Default | Description |
|-----|---------|-------------|
| `profile_dir` | `~/.ai-router/profile` | CloakBrowser persistent profile |
| `default_provider` | `gemini` | Provider when `ask` omits `provider` |
| `host` | `127.0.0.1` | MCP server bind address |
| `port` | `8087` | MCP server port |
| `answer_timeout_s` | `120` | Per-ask answer timeout |
| `providers.gemini.url` | `https://gemini.google.com/app` | Gemini chat URL |

Env overrides: `AI_ROUTER_PROFILE_DIR`, `AI_ROUTER_DEFAULT_PROVIDER`, `AI_ROUTER_PORT`, `AI_ROUTER_HOST`, `AI_ROUTER_ANSWER_TIMEOUT_S`.

---

## 10. Error handling

| Code | When | User action |
|------|------|-------------|
| `NOT_LOGGED_IN` | Prompt input missing + sign-in visible | Run `ai browser login` |
| `PROVIDER_NOT_READY` | ChatGPT ask in v1 | Wait for implementation |
| `BROWSER_BUSY` | Concurrent ask while mutex held | Retry |
| `TIMEOUT` | No stable answer within timeout | Retry or shorten prompt |
| `RATE_LIMITED` | Answer matches rate-limit markers | Wait, try later |
| `ADAPTER_ERROR` | DOM/network failure after retry | Check browser, logs |

All errors return structured MCP tool error with `code` + `message`.

---

## 11. Project structure

```text
pyproject.toml
src/ai_router/
├── __init__.py
├── config.py
├── errors.py
├── cli/
│   ├── main.py          # typer root: `ai`
│   ├── serve.py
│   └── browser.py       # `ai browser login|status`
├── mcp/
│   ├── server.py        # FastAPI + MCP route handlers
│   └── tools.py         # ask, list_providers, session_status
├── browser/
│   └── manager.py       # CloakBrowser launch, mutex, lifecycle
├── session/
│   └── manager.py       # Mcp-Session-Id → ChatSession map
├── router/
│   └── resolve.py       # provider resolution
└── adapters/
    ├── base.py          # Protocol, SessionStatus
    ├── registry.py
    ├── gemini/
    │   ├── adapter.py
    │   ├── selectors.py
    │   └── wait.py      # network + DOM wait
    └── chatgpt/
        └── adapter.py   # stub
tests/
├── test_router.py
├── test_config.py
└── test_gemini_wait.py  # unit tests for placeholder/brace logic
```

---

## 12. Dependencies

| Package | Purpose |
|---------|---------|
| `typer[all]` | CLI (`ai`, `ai browser`, `ai serve`) |
| `cloakbrowser` | Stealth Playwright persistent browser |
| `mcp` | MCP Python SDK |
| `fastapi` + `uvicorn` | HTTP/SSE transport |
| `pyyaml` | Config file |
| `pytest` + `pytest-asyncio` | Tests |

---

## 13. Security

- Bind localhost only
- No auth in v1 (localhost trust model)
- Profile directory contains live Google session credentials — document in README
- No `page.evaluate` / script injection (anti-bot)

---

## 14. Testing strategy (v1)

**Unit (automated):**
- Config load + env overrides
- Router: default provider, explicit provider, unknown provider
- Gemini wait helpers: brace balance check, rate-limit detection

**Manual (required before ship):**
1. `ai browser login` → login Gemini → close window
2. `ai serve` → connect Cursor via mcp-remote
3. `ask` in one Cursor tab → follow-up `ask` → same chat context
4. New Cursor tab → `ask` → new Gemini chat
5. `session_status` → `logged_in`
6. `list_providers` → gemini available, chatgpt coming_soon

---

## 15. Out of scope (v1)

- MCP tool `login` (CLI only)
- JSON parse/repair from Gemini answers
- Schema validation (Pydantic) on answers
- Batch processor / record classification
- Keyword-based provider routing
- NotebookLM adapter
- Headless ask mode
- Multi-account profiles
- Session TTL cleanup

---

## 16. Future extensions

| Extension | How adapter interface supports it |
|-----------|-----------------------------------|
| ChatGPT | Implement `ProviderAdapter` with ChatGPT selectors + network signals |
| NotebookLM | New adapter; `waitForStableAnswer` v2 pattern from pleaseprompto |
| Keyword routing | Add to `router/resolve.py` without changing adapters |
| `login` MCP tool | Thin wrapper calling same `browser.login()` as CLI |
