# Global `ai-router` CLI via pipx + PyPI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** End users install once with `pipx install ai-router` and run `ai-router` globally — no Poetry, no repo clone — with Cursor connecting via stdio MCP transport.

**Architecture:** Keep Poetry as the maintainer-only build tool. Rename the console entry point to `ai-router`, add a `Transport` enum defaulting to stdio for Cursor spawn, and enforce stdout discipline during stdio serve (MCP JSON-RPC owns stdout). Publish a wheel to PyPI; users install via pipx.

**Tech Stack:** Python 3.11+, Poetry (maintainers), Typer, MCP SDK (`mcp >=1.6,<2`), pipx, pytest, ruff

**Spec:** `docs/superpowers/specs/2026-07-10-pipx-global-cli-design.md`

---

## File structure

| File | Responsibility |
|------|----------------|
| `LICENSE` | MIT license text (PyPI requirement) |
| `pyproject.toml` | Package metadata, `ai-router` console script, pinned `mcp` range |
| `src/ai_router/mcp/transport.py` | `Transport` enum (`stdio` \| `http`) — single source of truth |
| `src/ai_router/mcp/server.py` | `create_mcp_app()`, `run_server(transport=...)` — dispatches to stdio or streamable-http |
| `src/ai_router/cli/serve.py` | `serve` subcommand flags; no stdout banner on stdio |
| `src/ai_router/cli/main.py` | Top-level Typer app, `--version`, cosmetic `name="ai-router"` |
| `tests/test_transport.py` | Unit tests for transport enum parsing and server dispatch |
| `tests/test_cli_serve.py` | Unit tests for serve CLI defaults, flags, stdout discipline |
| `tests/test_cli_version.py` | Unit tests for `--version` |
| `tests/test_packaging.py` | Assert `pyproject.toml` entry point name (no `ai` alias) |
| `README.md` | Split Install (users) vs Develop (maintainers) |

**Unchanged (verify only):** `src/ai_router/logger.py` already logs to stderr; `src/ai_router/config.py` already uses `Path.home() / ".ai-router"` (cwd-independent).

---

### Task 1: Add MIT LICENSE

**Files:**
- Create: `LICENSE`

- [ ] **Step 1: Create LICENSE file**

```text
MIT License

Copyright (c) 2026 Olivier Taylor

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

- [ ] **Step 2: Commit**

```bash
git add LICENSE
git commit -m "chore: add MIT LICENSE"
```

---

### Task 2: Transport enum

**Files:**
- Create: `src/ai_router/mcp/transport.py`
- Create: `tests/test_transport.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_transport.py`:

```python
from ai_router.mcp.transport import Transport


def test_transport_values():
    assert Transport.STDIO.value == "stdio"
    assert Transport.HTTP.value == "http"


def test_transport_is_str_enum():
    assert isinstance(Transport.STDIO, str)
    assert Transport("stdio") is Transport.STDIO
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_transport.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'ai_router.mcp.transport'`

- [ ] **Step 3: Write minimal implementation**

Create `src/ai_router/mcp/transport.py`:

```python
from __future__ import annotations

from enum import Enum


class Transport(str, Enum):
    STDIO = "stdio"
    HTTP = "http"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_transport.py -v`

Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ai_router/mcp/transport.py tests/test_transport.py
git commit -m "feat: add Transport enum for MCP serve modes"
```

---

### Task 3: Server transport dispatch

**Files:**
- Modify: `src/ai_router/mcp/server.py`
- Modify: `tests/test_transport.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_transport.py`:

```python
from unittest.mock import MagicMock

import pytest

from ai_router.mcp.server import run_server
from ai_router.mcp.transport import Transport


@pytest.fixture
def fake_mcp(monkeypatch):
    instances: list[MagicMock] = []

    def factory(host: str, port: int):
        mcp = MagicMock()
        instances.append(mcp)
        return mcp

    monkeypatch.setattr("ai_router.mcp.server.create_mcp_app", factory)
    return instances


def test_run_server_stdio_calls_stdio_transport(fake_mcp):
    run_server(transport=Transport.STDIO)
    assert len(fake_mcp) == 1
    fake_mcp[0].run.assert_called_once_with(transport="stdio")


def test_run_server_http_calls_streamable_http(fake_mcp, capsys):
    run_server(host="127.0.0.1", port=9090, transport=Transport.HTTP)
    assert len(fake_mcp) == 1
    fake_mcp[0].run.assert_called_once_with(transport="streamable-http")
    captured = capsys.readouterr()
    assert "9090" in captured.err
    assert captured.out == ""


def test_run_server_stdio_no_stderr_banner(fake_mcp, capsys):
    run_server(transport=Transport.STDIO)
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_transport.py::test_run_server_stdio_calls_stdio_transport -v`

