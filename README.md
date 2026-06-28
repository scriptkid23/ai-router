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

Server listens at `http://127.0.0.1:8088/mcp/sse`.

Health check: `curl http://127.0.0.1:8088/health`

## Cursor MCP configuration

Add to your Cursor MCP settings:

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

Start `npm run serve` **before** connecting Cursor.

## Workflow

1. **Start server** — `npm run serve`
2. **Login** — Agent calls `login` tool → browser opens → log in to ChatGPT, Gemini, NotebookLM in tabs → close browser
3. **Ask** — Agent calls `ask` with a prompt (optionally `provider`)
4. **Check sessions** — `session_status` or `list_providers`

## MCP tools

| Tool | Description |
|------|-------------|
| `login` | Open headed browser for manual login |
| `ask` | Send prompt to provider, return response |
| `list_providers` | List providers and routing keywords |
| `session_status` | Check login state per provider |

## Configuration

Config file: `~/.ai-router/config.json` (auto-created on first run)

Environment overrides:

- `AI_ROUTER_PROFILE_DIR` — browser profile path
- `AI_ROUTER_DEFAULT_PROVIDER` — default routing target
- `AI_ROUTER_PORT` / `AI_ROUTER_HOST` — server bind
- `AI_ROUTER_LOG_LEVEL` — `error`, `warn`, `info`, `debug`
- `AI_ROUTER_DEBUG=1` — save HTML dumps on adapter errors

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
