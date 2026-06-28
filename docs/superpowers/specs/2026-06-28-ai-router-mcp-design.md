# ai-router MCP Design

**Date:** 2026-06-28  
**Status:** Approved for implementation planning  
**Project:** ai-router — MCP server routing prompts to ChatGPT, Gemini, and NotebookLM via CloakBrowser

---

## 1. Summary

ai-router is a local MCP server that lets Cursor Agent send prompts to web-based AI providers (ChatGPT, Gemini, NotebookLM) using the user's existing browser sessions. Sessions are established once via manual login in a real browser, persisted with CloakBrowser, and reused headlessly for subsequent `ask` calls.

**Key decisions:**

| Decision | Choice |
|----------|--------|
| Interface | MCP tools only (no slash commands, no standalone CLI for v1) |
| Transport | HTTP/SSE server + `mcp-remote` bridge (same pattern as docgraph) |
| Browser | CloakBrowser (`launchPersistentContext`) |
| Stack | TypeScript / Node.js + `@modelcontextprotocol/sdk` + `cloakbrowser` |
| Login | Single `login()` call, one browser, user logs into all providers, close browser = done |
| Ask execution | Headless |
| Providers v1 | ChatGPT, Gemini, NotebookLM — extensible adapter architecture |
| Routing | Optional `provider` param; fallback = configurable default + keyword heuristic; return `routing_reason` |

---

## 2. Architecture

```
┌──────────────┐     stdio      ┌─────────────┐    HTTP/SSE    ┌──────────────────┐
│ Cursor Agent │ ◄────────────► │ mcp-remote  │ ◄────────────► │ ai-router serve  │
│              │   (npx bridge) │  (npx)      │ 127.0.0.1:8088 │  (long-running)  │
└──────────────┘                └─────────────┘                └────────┬─────────┘
                                                                          │
                         ┌────────────────────────────────────────────────┤
                         ▼                    ▼                           ▼
                  Tool Layer           Router                    Session Store
              login|ask|list|status   default+keyword          ~/.ai-router/
                         │                                              profile/
                         ▼                                              config.json
                  BrowserManager (mutex)
                         │
                         ▼
              ProviderAdapter interface
              ┌──────────┬──────────┬──────────────┐
              │ ChatGPT  │ Gemini   │ NotebookLM   │  + future
              └──────────┴──────────┴──────────────┘
```

### 2.1 Monolith server (recommended approach)

Single Node.js process: MCP SSE server calls CloakBrowser directly. BrowserManager uses a mutex so `login()` and `ask()` never run concurrently.

**Rejected alternatives:**

- **Browser Worker (child process):** unnecessary complexity for v1
- **CloakBrowser CDP Docker (`cloakserve`):** adds Docker dependency; harder headed login on Windows

### 2.2 Session storage

Single persistent profile directory shared across all providers:

- Path: `~/.ai-router/profile/` (CloakBrowser `userDataDir`)
- All provider cookies/localStorage live in one Chrome profile
- Matches login flow: user opens multiple tabs in one browser session

### 2.3 BrowserManager

- Wraps `launchPersistentContext` from `cloakbrowser`
- Mutex lock: reject concurrent `login()` / `ask()` with `BROWSER_BUSY`
- Reject duplicate `login()` with `LOGIN_IN_PROGRESS`
- `login()`: `headless: false`, `humanize: true`
- `ask()` / `session_status()`: `headless: true`, `humanize: true`

---

## 3. MCP Transport

### 3.1 Cursor configuration

```json
{
  "mcpServers": {
    "ai-router": {
      "command": "npx",
      "args": ["-y", "mcp-remote@latest", "http://127.0.0.1:8088/mcp/sse"]
    }
  }
}
```

### 3.2 Server process

