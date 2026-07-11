# ai-router

Python MCP server that routes prompts to web AI providers (Gemini, ChatGPT) via CloakBrowser.

| | Name |
|---|------|
| **PyPI / pipx** | [`mcp-ai-router`](https://pypi.org/project/mcp-ai-router/) |
| **CLI command** | `ai-router` |
| **GitHub** | [scriptkid23/ai-router](https://github.com/scriptkid23/ai-router) |

## Requirements

| Tool | Version |
|------|---------|
| Python | 3.11+ |
| [pipx](https://pypa.github.io/pipx/) | latest |
| Chrome | stable channel |

No Poetry, Node.js, or repo clone required for normal use.

## Install

```bash
python -m pip install --user pipx
python -m pipx ensurepath
# open a new terminal
pipx install mcp-ai-router
```

`pipx ensurepath` adds `~/.local/bin` to your shell PATH so `ai-router` works in the terminal.

Verify:

```bash
ai-router --version
ai-router --help
```

On first browser launch, CloakBrowser downloads a stealth Chromium binary (~200 MB) to `~/.cloakbrowser/`. You do **not** need to run `playwright install`.

Upgrade later:

```bash
pipx upgrade mcp-ai-router
```

### Login (one-time)

```bash
ai-router browser login
```

1. Headed Chrome windows open for each available provider (Gemini, ChatGPT).
2. Log in to each provider.
3. Close **all** browser windows when done.

Session is saved to `~/.ai-router/profile/`.

Verify login:

```bash
ai-router browser status
```

Expected output: `gemini: logged_in` and/or `chatgpt: logged_in`

### Connect Cursor (stdio — recommended)

Add to Cursor MCP settings (`~/.cursor/mcp.json` or project `.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "ai-router": {
      "type": "stdio",
      "command": "ai-router",
      "args": ["serve"]
    }
  }
}
```

This matches how other stdio MCP servers (e.g. codegraph) are configured. Cursor spawns `ai-router serve`; default transport is **stdio** — no separate terminal, no Node.js.

**Prerequisites before connecting:** run `ai-router browser login` once (see above).

#### If `"command": "ai-router"` fails

GUI apps sometimes use a different PATH than your terminal. Use the full path instead:

```bash
# macOS/Linux
command -v ai-router

# Windows
where ai-router
```

```json
{
  "mcpServers": {
    "ai-router": {
      "type": "stdio",
      "command": "/full/path/from-command-v-or-where",
      "args": ["serve"]
    }
  }
}
```

Example paths:

- macOS/Linux: `~/.local/bin/ai-router`
- Windows: `C:\\Users\\<you>\\.local\\bin\\ai-router.exe`

Reload MCP in Cursor after saving.

### Use in Cursor

The agent can call these MCP tools:

| Tool | Description |
|------|-------------|
| `ask` | Send a prompt, get raw text answer (default: Gemini; use `provider` param for ChatGPT) |
| `ask_multi` | Send one prompt to several providers in parallel |
| `list_providers` | List providers (`gemini`, `chatgpt`) |
| `session_status` | Check whether providers are logged in |

Login is **CLI only** — there is no MCP `login` tool. Run `ai-router browser login` manually.

**Conversation behavior:**

Each `ask` opens a **new** provider chat. Follow-up context is not preserved across calls. Cursor conversation context and provider chat context are separate; ai-router does not reuse the previous provider chat. Browser login (Google session) is persistent via `~/.ai-router/profile/`.

## CLI reference

```bash
ai-router --version
ai-router serve [--transport stdio|http] [--host 127.0.0.1] [--port 8087]
ai-router browser login [--provider gemini]
ai-router browser status [--provider gemini]
```

| Transport | Use case |
|-----------|----------|
| `stdio` (default) | Cursor MCP — `args: ["serve"]` |
| `http` | Local debugging only — see below |

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
| `AI_ROUTER_HOST` | `127.0.0.1` | MCP HTTP server bind address |
| `AI_ROUTER_PORT` | `8087` | MCP HTTP server port |
| `AI_ROUTER_ANSWER_TIMEOUT_S` | `120` | Per-request answer timeout |

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `pipx: command not found` right after install | Use `python -m pipx` instead |
| `ai-router: command not found` in terminal | Run `python -m pipx ensurepath` and open a new terminal |
| Cursor MCP red / cannot find `ai-router` | Use full path in `"command"` (see above) |
| Cursor MCP fails mysteriously | Stdio stdout must be MCP-only — no startup banner on stdout |
| `pipx install mcp-ai-router` fails | Check https://pypi.org/project/mcp-ai-router/ is reachable |
| `gemini: logged_out` | Run `ai-router browser login` again |
| `NOT_LOGGED_IN` from `ask` | Run `ai-router browser login` |
| Browser does not open | Requires `cloakbrowser` ≥ 0.4.4 |
| `BROWSER_BUSY` | Wait for the current `ask` to finish |
| Slow first `ask` after Cursor restart | Expected cold start; browser tabs are in-memory only |
| Profile lock / browser errors | Possible concurrent MCP processes — run one active server |
| Need HTTP debug | `ai-router serve --transport http` |

### HTTP debug (advanced)

Not needed for Cursor. Requires a separate terminal and optional Node.js bridge:

```bash
ai-router serve --transport http
```

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

## Development

Maintainers use Poetry in a repo checkout:

```bash
git clone https://github.com/scriptkid23/ai-router.git
cd ai-router
poetry install
poetry run pytest -v
poetry run ruff check src tests
poetry run ai-router serve --transport http   # local HTTP debug
```

Before release, test against:

- The `mcp` version locked in `poetry.lock` (currently 1.12.4)
- Latest stable `mcp` `<2`
- A clean wheel install from an empty directory outside the repo

Build and smoke-test a wheel locally:

```bash
poetry build
pipx install --force dist/mcp_ai_router-*.whl
mkdir -p /tmp/ai-router-smoke && cd /tmp/ai-router-smoke
ai-router --help
ai-router --version
ai-router browser status
```

Publish to PyPI (package name `mcp-ai-router`; PyPI renders this README as the project description):

```bash
poetry check
poetry build
poetry publish -r testpypi   # optional dry run
poetry publish
git tag v0.1.2
git push origin v0.1.2
```

## Security

- HTTP server binds to `127.0.0.1` only (localhost).
- Profile dir (`~/.ai-router/profile/`) contains live session credentials — treat it like a password.
