# ai-router

Python MCP server that routes prompts to web AI providers (Gemini, ChatGPT*) via CloakBrowser.

\* ChatGPT is registered but not implemented in v1.

## Requirements

| Tool | Version |
|------|---------|
| Python | 3.11+ |
| [Poetry](https://python-poetry.org/) | 2.x |
| Chrome | stable channel |
| Node.js | for `mcp-remote` bridge in Cursor |

## Setup

### 1. Install dependencies

```bash
git checkout python
cd ai-router
poetry install
```

Verify the CLI:

```bash
poetry run ai --help
```

On first browser launch, CloakBrowser downloads a stealth Chromium binary (~200 MB) to `~/.cloakbrowser/`. You do **not** need to run `playwright install`.

### 2. Login to Gemini (one-time)

```bash
poetry run ai browser login
```

1. A headed Chrome window opens at Gemini.
2. Log in with your Google account.
3. Close **all** browser windows when done.

Session is saved to `~/.ai-router/profile/`.

Verify login:

```bash
poetry run ai browser status
```

Expected output: `gemini: logged_in`

### 3. Start the MCP server

Keep this running in a separate terminal:

```bash
poetry run ai serve
```

Default endpoint: `http://127.0.0.1:8087/mcp`

Custom port:

```bash
poetry run ai serve --port 9090
```

### 4. Connect Cursor

Add to Cursor MCP settings (`mcp.json`):

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

**Important:** `ai serve` must be running **before** Cursor connects.

### 5. Use in Cursor

The agent can call these MCP tools:

| Tool | Description |
|------|-------------|
| `ask` | Send a prompt, get raw text answer from Gemini |
| `list_providers` | List providers (`gemini` = available, `chatgpt` = coming_soon) |
| `session_status` | Check whether providers are logged in |

Login is **CLI only** — there is no MCP `login` tool. Run `ai browser login` manually.

**Conversation behavior:**

- Same Cursor tab → same Gemini chat (follow-ups keep context)
- New Cursor tab → new Gemini chat (mapped automatically via `Mcp-Session-Id`)

## CLI reference

```bash
poetry run ai serve [--host 127.0.0.1] [--port 8087]
poetry run ai browser login [--provider gemini]
poetry run ai browser status [--provider gemini]
```

## Config (optional)

Create `~/.ai-router/config.yaml`:

```yaml
default_provider: gemini
host: 127.0.0.1
port: 8087
answer_timeout_s: 120
profile_dir: ~/.ai-router/profile
providers:
  gemini:
    url: https://gemini.google.com/app
```

Environment variable overrides:

| Variable | Default | Description |
|----------|---------|-------------|
| `AI_ROUTER_PROFILE_DIR` | `~/.ai-router/profile` | CloakBrowser persistent profile |
| `AI_ROUTER_DEFAULT_PROVIDER` | `gemini` | Default provider for `ask` |
| `AI_ROUTER_HOST` | `127.0.0.1` | MCP server bind address |
| `AI_ROUTER_PORT` | `8087` | MCP server port |
| `AI_ROUTER_ANSWER_TIMEOUT_S` | `120` | Per-request answer timeout |

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `gemini: logged_out` | Run `poetry run ai browser login` again |
| Cursor cannot connect | Ensure `ai serve` is running and the URL port matches |
| Browser does not open | Install Chrome; wait for CloakBrowser binary download on first run |
| `BROWSER_BUSY` | Wait for the current `ask` to finish (one request at a time) |
| `NOT_LOGGED_IN` from `ask` | Run `poetry run ai browser login` |

## Development

```bash
poetry run pytest -v
poetry run ruff check src tests
```

## Security

- Server binds to `127.0.0.1` only (localhost).
- Profile dir (`~/.ai-router/profile/`) contains live Google session credentials — treat it like a password.

## Branches

- `python` — current Python rewrite (this README)
- `main` — previous TypeScript implementation