| Setting | Value |
|---------|-------|
| Start command | `npx ai-router serve` or `npm run serve` |
| Bind address | `127.0.0.1` only |
| Default port | `8088` |
| MCP endpoint | `/mcp/sse` |
| Override | `--port`, `--host`, env `AI_ROUTER_PORT`, `AI_ROUTER_HOST` |

Server must be running before Cursor connects. v1 supports HTTP/SSE only — no stdio mode.

### 3.3 Security

- Bind localhost only; never `0.0.0.0`
- No authentication in v1 (localhost trust model, same as docgraph)
- Profile directory contains live session credentials — document in README

---

## 4. MCP Tools

### 4.1 `login`

Opens a headed browser for manual authentication across providers.

**Input:**

```typescript
{
  start_url?: string  // default: "about:blank"
}
```

**Output:**

```typescript
{
  success: true,
  message: "Browser closed. Session saved.",
  profile_path: string,
  duration_ms: number
}
```

**Behavior:**

1. Acquire mutex; fail if `BROWSER_BUSY` or `LOGIN_IN_PROGRESS`
2. Launch `launchPersistentContext({ userDataDir, headless: false, humanize: true })`
3. Open blank tab or optional helper page listing provider login URLs:
   - ChatGPT: `https://chatgpt.com`
   - Gemini: `https://gemini.google.com`
   - NotebookLM: `https://notebooklm.google.com`
4. Block until browser disconnects (user closes window)
5. Persistent context auto-saves; return success
6. Release mutex

### 4.2 `ask`

Sends a prompt to a provider and returns the response.

**Input:**

```typescript
{
  prompt: string,           // required
  provider?: "chatgpt" | "gemini" | "notebooklm",
  timeout_ms?: number       // default: 120000
}
```

**Output:**

```typescript
{
  text: string,
  provider: string,
  routing_reason: string,   // "explicit" | "keyword:gemini" | "default:chatgpt"
  duration_ms: number,
  url: string
}
```

**Routing (when `provider` omitted):**

1. Scan prompt for keywords (case-insensitive, from config):
   - `gemini`, `@gemini`, `hỏi gemini` → gemini
   - `notebooklm`, `notebook lm`, `@notebooklm` → notebooklm
   - `chatgpt`, `gpt`, `@chatgpt` → chatgpt
2. No match → `config.defaultProvider` (default: `chatgpt`)

First matching keyword wins. Log and return `routing_reason`.

**Behavior:**

1. Validate prompt non-empty → `PROMPT_EMPTY`
2. Resolve provider via router
3. Acquire mutex; fail if busy
4. Launch headless persistent context with same profile
5. Delegate to provider adapter
6. Return response text + metadata
7. Release mutex

### 4.3 `list_providers`

**Output:**

```typescript
{
  providers: [
    {
      id: "chatgpt",
      name: "ChatGPT",
      url: "https://chatgpt.com",
      keywords: ["chatgpt", "gpt", "@chatgpt"]
    },
    // gemini, notebooklm
  ],
  default_provider: "chatgpt"
}
```

### 4.4 `session_status`

Checks login state per provider without sending a prompt.

**Input:**

```typescript
{
  providers?: string[]  // optional filter; default: all
}
```

**Output:**

```typescript
{
  profile_exists: boolean,
  sessions: [
    {
      provider: "chatgpt",
      status: "logged_in" | "logged_out" | "unknown",
      checked_at: string  // ISO 8601
    }
  ]
}
```

Headless quick check via adapter `checkSession()`.

---

## 5. Error Codes

| Code | Meaning |
|------|---------|
| `BROWSER_BUSY` | Another tool holds the browser mutex |
| `LOGIN_IN_PROGRESS` | Duplicate `login()` call |
| `NO_PROFILE` | Profile directory missing; run `login()` first |
| `SESSION_EXPIRED` | Provider session invalid; run `login()` |
| `PROVIDER_NOT_FOUND` | Unknown provider id |
| `TIMEOUT` | Response exceeded `timeout_ms` |
| `ADAPTER_ERROR` | DOM selector failure / UI changed |
| `PROMPT_EMPTY` | Empty prompt |

