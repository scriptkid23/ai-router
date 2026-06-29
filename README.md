# ai-router

MCP server that routes prompts to **ChatGPT**, **Gemini**, and **NotebookLM** using your existing browser sessions via [CloakBrowser](https://github.com/CloakHQ/cloakbrowser).

## Prerequisites

- Node.js 20+
- First run downloads CloakBrowser Chromium binary (~200MB) to `~/.cloakbrowser`

## Install

```bash
npm install
npm run build
```

## Start server

```bash
npm run serve
```

Server listens at `http://127.0.0.1:8087/mcp/sse`.

Health check: `curl http://127.0.0.1:8087/health`

## Cursor MCP configuration

Add to your Cursor MCP settings:

```json
{
  "mcpServers": {
    "ai-router": {
      "command": "npx",
      "args": ["-y", "mcp-remote@latest", "http://127.0.0.1:8087/mcp/sse"]
    }
  }
}
```

Start `npm run serve` **before** connecting Cursor.

## Workflow

1. **Start server** — `npm run serve`
2. **Login** — Agent calls `login` → browser **opens visibly** → log in to ChatGPT, Gemini, NotebookLM → close browser
3. **Ask** — Agent calls `ask` → runs **headless** by default (no window)
4. **Check sessions** — `session_status` or `list_providers`

## MCP tools

| Tool | Description |
|------|-------------|
| `login` | Open **visible** browser with provider tabs for manual login |
| `ask` | Send prompt to provider, return response (headless by default) |
| `list_providers` | List providers and routing keywords |
| `session_status` | Check login state per provider (headless by default) |

### `ask` parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `prompt` | yes | Question or instruction to send |
| `provider` | no | `chatgpt`, `gemini`, or `notebooklm` — omit to use keyword routing + default |
| `timeout_ms` | no | Max wait for response (default: `timeouts.ask_ms`, 120000) |
| `prompt_input_mode` | no | `fill` (default, paste whole prompt) or `type` (human-like keystrokes) |

## Configuration

Config file: `~/.ai-router/config.json` (auto-created on first run with defaults below).

### Default config

```json
{
  "server": {
    "host": "127.0.0.1",
    "port": 8087,
    "path": "/mcp/sse",
    "messagesPath": "/mcp/messages"
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
    "humanize": true,
    "headless": true,
    "prompt_input_mode": "fill",
    "type_delay_ms": 20
  }
}
```

Only include keys you want to override — missing keys keep defaults.

### Config reference

| Key | Default | Description |
|-----|---------|-------------|
| `server.host` | `127.0.0.1` | MCP server bind address (localhost only) |
| `server.port` | `8087` | MCP server port |
| `server.path` | `/mcp/sse` | SSE endpoint for `mcp-remote` |
| `defaultProvider` | `chatgpt` | Provider when prompt has no keyword match |
| `profileDir` | `~/.ai-router/profile` | CloakBrowser persistent profile (cookies) |
| `timeouts.ask_ms` | `120000` | Default timeout for `ask` (ms) |
| `timeouts.session_check_ms` | `30000` | Timeout for `session_status` checks |
| `routing.keywords` | see above | Keyword → provider mapping for auto-routing |
| `providers.notebooklm.notebook_url` | `null` | Open this notebook URL; `null` = first notebook in list |
| `browser.fingerprint_seed` | `42069` | Fixed CloakBrowser fingerprint across runs |
| `browser.humanize` | `true` | Human-like mouse/keyboard via CloakBrowser |
| `browser.headless` | `true` | Hide browser for `ask` / `session_status`. `login` is always visible |
| `browser.prompt_input_mode` | `fill` | `fill` = paste prompt at once; `type` = keystroke simulation |
| `browser.type_delay_ms` | `20` | Per-key delay when `prompt_input_mode` is `type` |

### Environment overrides

| Variable | Effect |
|----------|--------|
| `AI_ROUTER_PROFILE_DIR` | Override `profileDir` |
| `AI_ROUTER_DEFAULT_PROVIDER` | Override `defaultProvider` |
| `AI_ROUTER_PORT` | Override `server.port` |
| `AI_ROUTER_HOST` | Override `server.host` |
| `AI_ROUTER_HEADLESS` | `true` / `false` — override `browser.headless` |
| `AI_ROUTER_LOG_LEVEL` | `error`, `warn`, `info`, `debug` |
| `AI_ROUTER_DEBUG` | `1` — save HTML dumps on adapter errors to `~/.ai-router/debug/` |

Example — longer timeout and visible browser for debugging:

```json
{
  "timeouts": { "ask_ms": 300000 },
  "browser": { "headless": false }
}
```

Or via env when starting the server:

```bash
AI_ROUTER_HEADLESS=true npm run serve
```

## Security

- Profile at `~/.ai-router/profile/` contains live session cookies — treat as credentials
- Server binds `127.0.0.1` only — do not expose to the network
- Debug screenshots saved to `~/.ai-router/debug/` on adapter errors

## Development

```bash
npm test
npm run typecheck
npm run build
```
