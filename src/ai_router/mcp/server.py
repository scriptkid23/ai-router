from mcp.server.fastmcp import Context, FastMCP

from ai_router.config import load_config
from ai_router.errors import AiRouterError
from ai_router.logger import configure
from ai_router.mcp.tools import (
    create_app_state,
    handle_ask,
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


def run_server(host: str | None = None, port: int | None = None) -> None:
    cfg = load_config()
    mcp = create_mcp_app(host or cfg.host, port or cfg.port)
    mcp.run(transport="streamable-http")
