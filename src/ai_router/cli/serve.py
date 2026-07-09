from __future__ import annotations

from typing import Annotated

import typer

from ai_router.config import load_config
from ai_router.mcp.server import run_server


def serve_cmd(
    host: Annotated[str | None, typer.Option(help="Bind host")] = None,
    port: Annotated[int | None, typer.Option(help="Bind port")] = None,
) -> None:
    """Start the MCP HTTP/SSE server."""
    cfg = load_config()
    typer.echo(f"Starting ai-router on {host or cfg.host}:{port or cfg.port}")
    run_server(host=host, port=port)
