# Design: Global `ai-router` CLI via pipx + PyPI

**Date:** 2026-07-10  
**Status:** Approved for planning (revised after multi-pass review)  
**Goal:** End users install once and run `ai-router` without knowing Poetry or cloning the repo.

## Context

Today the CLI entry point exists (`[tool.poetry.scripts] ai = ...`) but docs and workflow force `poetry run ai ...`. That couples every user to Poetry and a local checkout.

Decisions from brainstorming:

| Decision | Choice |
|----------|--------|
| Install model | Global tool (not clone-only) |
| Installer | `pipx install ai-router` |
| Distribution | Publish to PyPI (name availability is a **snapshot** — recheck normalized name at release time) |
| Packaging | Keep Poetry for maintainers; no hatch/uv migration |
| CLI command | `ai-router` (not `ai` — too short / collision-prone) |
| Cursor MCP | stdio direct spawn (not `mcp-remote` + HTTP as primary) |

## End-user experience

**Prerequisite:** package must be published to PyPI first (see [First release checklist](#first-release-checklist)). Until then, `pipx install ai-router` will fail for end users.

```bash
# one-time (module invocation avoids PATH issues right after pip install)
python -m pip install --user pipx
python -m pipx ensurepath
# restart terminal
pipx install ai-router
```

Windows may use `py -m pip` / `py -m pipx`. For other install methods, link to [pipx installation docs](https://pipx.pypa.io/stable/installation/) on error.

Upgrade: `pipx upgrade ai-router`.

On first browser launch, CloakBrowser downloads a stealth Chromium binary (~200 MB) to `~/.cloakbrowser/`. Users do **not** run `playwright install`.

### Cursor MCP config (recommended: full path)

GUI apps (Cursor on Windows/macOS) often use a PATH that differs from the shell. **Prefer the exact path returned by the system** in `mcp.json`:

```bash
# Windows
where ai-router

# macOS/Linux
command -v ai-router
```

```json
{
  "mcpServers": {
    "ai-router": {
      "command": "/full/path/from/where-or-command-v",
      "args": ["serve"]
    }
  }
}
```

**Example only** (actual path depends on `PIPX_BIN_DIR` and OS):

- Windows: `C:\\Users\\<you>\\.local\\bin\\ai-router.exe`
- macOS/Linux: `~/.local/bin/ai-router`

If bare `command: ai-router` works in your environment, that is fine — but README should present full path as the **recommended** config.

No separate terminal for the server. No Node/`mcp-remote` in the happy path. Login remains CLI-only (`ai-router browser login`).

## Maintainer experience

Unchanged day-to-day:

- `poetry install`
- `poetry run pytest` / `poetry run ruff ...`
- Release: see [First release checklist](#first-release-checklist)

Poetry stays an internal build/dev tool. It must not appear in the user Install section of the README.

## Packaging changes

All metadata stays under **`[tool.poetry]`** (minimal change — do not mix `[project]` fields without verifying which Poetry version reads as canonical).

1. Rename console script entry:

   ```toml
   [tool.poetry]
   name = "ai-router"
   version = "0.1.0"
   description = "MCP server routing prompts to web AI providers via CloakBrowser"
   authors = ["Maintainer Name <maintainer@example.com>"]  # real author, no placeholder
   license = "MIT"
   readme = "README.md"
   homepage = "https://github.com/scriptkid23/ai-router"
   repository = "https://github.com/scriptkid23/ai-router"
   keywords = ["mcp", "gemini", "ai", "cursor"]
   packages = [{ include = "ai_router", from = "src" }]
   classifiers = [
     "Programming Language :: Python :: 3",
     "Programming Language :: Python :: 3.11",
     "License :: OSI Approved :: MIT License",
     "Operating System :: OS Independent",
   ]

   [tool.poetry.dependencies]
   python = ">=3.11,<4"
   mcp = ">=1.6,<2"   # see Dependency pinning below
   # ... other deps unchanged

   [tool.poetry.scripts]
   ai-router = "ai_router.cli.main:app"
   ```

   The **console script key** `ai-router` creates the `ai-router` executable. This is what matters for pipx — not the Typer `name=`.

2. Add `LICENSE` file (MIT) if absent.

3. Rename Typer app in `src/ai_router/cli/main.py` from `name="ai"` to `name="ai-router"`. **Cosmetic only** — affects `--help` banner, not the executable name.

4. Add top-level `ai-router --version` (from `importlib.metadata.version("ai-router")`) for support/debug.

5. Do **not** keep a second `ai` script alias in v1.

6. **Reserve PyPI name early:** publish `0.0.1` (or first real version) early in implementation. Recheck normalized name immediately before production publish.

### Dependency pinning (`mcp`)

`mcp = "^1.6"` in Poetry does **not** mean end users run MCP 1.6. pipx installs resolve dependencies from PyPI independently of `poetry.lock`.

Requirement:

```toml
mcp = ">=1.6,<2"
```

After spike, raise minimum to the lowest version actually verified (e.g. `>=1.12,<2` if 1.6 is untested). CI must test against:

- The version locked in maintainer `poetry.lock` (currently `1.12.4`).
- Latest stable `mcp` `<2`.
- A clean wheel install (not dev checkout).

MCP v2 is a breaking line — upper bound `<2` is mandatory.

## MCP transport: stdio primary, HTTP retained

Current code hardcodes HTTP:

```python
# src/ai_router/mcp/server.py
mcp.run(transport="streamable-http")
```

### Required behavior

Use a `Transport` enum (`stdio` | `http`), not free-form strings:

```python
class Transport(str, Enum):
    STDIO = "stdio"
    HTTP = "http"
```

| Flag | Default | Behavior |
|------|---------|----------|
| `--transport stdio` | **yes** | `mcp.run(transport="stdio")` — Cursor spawn |
| `--transport http` | no | `mcp.run(transport="streamable-http")` on `host`/`port` |

CLI:

```text
ai-router serve [--transport stdio|http] [--host 127.0.0.1] [--port 8087]
```

- For `stdio`, `--host` / `--port` are ignored (no error); they only apply to `http`.
- `create_mcp_app(host, port)` may use dummy values for stdio or refactor signature — stdio must not bind TCP.
- Invalid transport values rejected by Typer.

Example implementation pattern:

```python
if transport is Transport.STDIO:
    mcp.run(transport="stdio")
    return

typer.echo(f"Starting ai-router HTTP server on {host}:{port}", err=True)
mcp.run(transport="streamable-http")
```

HTTP diagnostic messages go to **stderr** (`err=True`). Stdio has **no startup banner at all** — Cursor owns the process; users do not read a terminal.

### Stdio stdout discipline (blocker)

With stdio transport, **stdout is the MCP JSON-RPC channel**. Any non-protocol bytes corrupt framing.

Required when `--transport stdio`:

- **No startup banner to stdout** — best option: print nothing on stdio startup.
- **No** `print()`, default `typer.echo()`, or logging handlers on stdout during `serve`.
- Route all logs to **stderr** or log file. Today `ai_router.logger.configure()` uses `StreamHandler(sys.stderr)` — keep that.
- **Remove** `serve_cmd`'s current `typer.echo(...)` for stdio (today it writes to stdout).
- Audit full startup path: import-time output, browser init warnings, dependency debug prints.

`browser login` may use `typer.echo` — separate CLI command, not Cursor-spawned.

Acceptance:

```text
Starting the stdio server must produce no non-protocol bytes on stdout.
Logs may be written to stderr.
```

### Working directory independence

MCP hosts may spawn stdio servers with an unpredictable cwd (sometimes `/`). Config and user data must not depend on repo root or cwd.

**Current codebase audit (2026-07-10):** config uses `Path.home() / ".ai-router"`; selectors are Python constants; no bundled JSON/JS assets under `src/ai_router/`. **Low risk today**, but wheel smoke must still run from an **empty directory outside the repo**:

```bash
mkdir /tmp/ai-router-smoke && cd /tmp/ai-router-smoke
ai-router --help
ai-router browser status
```

If future code adds package data (templates, injection scripts), use `importlib.resources` and verify files ship in the wheel.

### Process lifecycle: Cursor respawn vs in-memory state

**Conversation context:** `ask` is **stateless** — each call opens a fresh provider chat. `mcp_session_id` is optional, trace-only. No per-tab chat mapping required for stdio.

**In-memory runtime state:** browser manager, pinned tabs, page workers live in the MCP process. Cursor respawn = cold start; first `ask` slower.

| Persists across respawn | Lost on respawn |
|-------------------------|-----------------|
| Google login (`~/.ai-router/profile/`) | Warm browser tabs / workers |
| Config (`~/.ai-router/config.yaml`) | In-flight jobs |
| CloakBrowser binary (`~/.cloakbrowser/`) | Page queue depth |

**Out of scope for v1:** persisting in-memory worker state across restarts.

### Multi-process / shared Chrome profile (spike required)

Multiple Cursor windows or MCP reloads may spawn multiple `ai-router serve` processes sharing `~/.ai-router/profile/`. Chromium persistent profiles can profile-lock or corrupt session state under concurrent access.

Spike before release:

```text
Start two independent ai-router serve --transport stdio processes
with the same profile_dir; observe lock/conflict behavior.
Verify Cursor terminate leaves no orphan Chrome processes.
```

If concurrent instances are unsupported, pick one: file lock + clear error, document single active server, or separate runtime profile from login data. Does not block v1 if Cursor typically runs one process — but must be verified.

### HTTP / mcp-remote

Advanced/troubleshooting only:

```bash
ai-router serve --transport http
# optional: mcp-remote → http://127.0.0.1:8087/mcp  (requires Node.js)
```

### Implementation spike

Hard gates before merge:

1. `mcp.run(transport="stdio")` on dependency range end users will actually resolve (`>=X,<2`), not only maintainer lock.
2. Stdio stdout-clean (pipe test).
3. cwd-independent wheel smoke from empty directory.

## Documentation

Split README into two audiences.

### Install (users)

Prerequisites — **only**: Python 3.11+, pipx, Chrome (stable).

Content: `pipx install`, CloakBrowser download note, login, full-path Cursor stdio config, `ai-router --version`.

**Fix conversation behavior** — replace outdated session wording with:

> Each `ask` opens a **new** provider chat. Follow-up context is not preserved across calls. Cursor conversation context and provider chat context are separate; ai-router does not reuse the previous provider chat. Browser login (Google session) is persistent via `~/.ai-router/profile/`.

Do not mention `mcp_session_id` in user-facing docs.

Replace all `poetry run ai` / bare `ai` with `ai-router`.

### Develop (maintainers)

Poetry, tests, publish, `poetry run ai-router` during local dev.

## First release checklist

1. Finalize `mcp` dependency range with `<2` upper bound; spike stdio on resolved versions.
2. Add `LICENSE`, complete `[tool.poetry]` metadata, `python = ">=3.11,<4"`.
3. Rename console entry to `ai-router`; no `ai` alias.
4. Implement `Transport` enum; default stdio; no stdio startup banner.
5. Add `ai-router --version`.
6. Audit stdio stdout discipline and cwd-independent paths.
7. `poetry check` → `poetry build`.
8. Inspect wheel: `entry_points.txt` has only `ai-router = ai_router.cli.main:app` (no `ai` alias).
9. **Local wheel smoke (primary):**

   ```bash
   poetry build
   pipx install --force dist/ai_router-*.whl
   cd /tmp/empty-dir
   ai-router --help
   ai-router --version
   ai-router browser status
   ```

   Dependencies resolve from **production PyPI**, not TestPyPI.

10. Stdio MCP integration: initialize, `list_tools`, `list_providers` (MCP Inspector or Cursor).
11. HTTP regression: `--transport http` + `/mcp` connection.
12. Multi-process profile spike + orphan Chrome check.
13. `poetry publish -r testpypi` — inspect metadata/README render on TestPyPI (publish pipeline check, **not** primary install smoke).
14. Recheck PyPI normalized name `ai-router` immediately before production publish.
15. `poetry publish` to production PyPI; `git tag vX.Y.Z`.
16. Clean-machine test: Windows + one Unix OS, `pipx install ai-router`, Cursor spawn with full path.

Subsequent releases: bump version → `git tag` → `poetry build` → local wheel smoke → `poetry publish` → users `pipx upgrade ai-router`.

## Error handling / UX notes

| Problem | Guidance |
|---------|----------|
| `pipx: command not found` right after install | Use `python -m pipx` instead |
| `ai-router: command not found` | `python -m pipx ensurepath` + new terminal |
| Cursor cannot find `ai-router` | Paste exact path from `where ai-router` / `command -v ai-router` |
| Cursor MCP fails mysteriously | Stdio stdout pollution — logs must be stderr-only; no startup banner |
| `pipx install ai-router` fails | Not on PyPI yet, or name taken — see release checklist |
| Not logged in | `ai-router browser login` |
| Slow first `ask` after Cursor restart | Expected cold start; use HTTP long-running if warm tabs matter |
| Profile lock / browser errors | Possible concurrent MCP processes — see multi-process spike |
| Need HTTP debug | `ai-router serve --transport http` |

## Testing (three tiers)

### Unit tests

- Default transport is `stdio`.
- `--transport http` selects `streamable-http` with host/port.
- Stdio does not bind TCP; host/port ignored.
- Invalid transport rejected by Typer.
- Stdio startup writes **no banner** to stdout.
- `entry_points` / packaging helpers if needed.

### Built-wheel smoke (clean environment)

1. `poetry build`
2. `pipx install --force dist/*.whl`
3. Run from directory **outside repository** (empty `/tmp` dir)
4. `ai-router --help`, `ai-router --version`
5. `ai-router browser status`
6. Inspect wheel `entry_points.txt` — only `ai-router`, no `ai` alias
7. Stdio server starts; stdout has no non-protocol bytes before MCP handshake

### MCP integration

- Spawn via stdio client (Inspector or test harness)
- Complete MCP initialize
- `list_tools`, `list_providers`
- Terminate client; server exits cleanly
- No orphan Chrome/server processes
- HTTP `/mcp` regression

## Out of scope

- Standalone binary / Windows `.msi`
- Homebrew / scoop formulas
- Migrating off Poetry to uv/hatch
- Keeping `ai` as CLI alias
- Changing browser login to an MCP tool
- Reintroducing per-session / per-tab conversation continuity
- Persisting in-memory browser/worker state across MCP process restarts
- Guaranteed safe concurrent multi-process access to one Chrome profile (unless spike proves otherwise — then document or lock)

## Success criteria

- User with Python + pipx installs from PyPI and runs `ai-router` without Poetry.
- Cursor connects via stdio with full-path `command` and `args: ["serve"]`.
- Stdio: **zero** non-protocol stdout output at startup; logs on stderr/file only.
- `ai-router --version` reports installed package version.
- Wheel smoke passes from empty directory outside repo.
- `mcp` dependency range tested on versions end users actually resolve.
- Maintainers publish with Poetry; releases git-tagged.
- HTTP transport remains for debugging.
- README describes stateless `ask` accurately; no Poetry/Node.js in primary path.