Expected: FAIL — `run_server()` got unexpected keyword argument `transport`

- [ ] **Step 3: Implement transport dispatch in server.py**

Replace `run_server` in `src/ai_router/mcp/server.py`:

```python
from ai_router.mcp.transport import Transport


def run_server(
    host: str | None = None,
    port: int | None = None,
    transport: Transport = Transport.STDIO,
) -> None:
    cfg = load_config()
    bind_host = host or cfg.host
    bind_port = port or cfg.port
    mcp = create_mcp_app(bind_host, bind_port)

    if transport is Transport.STDIO:
        mcp.run(transport="stdio")
        return

    import sys

    print(f"Starting ai-router HTTP server on {bind_host}:{bind_port}", file=sys.stderr)
    mcp.run(transport="streamable-http")
```

Add the import at the top of the file with the other imports:

```python
from ai_router.mcp.transport import Transport
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_transport.py -v`

Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ai_router/mcp/server.py tests/test_transport.py
git commit -m "feat: dispatch MCP serve to stdio or streamable-http"
```

---

### Task 4: Serve CLI with transport flag

**Files:**
- Modify: `src/ai_router/cli/serve.py`
- Create: `tests/test_cli_serve.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cli_serve.py`:

```python
from unittest.mock import MagicMock

from typer.testing import CliRunner

from ai_router.cli.main import app
from ai_router.mcp.transport import Transport

runner = CliRunner()


