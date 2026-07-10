from __future__ import annotations

import asyncio
from typing import Annotated

import typer
from playwright.async_api import Error as PlaywrightError

from ai_router.adapters.registry import build_registry
from ai_router.browser.cloak import launch_persistent_context_async
from ai_router.config import load_config

browser_app = typer.Typer(help="Browser login and session management")


async def _login(provider: str | None) -> None:
    cfg = load_config()
    cfg.profile_dir.mkdir(parents=True, exist_ok=True)
    registry = build_registry()
    targets = (
        [registry.get(provider)]
        if provider
        else [a for a in registry.list_all() if a.status == "available"]
    )

    typer.echo("Launching browser...")
    ctx = await launch_persistent_context_async(
        str(cfg.profile_dir),
        headless=False,
    )
    try:
        for adapter in targets:
            page = await ctx.new_page()
            url = cfg.providers[adapter.id].url
            typer.echo(f"Opening {adapter.name}: {url}")
            await page.goto(url, wait_until="domcontentloaded")
        typer.echo("Log in to each provider, then close all browser windows...")
        while ctx.pages:
            await asyncio.sleep(0.5)
    finally:
        try:
            await ctx.close()
        except PlaywrightError:
            pass


@browser_app.command("login")
def login(
    provider: Annotated[
        str | None, typer.Option(help="Provider id (default: all available)")
    ] = None,
) -> None:
    """Open headed browser for manual login. Close window when done."""
    asyncio.run(_login(provider))


@browser_app.command("status")
def status(
    provider: Annotated[str | None, typer.Option(help="Provider id (default: all)")] = None,
) -> None:
    """Check login status for provider(s)."""
    from ai_router.mcp.tools import create_app_state, handle_session_status

    async def _run() -> dict:
        state = create_app_state()
        return await handle_session_status(state, provider=provider)

    result = asyncio.run(_run())
    for pid, st in result.items():
        typer.echo(f"{pid}: {st}")
