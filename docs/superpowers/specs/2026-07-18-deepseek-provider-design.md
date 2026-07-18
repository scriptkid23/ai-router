# DeepSeek Provider Design

**Date:** 2026-07-18  
**Status:** Approved  
**Scope:** Add DeepSeek (chat.deepseek.com web) as a fourth provider in AI Router

## Summary

Add a `deepseek` provider that automates authenticated DeepSeek web sessions via Playwright/CloakBrowser, following the same adapter pattern as ChatGPT, Gemini, and Claude. Each `ask` opens a new chat, submits a prompt through the DOM, listens to the `/api/v0/chat/completion` SSE endpoint for stream-end signals, and reads the main response text from the DOM (excluding thinking blocks).

## Requirements (confirmed)

| Decision | Choice |
|----------|--------|
| Answer source | DOM only — `.ds-assistant-message-main-content` (stream is signal-only) |
| Thinking blocks | Excluded from returned text |
| Chat lifecycle | New chat per `ask` → navigate to `https://chat.deepseek.com/` |
| Model selection | Use account default; no UI intervention |
| Thinking toggle | Use account UI default; no intervention |
| Stream completion | `event: close` (+ `response/status: FINISHED` as secondary) |
| Auth | Browser session (cookies + PoW headers auto-handled by web UI) |

## Architecture

```mermaid
flowchart LR
    MCP["MCP ask(provider=deepseek)"] --> Worker["PageWorker"]
    Worker --> Planner["DeepSeekPlanner"]
    Planner --> Browser["Playwright tab chat.deepseek.com"]
    Browser --> SSE["Intercept /api/v0/chat/completion SSE"]
    Browser --> DOM["Read .ds-assistant-message-main-content"]
    SSE --> Parser["parse_stream_done"]
    Parser --> Reducer["StateReducer"]
    DOM --> Reducer
    Reducer --> Answer["Return main response text"]
```

### Per-ask flow

1. `goto` → `https://chat.deepseek.com/`
2. `wait_idle` → prompt input ready
3. `clear_input` → `type` → `submit`
4. `wait_generating` → generation started
5. `wait_answer` → stream end + DOM stable (StateReducer hybrid gate)
6. Read text from last `.ds-assistant-message-main-content`

## Module structure

```
src/ai_router/adapters/deepseek/
├── __init__.py
├── adapter.py      # DeepSeekAdapter
├── selectors.py    # URLs, regex, DOM selectors, error markers
├── stream.py       # parse_stream_done, SSE event+data iterator
├── wait.py         # is_stop_visible, read_response_snapshot, submit_ready
└── planner.py      # DeepSeekPlanner
```

### DeepSeekAdapter

| Field | Value |
|-------|-------|
| `id` | `"deepseek"` |
| `name` | `"DeepSeek"` |
| `keywords` | `["deepseek", "@deepseek"]` |
| `status` | `"available"` |

`build_profile()` mirrors Claude wiring:

```python
ProviderProfile(
    provider_id="deepseek",
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
)
```

## Stream parsing

### Network interception

```python
DEEPSEEK_COMPLETION_RE = re.compile(
    r"/api/v0/chat/completion(?:\?|$)",
    re.I,
)
```

### SSE format

DeepSeek web uses named SSE events with JSON Patch-style payloads:

```
event: ready
data: {"request_message_id":3,"response_message_id":4,"model_type":"expert"}

data: {"p":"response/fragments/-1/content","o":"APPEND","v":" need"}
data: {"p":"response/status","o":"SET","v":"FINISHED"}

event: close
data: {"click_behavior":"none","auto_resume":false}
```

Unlike Claude/ChatGPT parsers (which scan `data:` lines only), DeepSeek requires parsing **`event:` lines** together with their adjacent `data:` payloads. Intermediate THINK fragment patches are ignored for completion detection.

### `parse_stream_done(status, body) → StreamDone`

| Condition | Result |
|-----------|--------|
| HTTP 401, 403, 429 or body contains rate-limit markers | `done=True, ok=False, error_kind="rate_limit"` |
| HTTP ≥ 400 (other) | `done=True, ok=False, error_kind="error"` |
| `event: close` seen in body | `done=True, ok=True` |
| Patch `{"p":"response/status","o":"SET","v":"FINISHED"}` | `done=True, ok=True` |
| BATCH patch with `quasi_status: "FINISHED"` | `done=True, ok=True` |
| Partial stream (THINK fragments only, no end signal) | `done=False, ok=False` |

Answer text is read from the DOM by StateReducer — not from SSE `RESPONSE` fragment patches.

## DOM selectors

### Response (from captured HTML)