Errors returned as MCP tool error text with prefix, e.g.:

```
[SESSION_EXPIRED] ChatGPT session expired. Run login() to re-authenticate.
```

On `ADAPTER_ERROR`, save debug screenshot to `~/.ai-router/debug/<timestamp>.png`.

---

## 6. Provider Adapters

### 6.1 Interface

```typescript
interface ProviderAdapter {
  id: string;
  name: string;
  url: string;
  keywords: string[];

  checkSession(page: Page): Promise<"logged_in" | "logged_out" | "unknown">;
  ask(page: Page, prompt: string, options: AskOptions): Promise<string>;
}

interface AskOptions {
  timeoutMs: number;
  signal?: AbortSignal;
}
```

Registry maps `id → adapter`. Adding a provider = new file + registry entry; no core changes.

### 6.2 Common flow

```
navigate(url)
  → wait for load
  → checkSession() → throw SESSION_EXPIRED if logged_out
  → locate input (contenteditable / textarea)
  → type prompt (humanize: type(), not fill())
  → submit (Enter or Send button)
  → waitForResponseComplete()
  → extractLastAssistantMessage()
  → return text
```

**Response completion heuristic:**

- Send/Stop button state change, OR
- Assistant message text stable for 2 consecutive seconds, OR
- Timeout → return partial text if available

Prefer role/aria/data-testid selectors over CSS classes.

### 6.3 ChatGPT

| Step | Detail |
|------|--------|
| URL | `https://chatgpt.com` |
| Session | Chat input present vs "Log in" button |
| Input | `textarea` or `div[contenteditable=true]` |
| Submit | Enter |
| Extract | Last `[data-message-author-role="assistant"]` message |

### 6.4 Gemini

| Step | Detail |
|------|--------|
| URL | `https://gemini.google.com/app` |
| Session | Chat input vs Google login redirect |
| Input | `rich-textarea` / contenteditable in chat area |
| Submit | Enter or Send button |
| Extract | Last response block in conversation panel |

### 6.5 NotebookLM

| Step | Detail |
|------|--------|
| URL | `https://notebooklm.google.com` |
| Session | Dashboard accessible vs login wall |
| Notebook | Use `config.providers.notebooklm.notebook_url` if set; else first notebook in list |
| Input | Chat panel (requires active notebook) |
| Submit | Enter or Send |
| Extract | Last assistant reply in chat panel |

**v1 limitation:** No source upload via MCP — chat only against an existing notebook. Document in `list_providers` metadata.

### 6.6 Anti-detection in `ask()`

```typescript
launchPersistentContext({
  userDataDir: PROFILE_PATH,
  headless: true,
  humanize: true,
  args: [`--fingerprint=${config.browser.fingerprint_seed}`],
})
```

- Use `page.locator().type()` not `fill()`
- Avoid Playwright `waitForTimeout()` — use Node `setTimeout` or `waitForFunction`
- Fixed fingerprint seed for consistent device identity across sessions

### 6.7 Adding future providers (e.g. Claude.ai)

1. Create `src/adapters/claude.ts` implementing `ProviderAdapter`
2. Register in `src/adapters/registry.ts`
3. Optionally add keywords to config

---

## 7. Configuration

**Path:** `~/.ai-router/config.json` (created with defaults on first run)

```json
{
  "server": {
    "host": "127.0.0.1",
    "port": 8088,
    "path": "/mcp/sse"
  },
  "defaultProvider": "chatgpt",
  "profileDir": "~/.ai-router/profile",
  "timeouts": {
    "ask_ms": 120000,
    "session_check_ms": 30000
  },
  "routing": {
    "keywords": {
      "gemini": ["gemini", "@gemini", "hỏi gemini"],
      "notebooklm": ["notebooklm", "notebook lm", "@notebooklm"],
      "chatgpt": ["chatgpt", "gpt", "@chatgpt"]
    }
  },
  "providers": {
    "notebooklm": {
      "notebook_url": null
    }
  },
  "browser": {
    "fingerprint_seed": "42069",
    "humanize": true
  }
}
```