def test_serve_defaults_to_stdio(monkeypatch):
    captured: dict = {}

    def fake_run_server(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("ai_router.cli.serve.run_server", fake_run_server)
    result = runner.invoke(app, ["serve"])
    assert result.exit_code == 0
    assert captured["transport"] is Transport.STDIO
    assert result.stdout == ""


def test_serve_http_transport(monkeypatch):
    captured: dict = {}

    def fake_run_server(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("ai_router.cli.serve.run_server", fake_run_server)
    result = runner.invoke(app, ["serve", "--transport", "http", "--port", "9090"])
    assert result.exit_code == 0
    assert captured["transport"] is Transport.HTTP
    assert captured["port"] == 9090
    assert "9090" in result.stderr
    assert result.stdout == ""


def test_serve_invalid_transport_rejected():
    result = runner.invoke(app, ["serve", "--transport", "websocket"])
    assert result.exit_code != 0
    assert "websocket" in result.stdout.lower() or "invalid" in result.stdout.lower()


def test_serve_stdio_ignores_host_port(monkeypatch):
    captured: dict = {}

    def fake_run_server(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("ai_router.cli.serve.run_server", fake_run_server)
    result = runner.invoke(app, ["serve", "--host", "0.0.0.0", "--port", "9999"])
    assert result.exit_code == 0
    assert captured["transport"] is Transport.STDIO
    assert result.stdout == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_cli_serve.py -v`

Expected: FAIL — stdout not empty (current `typer.echo` banner) or missing `--transport` option

- [ ] **Step 3: Implement serve CLI**

Replace `src/ai_router/cli/serve.py`:

```python
from __future__ import annotations

import sys
from typing import Annotated

import typer

from ai_router.config import load_config
from ai_router.mcp.server import run_server
from ai_router.mcp.transport import Transport


def serve_cmd(
    transport: Annotated[
        Transport,
        typer.Option(help="MCP transport: stdio (Cursor) or http (debug)"),
    ] = Transport.STDIO,
    host: Annotated[str | None, typer.Option(help="Bind host (http only)")] = None,
    port: Annotated[int | None, typer.Option(help="Bind port (http only)")] = None,
) -> None:
    """Start the MCP server."""
    if transport is Transport.HTTP:
        cfg = load_config()
        bind_host = host or cfg.host
        bind_port = port or cfg.port
        typer.echo(f"Starting ai-router HTTP server on {bind_host}:{bind_port}", err=True)

    run_server(host=host, port=port, transport=transport)
```

Note: `typer.echo(..., err=True)` routes HTTP startup message to stderr. Stdio path prints nothing.

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_cli_serve.py -v`

Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ai_router/cli/serve.py tests/test_cli_serve.py
git commit -m "feat: serve CLI defaults to stdio transport"
```

---

### Task 5: Top-level `--version` and Typer rename

**Files:**
- Modify: `src/ai_router/cli/main.py`
- Create: `tests/test_cli_version.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli_version.py`:

```python
from typer.testing import CliRunner

from ai_router.cli.main import app

runner = CliRunner()


def test_version_flag():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "0.1.0"
    assert result.stdout.endswith("\n") or result.stdout == "0.1.0"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_cli_version.py -v`

Expected: FAIL — no such option `--version`

- [ ] **Step 3: Implement version callback and rename Typer app**

Replace `src/ai_router/cli/main.py`:

```python
import importlib.metadata

import typer

from ai_router.cli.browser import browser_app
from ai_router.cli.serve import serve_cmd


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(importlib.metadata.version("ai-router"))
        raise typer.Exit()


app = typer.Typer(name="ai-router", help="ai-router — web AI provider automation")


@app.callback()
def main(
    version: typer.Option[bool | None] = typer.Option(
        None,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show installed version and exit",
    ),
) -> None:
    """MCP server routing prompts to web AI providers via CloakBrowser."""
    pass


app.command("serve")(serve_cmd)
app.add_typer(browser_app, name="browser")

if __name__ == "__main__":
    app()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_cli_version.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ai_router/cli/main.py tests/test_cli_version.py
git commit -m "feat: add ai-router --version and rename Typer app"
```

---

### Task 6: Packaging metadata and console script rename

**Files:**
- Modify: `pyproject.toml`
- Create: `tests/test_packaging.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_packaging.py`:

```python
from pathlib import Path

import tomllib


def test_console_script_is_ai_router_only():
    data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    scripts = data["tool"]["poetry"]["scripts"]
    assert "ai-router" in scripts
    assert scripts["ai-router"] == "ai_router.cli.main:app"
    assert "ai" not in scripts


def test_mcp_dependency_has_upper_bound():
    deps = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))[
        "tool"
    ]["poetry"]["dependencies"]
    assert deps["mcp"] == ">=1.6,<2"


def test_python_requires_311_to_4():
    deps = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))[
        "tool"
    ]["poetry"]["dependencies"]
    assert deps["python"] == ">=3.11,<4"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_packaging.py -v`

Expected: FAIL — script key is `ai`, not `ai-router`; `mcp` is `^1.6`

- [ ] **Step 3: Update pyproject.toml**

Replace the `[tool.poetry]` and scripts sections in `pyproject.toml`:

```toml
[tool.poetry]
name = "ai-router"
version = "0.1.0"
description = "MCP server routing prompts to web AI providers via CloakBrowser"
authors = ["Olivier Taylor <olivier.taylor.work@gmail.com>"]
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
typer = { extras = ["all"], version = "^0.15" }
cloakbrowser = "^0.4.10"
mcp = ">=1.6,<2"
uvicorn = "^0.30"
pyyaml = "^6.0"

[tool.poetry.scripts]
ai-router = "ai_router.cli.main:app"
```

- [ ] **Step 4: Run tests and poetry check**

Run:

```bash
poetry lock --no-update
poetry check
poetry run pytest tests/test_packaging.py -v
```

Expected: `poetry check` reports no errors; 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml poetry.lock tests/test_packaging.py
git commit -m "chore: PyPI metadata and ai-router console script"
```

---

### Task 7: Stdio import-time stdout audit

**Files:**
- Modify: `tests/test_cli_serve.py` (add import smoke test)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli_serve.py`:

```python
def test_import_mcp_server_writes_nothing_to_stdout(capsys):
    import importlib

    import ai_router.mcp.server as server_mod

    importlib.reload(server_mod)
    captured = capsys.readouterr()
    assert captured.out == ""
