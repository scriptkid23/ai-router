# ai-router

Python MCP server that routes prompts to web AI providers (Gemini, ChatGPT*) via CloakBrowser.

\* ChatGPT is registered but not implemented in v1.

## Requirements

| Tool | Version |
|------|---------|
| Python | 3.11+ |
| [pipx](https://pypa.github.io/pipx/) | latest |
| Chrome | stable channel |

## Install

```bash
python -m pip install --user pipx
python -m pipx ensurepath
# restart terminal
pipx install ai-router
```

Verify:

```bash
ai-router --version
ai-router --help
```

On first browser launch, CloakBrowser downloads a stealth Chromium binary (~200 MB) to `~/.cloakbrowser/`. You do **not** need to run `playwright install`.

### Login to Gemini (one-time)

```bash
ai-router browser login
```

1. A headed Chrome window opens at Gemini.
2. Log in with your Google account.
3. Close **all** browser windows when done.

Session is saved to `~/.ai-router/profile/`.

Verify login:

```bash
ai-router browser status
```

Expected output: `gemini: logged_in`

### Connect Cursor

GUI apps (Cursor on Windows/macOS) often use a PATH that differs from your shell. Use the **exact path** from your system:

```bash
# macOS/Linux
command -v ai-router

# Windows
where ai-router
```

Add to Cursor MCP settings (`mcp.json`):

```json
{
  "mcpServers": {
    "ai-router": {
      "command": "/full/path/from-command-v-or-where",
      "args": ["serve"]
    }
  }
}
```

Example paths (yours may differ):

- macOS/Linux: `~/.local/bin/ai-router`
- Windows: `C:\\Users\\<you>\\.local\\bin\\ai-router.exe`

No separate terminal for the server. Login remains CLI-only (`ai-router browser login`).

Upgrade:

```bash
pipx upgrade ai-router
```

### Use in Cursor

The agent can call these MCP tools:

| Tool | Description |
|------|-------------|
| `ask` | Send a prompt, get raw text answer from Gemini |
| `ask_multi` | Send one prompt to several providers in parallel |
| `list_providers` | List providers (`gemini` = available, `chatgpt` = coming_soon) |
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

Default transport is `stdio` (for Cursor). Use `--transport http` for debugging.

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
| `ai-router: command not found` | Run `python -m pipx ensurepath` and open a new terminal |
| Cursor cannot find `ai-router` | Paste exact path from `command -v ai-router` / `where ai-router` |
| Cursor MCP fails mysteriously | Stdio stdout must be MCP-only — ensure no startup banner on stdout |
| `pipx install ai-router` fails | Package may not be on PyPI yet, or name taken |
| `gemini: logged_out` | Run `ai-router browser login` again |
| `NOT_LOGGED_IN` from `ask` | Run `ai-router browser login` |
| Browser does not open | Requires `cloakbrowser` ≥ 0.4.4 |
| `BROWSER_BUSY` | Wait for the current `ask` to finish |
| Slow first `ask` after Cursor restart | Expected cold start; browser tabs are in-memory only |
| Profile lock / browser errors | Possible concurrent MCP processes — run one active server |
| Need HTTP debug | `ai-router serve --transport http` |

### HTTP debug (advanced)

```bash
ai-router serve --transport http
```

Optional bridge (requires Node.js):

```bash
npx -y mcp-remote@latest http://127.0.0.1:8087/mcp
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
pipx install --force dist/ai_router-*.whl
mkdir -p /tmp/ai-router-smoke && cd /tmp/ai-router-smoke
ai-router --help
ai-router --version
ai-router browser status
```

Publish:

```bash
poetry check
poetry publish -r testpypi   # dry run
poetry publish
git tag v0.1.0
git push origin v0.1.0
```

## Security

- HTTP server binds to `127.0.0.1` only (localhost).
- Profile dir (`~/.ai-router/profile/`) contains live Google session credentials — treat it like a password.

## Branches

- `python` — current Python rewrite (this README)
- `main` — previous TypeScript implementation
