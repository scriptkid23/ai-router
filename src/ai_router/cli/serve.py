from __future__ import annotations

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