```

- [ ] **Step 2: Run test**

Run: `poetry run pytest tests/test_cli_serve.py::test_import_mcp_server_writes_nothing_to_stdout -v`

Expected: PASS (logger uses stderr; no import-time prints today). If FAIL, fix the offending module — move output to stderr or remove it.

- [ ] **Step 3: Commit (only if fixes were needed)**

```bash
git add tests/test_cli_serve.py src/ai_router/
git commit -m "test: assert MCP server import does not write to stdout"
```

---

### Task 8: Full test suite and lint

**Files:**
- Verify: all `tests/`

- [ ] **Step 1: Run full test suite**

Run:

```bash
poetry run pytest -v
poetry run ruff check src tests
```

Expected: all tests PASS; ruff clean

- [ ] **Step 2: Commit any lint fixes**

```bash
git add -A
git commit -m "chore: lint fixes for pipx CLI work"
```

(Skip commit if nothing changed.)

---

### Task 9: README — Install (users) and Develop (maintainers)

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Replace README with split-audience content**

Replace `README.md` with user-first install docs. Key sections:

**Install (users)** — prerequisites: Python 3.11+, pipx, Chrome only (no Poetry, no Node):

```bash
python -m pip install --user pipx
python -m pipx ensurepath
# restart terminal
pipx install ai-router
ai-router --version
ai-router browser login
ai-router browser status
```

Cursor MCP config (recommended full path):

```bash
command -v ai-router   # macOS/Linux
where ai-router      # Windows
```

```json
{
  "mcpServers": {
    "ai-router": {
      "command": "/full/path/from-command-v",
      "args": ["serve"]
    }
  }
}
```

**Conversation behavior** (replace outdated session wording):

> Each `ask` opens a **new** provider chat. Follow-up context is not preserved across calls. Cursor conversation context and provider chat context are separate; ai-router does not reuse the previous provider chat. Browser login (Google session) is persistent via `~/.ai-router/profile/`.

Do **not** mention `mcp_session_id`. Replace all `poetry run ai` / bare `ai` with `ai-router`.

**Develop (maintainers):**

```bash
poetry install
poetry run pytest -v
poetry run ruff check src tests
poetry run ai-router serve --transport http   # local debug
```

Note CloakBrowser ~200 MB download on first browser launch to `~/.cloakbrowser/`.

HTTP debug (advanced):

```bash
ai-router serve --transport http
# optional: npx -y mcp-remote@latest http://127.0.0.1:8087/mcp
```

Troubleshooting table from spec (pipx PATH, full path for Cursor, stdio stdout pollution, profile lock).

- [ ] **Step 2: Verify no Poetry/Node in Install section**

Run: `grep -n "poetry\|Poetry\|npx\|mcp-remote" README.md`

Expected: matches only in Develop or Advanced/Troubleshooting sections, not in Install prerequisites

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: pipx install path and stdio Cursor config"
```

---

### Task 10: MCP stdio spike (dependency range)

**Files:**
- Verify: `src/ai_router/mcp/server.py`
- Possibly modify: `pyproject.toml` (raise `mcp` minimum after spike)

- [ ] **Step 1: Build wheel and install via pipx**

Run:

```bash
poetry build
pipx install --force dist/ai_router-*.whl
```

- [ ] **Step 2: Stdio stdout pipe test**

Run:

```bash
mkdir -p /tmp/ai-router-smoke && cd /tmp/ai-router-smoke
timeout 3 ai-router serve 2>/dev/null | head -c 1 | xxd
```

Expected: no bytes on stdout before timeout (empty pipe or process waits silently). Any non-empty stdout before MCP client connects is a **blocker**.

- [ ] **Step 3: cwd-independent smoke**

Run from `/tmp/ai-router-smoke`:

```bash
ai-router --help
ai-router --version
ai-router browser status
```

Expected: all succeed without repo checkout as cwd

- [ ] **Step 4: MCP stdio integration**

Use MCP Inspector or Cursor with full-path config:

```json
{ "command": "/path/to/ai-router", "args": ["serve"] }
```

Verify: initialize handshake, `list_tools`, `list_providers`.

- [ ] **Step 5: Raise mcp minimum if spike confirms**

If stdio only works on `>=1.12`, update `pyproject.toml`:

```toml
mcp = ">=1.12,<2"
```

Update `tests/test_packaging.py` assertion accordingly. Run `poetry lock` and commit.

- [ ] **Step 6: Commit spike results (if pyproject changed)**

```bash
git add pyproject.toml poetry.lock tests/test_packaging.py
git commit -m "chore: pin mcp minimum to verified stdio version"
```

---

### Task 11: HTTP transport regression

**Files:**
- Verify: `src/ai_router/cli/serve.py`, `src/ai_router/mcp/server.py`

- [ ] **Step 1: Start HTTP server**

Run:

```bash
ai-router serve --transport http --port 8087
```

Expected stderr: `Starting ai-router HTTP server on 127.0.0.1:8087`

- [ ] **Step 2: Connect to /mcp**