**Environment overrides:**

- `AI_ROUTER_PROFILE_DIR`
- `AI_ROUTER_DEFAULT_PROVIDER`
- `AI_ROUTER_PORT`
- `AI_ROUTER_HOST`
- `AI_ROUTER_LOG_LEVEL` (`error` | `warn` | `info` | `debug`)
- `AI_ROUTER_DEBUG=1` — enable HTML dump on adapter errors

Shallow merge: user config overrides defaults only for specified keys.

---

## 8. Logging & Debug

- **stderr:** structured logs (`[ai-router] level=info tool=ask provider=gemini duration_ms=8421`)
- **stdout:** MCP protocol only
- Do not log prompts/responses at `info` level (debug only)
- On adapter failure: screenshot + optional HTML dump to `~/.ai-router/debug/`

---

## 9. Project Structure

```
ai-router/
├── src/
│   ├── cli.ts                 # serve | status subcommands
│   ├── server.ts              # HTTP + MCP SSE transport
│   ├── mcp/
│   │   └── register-tools.ts
│   ├── tools/
│   │   ├── login.ts
│   │   ├── ask.ts
│   │   ├── list-providers.ts
│   │   └── session-status.ts
│   ├── browser/
│   │   └── manager.ts
│   ├── router/
│   │   └── resolve-provider.ts
│   ├── adapters/
│   │   ├── types.ts
│   │   ├── registry.ts
│   │   ├── chatgpt.ts
│   │   ├── gemini.ts
│   │   └── notebooklm.ts
│   └── config/
│       └── load-config.ts
├── docs/superpowers/specs/
├── package.json
├── tsconfig.json
└── README.md
```

---

## 10. Testing Strategy

| Layer | Approach |
|-------|----------|
| Router | Unit tests — keyword matching, default fallback |
| Config | Unit tests — defaults, env override, merge |
| Adapters | Manual integration with real sessions |
| MCP tools | Manual via Cursor after `serve` + `mcp-remote` |
| BrowserManager | Unit test mutex (mocked browser) |

**CI v1:** `tsc --noEmit` + router/config unit tests. No CloakBrowser in CI.

**Local smoke test:** `npm run build && node scripts/smoke-session-status.js`

---

## 11. Scope

### v1 (this spec)

- HTTP/SSE MCP server on localhost
- 4 tools: `login`, `ask`, `list_providers`, `session_status`
- 3 providers with extensible adapters
- Keyword routing + configurable default
- Headless `ask`
- Single concurrent browser operation (mutex)

### Post-v1

- `ask_stream` with partial updates
- Headed fallback when `SESSION_EXPIRED`
- NotebookLM source upload
- LLM-based provider routing
- Request queue or browser pool for concurrency
- Optional auth token for non-localhost deployment

---

## 12. Dependencies

```json
{
  "dependencies": {
    "@modelcontextprotocol/sdk": "latest",
    "cloakbrowser": "latest",
    "playwright-core": "peer via cloakbrowser"
  }
}
```

First run downloads CloakBrowser Chromium binary (~200MB) to `~/.cloakbrowser`.

---

## 13. User Workflow

1. Start server: `npm run serve`
2. Configure Cursor MCP with `mcp-remote` → `http://127.0.0.1:8088/mcp/sse`
3. Agent calls `login()` → user logs into ChatGPT, Gemini, NotebookLM in browser tabs → close browser
4. Agent calls `ask({ prompt: "..." })` or with explicit `provider`
5. Agent receives response text + `routing_reason`
6. On session expiry: `session_status` or `ask` returns `SESSION_EXPIRED` → repeat step 3
