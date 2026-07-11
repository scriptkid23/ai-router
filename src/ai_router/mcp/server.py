from mcp.server.fastmcp import Context, FastMCP

from ai_router.config import load_config
from ai_router.errors import AiRouterError
from ai_router.logger import configure
from ai_router.mcp.transport import Transport
from ai_router.mcp.tools import (
    create_app_state,
    handle_ask,
    handle_ask_multi,
    handle_list_providers,
    handle_session_status,
)

configure()
_state = create_app_state()


def _mcp_session_id(ctx: Context) -> str | None:
    try:
        request = ctx.request_context.request
    except Exception:
        return None
    if request is None:
        return None
    headers = getattr(request, "headers", None)
    if headers is None:
        return None
    return headers.get("mcp-session-id") or headers.get("Mcp-Session-Id")


def create_mcp_app(host: str, port: int) -> FastMCP:
    mcp = FastMCP("ai-router", host=host, port=port)

    @mcp.tool()
    async def ask(ctx: Context, prompt: str, provider: str | None = None) -> dict:
        """Send a prompt to a web AI provider and return the raw text answer."""
        try:
            return await handle_ask(
                _state,
                prompt=prompt,
                provider=provider,
                mcp_session_id=_mcp_session_id(ctx),
            )
        except AiRouterError as exc:
            raise RuntimeError(f"[{exc.code}] {exc.message}") from exc

    @mcp.tool()
    async def ask_multi(
        ctx: Context,
        prompt: str,
        providers: list[str] | None = None,
        strategy: str | None = None,
    ) -> dict:
        """Send one prompt to several providers in parallel; return every answer.

        strategy: "all" (default; selected=null, client compares),
        "first" (earliest finisher), "longest" (longest non-error answer).
        """
        try:
            return await handle_ask_multi(
                _state,
                prompt=prompt,
                providers=providers,
                strategy=strategy,
                mcp_session_id=_mcp_session_id(ctx),
            )
        except AiRouterError as exc:
            raise RuntimeError(f"[{exc.code}] {exc.message}") from exc

    @mcp.tool()
    async def list_providers() -> dict:
        """List registered AI providers and their availability status."""
        return await handle_list_providers(_state)

    @mcp.tool()
    async def session_status(provider: str | None = None) -> dict:
        """Check whether providers have an active logged-in browser session."""
        try:
            return await handle_session_status(_state, provider=provider)
        except AiRouterError as exc:
            raise RuntimeError(f"[{exc.code}] {exc.message}") from exc

    return mcp


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

    mcp.run(transport="streamable-http")
