# Design: Global `ai-router` CLI via pipx + PyPI

**Date:** 2026-07-10  
**Status:** Approved for planning  
**Goal:** End users install once and run `ai-router` without knowing Poetry or cloning the repo.

## Context

Today the CLI entry point exists (`[tool.poetry.scripts] ai = ...`) but docs and workflow force `poetry run ai ...`. That couples every user to Poetry and a local checkout.

Decisions from brainstorming:

| Decision | Choice |
|----------|--------|
| Install model | Global tool (not clone-only) |
| Installer | `pipx install ai-router` |
| Distribution | Publish to PyPI (`ai-router` name available as of 2026-07-10) |
| Packaging | Keep Poetry for maintainers; no hatch/uv migration |
| CLI command | `ai-router` (not `ai` — too short / collision-prone) |
| Cursor MCP | stdio direct spawn (not `mcp-remote` + HTTP as primary) |

## End-user experience

```bash
# one-time
python -m pip install --user pipx
pipx ensurepath   # restart terminal if needed
pipx install ai-router

# daily
ai-router browser login
# Cursor spawns: ai-router serve  (stdio)
```

Upgrade: `pipx upgrade ai-router`.

### Cursor MCP config (primary)

```json
{
  "mcpServers": {
    "ai-router": {
      "command": "ai-router",
      "args": ["serve"]
    }
  }
}
```

No separate terminal for the server. No Node/`mcp-remote` in the happy path. Login remains CLI-only (`ai-router browser login`).

## Maintainer experience

Unchanged day-to-day:

- `poetry install`
- `poetry run pytest` / `poetry run ruff ...`
- Release: bump version in `pyproject.toml` → `poetry build` → `poetry publish`

Poetry stays an internal build/dev tool. It must not appear in the user Install section of the README.

## Packaging changes

1. Rename script entry in `pyproject.toml`:

   ```toml
   [tool.poetry.scripts]
   ai-router = "ai_router.cli.main:app"
   ```

2. Rename Typer app in `src/ai_router/cli/main.py` from `name="ai"` to `name="ai-router"`.

3. Add/complete PyPI metadata as needed for a clean project page: license, homepage/repository URLs (if missing).

4. Do **not** keep a second `ai` script alias in v1 (YAGNI; can add later if requested).

## MCP transport: stdio primary, HTTP retained

Current code hardcodes HTTP:

```python
# src/ai_router/mcp/server.py
mcp.run(transport="streamable-http")
```

### Required behavior

| Flag | Default | Behavior |
|------|---------|----------|
| `--transport stdio` | **yes** (default) | `mcp.run(transport="stdio")` — Cursor spawn |
| `--transport http` | no | Existing streamable-http on `host`/`port` — debug / advanced |

CLI:

```text
ai-router serve [--transport stdio|http] [--host ...] [--port ...]
```

- For `stdio`, `--host` / `--port` are ignored (no error); they only apply to `http`.
- For `http`, keep current bind defaults (`127.0.0.1:8087`).

### Session mapping under stdio

HTTP today maps Gemini chats via `Mcp-Session-Id` request headers. Stdio has no HTTP headers.

Design requirement:

- Prefer whatever session/request identity FastMCP + Cursor expose on stdio (if available via `Context`).
- If no per-client session id is available: use a single stable default session for stdio so follow-ups in one Cursor connection still share context; document that multi-tab isolation may differ from HTTP until a richer id is available.
- Do not break HTTP header-based mapping when `--transport http` is used.

Exact FastMCP/Cursor session API is an implementation detail to verify during the plan; the contract above is the acceptance bar.

### HTTP / mcp-remote

Keep as advanced/troubleshooting only:

```bash
ai-router serve --transport http
# optional: mcp-remote → http://127.0.0.1:8087/mcp
```

Not the README primary path.

## Documentation

Split README into two audiences:

1. **Install (users)** — prerequisites (Python 3.11+, pipx, Chrome), `pipx install`, login, Cursor stdio config, CLI reference with `ai-router`.
2. **Develop (maintainers)** — Poetry, tests, publish steps.

Replace all user-facing `poetry run ai` / bare `ai` with `ai-router`.

## Release process (v1)

Manual is enough for v1:

1. Bump `version` in `pyproject.toml`
2. `poetry build`
3. `poetry publish` (PyPI API token)

Optional follow-up (out of scope for first implementation plan unless cheap): GitHub Action on tag `v*`.

## Error handling / UX notes

| Problem | Guidance |
|---------|----------|
| `ai-router: command not found` | `pipx ensurepath` + new terminal |
| Cursor cannot find `ai-router` | Cursor’s PATH may differ from the shell; use full path from `pipx list` / `where ai-router` if needed |
| Not logged in | `ai-router browser login` |
| Need HTTP debug | `ai-router serve --transport http` |

## Testing

Minimum for this feature:

1. Built wheel installs and exposes `ai-router --help` (entry point smoke).
2. `ai-router serve --transport stdio` starts without binding a TCP port (smoke; may use a short-lived process or unit-level transport selection test).
3. Regression: `--transport http` still selects streamable-http with host/port.

## Out of scope

- Standalone binary / Windows `.msi`
- Homebrew / scoop formulas
- Migrating off Poetry to uv/hatch
- Publishing under a different PyPI name
- Keeping `ai` as a CLI alias
- Changing browser login to an MCP tool

## Success criteria

- A user with Python + pipx can install from PyPI and run `ai-router` with no Poetry knowledge.
- Cursor connects via stdio using `command: ai-router`, `args: ["serve"]`.
- Maintainers still develop and publish with Poetry.
- HTTP transport remains available for debugging.