```python
SEL_ASSISTANT_MAIN = ".ds-assistant-message-main-content"
SEL_ASSISTANT_TEXT = ".ds-assistant-message-main-content .ds-markdown"
# Excluded: .ds-think-content (thinking blocks)
```

### `read_response_snapshot(page)`

1. Count assistant turns via `.ds-assistant-message-main-content`
2. Read `.ds-markdown` inner_text from the last main-content block
3. Return `(count, text)`

### `is_stop_visible(page)`

Returns `True` while either:

- A Stop button is visible (discovered during implementation, e.g. `aria-label*="Stop"`), or
- Generation indicators are present before main content appears

### Input / submit (discover during implementation)

```python
DEEPSEEK_URL = "https://chat.deepseek.com/"

SEL_PROMPT_INPUT = (
    'textarea, '
    'div[contenteditable="true"]'
)
SEL_SUBMIT_BUTTON = (
    'button[aria-label*="Send" i], '
    'button[type="submit"]'
)
SEL_LOGIN = 'a[href*="/login"], button:has-text("Log in")'
```

### Error markers

```python
DEEPSEEK_ERROR_MARKERS = (
    "something went wrong",
    "unable to respond",
    "an error occurred",
)

RATE_LIMIT_MARKERS = (
    "rate limit",
    "too many requests",
    "try again later",
)
```

## Planner

```python
[
    Command("goto", {"url": DEEPSEEK_URL}),
    Command("wait_idle"),
    Command("clear_input"),
    Command("type", {"prompt": job.prompt}),
    Command("submit"),
    Command("wait_generating"),
    Command("wait_answer"),
]
```

Recovery uses the same script (reload + retry) as ChatGPT and Claude.

## Config & registry

### Registry

Register `DeepSeekAdapter()` in `build_registry()` alongside Gemini, ChatGPT, and Claude.

### Config defaults

```yaml
providers:
  deepseek:
    url: "https://chat.deepseek.com/"
```

`deepseek_answer_timeout_s` in `AppConfig` (mirrors `claude_answer_timeout_s`). Default `300.0`; YAML/env overrides supported.

Environment variable: `AI_ROUTER_DEEPSEEK_ANSWER_TIMEOUT_S`.

## Session / login

- `check_session`: navigate to `https://chat.deepseek.com/`, wait for `SEL_PROMPT_INPUT` → `LOGGED_IN`
- `SEL_LOGIN` visible → `LOGGED_OUT`
- Timeout without either → `UNKNOWN`
- CLI: `ai-router browser login --provider deepseek` (reuses existing browser login flow)

## Error handling

| Code | Trigger |
|------|---------|
| `DEEPSEEK_ERROR` | DOM error markers or non-recoverable HTTP errors |
| Rate limit | HTTP 429, auth errors, or rate-limit markers in body/DOM |

Recoverable codes for planner retry: `("DEEPSEEK_ERROR",)`.

Partial SSE bodies without `event: close` / `FINISHED` return `done=False` (no `stream_end` event). The job then relies on the DOM no-stream fallback or times out — same as Claude.

## Testing

Unit tests only (no live browser required):

### `tests/test_deepseek_stream.py`

- `event: close` → `done=True, ok=True`
- `response/status SET FINISHED` → `done=True, ok=True`
- BATCH `quasi_status: FINISHED` → `done=True, ok=True`
- Partial stream (THINK fragments only) → `done=False`
- HTTP 429 → `error_kind="rate_limit"`

### `tests/test_deepseek_planner.py`

- Plan includes `goto` to `chat.deepseek.com`
- Core command sequence: clear → type → submit → wait

### Other updates

- `tests/test_router.py` — add case resolving `provider=deepseek`
- `tests/test_ask_multi.py` — include deepseek in available providers list

## Documentation

Update README:

- Add DeepSeek to supported providers table
- Add `ai-router browser login --provider deepseek` example
- `list_providers` returns `deepseek` with `status: available`

## Out of scope

- Direct API calls with Bearer token (web automation only)
- Model selection via UI or config
- Multi-turn conversation (keeping existing chat)
- Extracting answer text from SSE `RESPONSE` fragment patches
- Thinking content in returned text
- PoW solver implementation (`x-ds-pow-response` — browser handles it)
- WebSocket completion source (`parse_ws_frame`)

## Reference: captured completion request

```
POST https://chat.deepseek.com/api/v0/chat/completion
Accept: text/event-stream
Content-Type: application/json
Authorization: Bearer <session token>
x-ds-pow-response: <PoW challenge response>
x-hif-leim: <anti-bot header>

Body: {
  "chat_session_id": "<uuid>",
  "parent_message_id": 2,
  "model_type": null,
  "prompt": "2+2",
  "thinking_enabled": true,
  "search_enabled": false,
  ...
}
```

Stream end signals: `response/status SET FINISHED`, then `event: close`.
