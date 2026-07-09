# ai-router

Python MCP server that routes prompts to web AI providers (Gemini, ChatGPT*) via CloakBrowser.

\* ChatGPT is registered but not implemented in v1.

## Requirements

- Python 3.11+
- [Poetry](https://python-poetry.org/)
- Chrome (stable)
- Node.js (for `mcp-remote` bridge in Cursor)

## Install

```bash
poetry install
```

## Usage

### 1. Login (once)

```bash
poetry run ai browser login
```

Log in to Gemini in the headed browser, then close all windows.

### 2. Start MCP server

```bash
poetry run ai serve
```

Default: `http://127.0.0.1:8087/mcp`

### 3. Check session

```bash
poetry run ai browser status
```

### 4. Connect Cursor

Add to Cursor MCP config:

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

## MCP Tools

| Tool | Description |
|------|-------------|
| `ask` | Send prompt, get raw text answer |
| `list_providers` | List providers and availability |
| `session_status` | Check login state |

Login is CLI-only: `ai browser login` (not an MCP tool).

## Config

`~/.ai-router/config.yaml` — optional. Env overrides:

- `AI_ROUTER_PROFILE_DIR`
- `AI_ROUTER_DEFAULT_PROVIDER`
- `AI_ROUTER_HOST`
- `AI_ROUTER_PORT`
- `AI_ROUTER_ANSWER_TIMEOUT_S`

## Development

```bash
poetry run pytest -v
poetry run ruff check src tests
```

## Security

Profile dir (`~/.ai-router/profile/`) contains live browser sessions. Bind is localhost-only.

Previous TypeScript implementation: branch `main`.