Run (separate terminal):

```bash
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8087/mcp
```

Expected: HTTP response (not connection refused). Optional: MCP Inspector over HTTP.

- [ ] **Step 3: No commit** (verification only unless bugs found)

---

### Task 12: Multi-process profile spike

**Files:**
- Verify: `src/ai_router/browser/profile.py`, `src/ai_router/config.py`

- [ ] **Step 1: Start two stdio servers**

Run two terminals:

```bash
ai-router serve --transport stdio
ai-router serve --transport stdio
```

Observe: profile lock errors, Chrome conflicts, or clean coexistence.

- [ ] **Step 2: Terminate and check orphans**

After killing both processes:

```bash
pgrep -fl "chrome|chromium|cloak" || true
```

Expected: no orphan browser processes from ai-router.

- [ ] **Step 3: Document outcome**

If concurrent instances fail: add a note to README Troubleshooting ("single active MCP server recommended") or implement file lock with clear error in a follow-up. Does not block v1 if Cursor typically runs one process.

- [ ] **Step 4: Commit docs only if spike findings require it**

```bash
git add README.md
git commit -m "docs: note single-process MCP server recommendation"
```

---

### Task 13: Wheel inspection and CI matrix note

**Files:**
- Verify: `dist/ai_router-*.whl`

- [ ] **Step 1: Inspect wheel entry points**

Run:

```bash
unzip -p dist/ai_router-*.whl ai_router-*.dist-info/entry_points.txt
```

Expected:

```text
[console_scripts]
ai-router = ai_router.cli.main:app
```

No `ai =` line.

- [ ] **Step 2: Add CI matrix documentation to Develop section (optional one-liner in README)**

Maintainers should test against:
- Locked `mcp` in `poetry.lock` (1.12.4)
- Latest stable `mcp` `<2`
- Clean wheel install from `/tmp`

- [ ] **Step 3: Commit if README updated**

---

### Task 14: PyPI release

**Files:**
- Verify: `pyproject.toml`, `LICENSE`, `README.md`

- [ ] **Step 1: Pre-publish checks**

Run:

```bash
poetry check
poetry build
poetry run pytest -v
```

- [ ] **Step 2: TestPyPI dry run**

Run:

```bash
poetry publish -r testpypi
```

Inspect metadata and README render on TestPyPI web UI.

- [ ] **Step 3: Recheck PyPI name availability**

Search https://pypi.org/project/ai-router/ — name must be available (normalized `ai-router`).

- [ ] **Step 4: Production publish**

Run:

```bash
poetry publish
git tag v0.1.0
git push origin v0.1.0
```

- [ ] **Step 5: Clean-machine verification**

On a machine without the repo:

```bash
pipx install ai-router
ai-router --version
```

Configure Cursor with full path from `command -v ai-router`.

---

## Spec coverage checklist (self-review)

| Spec requirement | Task |
|------------------|------|
| Console script `ai-router`, no `ai` alias | Task 6 |
| Typer cosmetic rename | Task 5 |
| `--version` from importlib.metadata | Task 5 |
| `Transport` enum, default stdio | Tasks 2–4 |
| HTTP retained for debug | Tasks 3–4, 11 |
| Stdio no stdout banner | Tasks 3–4, 10 |
| Logger on stderr | Task 7 (audit) |
| `mcp >=X,<2` pinning | Task 6, 10 |
| LICENSE MIT | Task 1 |
| Poetry metadata complete | Task 6 |
| README split Install/Develop | Task 9 |
| Stateless `ask` wording | Task 9 |
| Full-path Cursor config | Task 9 |
| Wheel smoke from empty dir | Task 10 |
| Stdio MCP integration | Task 10 |
| Multi-process spike | Task 12 |
| PyPI publish checklist | Task 14 |
| cwd independence | Task 10 |

**Out of scope (no tasks):** `ai` alias, Homebrew, standalone binary, persisting in-memory worker state, guaranteed concurrent profile access.

---

## Success criteria

- [ ] `pipx install ai-router` works from PyPI without Poetry
- [ ] Cursor connects via stdio with full-path `command` and `args: ["serve"]`
- [ ] Stdio startup: zero non-protocol bytes on stdout
- [ ] `ai-router --version` reports `0.1.0`
- [ ] Wheel smoke passes from `/tmp/ai-router-smoke`
- [ ] HTTP `--transport http` still works
- [ ] README accurate; no Poetry/Node in primary install path
