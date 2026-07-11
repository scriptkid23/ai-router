"""Launch CloakBrowser persistent context via Playwright async API."""

from __future__ import annotations

from typing import Any

from cloakbrowser.config import get_default_stealth_args
from cloakbrowser.download import ensure_binary
from playwright.async_api import BrowserContext, async_playwright


async def launch_persistent_context_async(
    user_data_dir: str,
    *,
    headless: bool = False,
    **kwargs: Any,
) -> BrowserContext:
    """Open a persistent browser profile using the CloakBrowser stealth binary."""
    pw = await async_playwright().start()
    binary_path = ensure_binary()
    chrome_args = list(get_default_stealth_args())

    ctx = await pw.chromium.launch_persistent_context(
        user_data_dir,
        executable_path=binary_path,
        headless=headless,
        args=chrome_args,
        ignore_default_args=["--enable-automation"],
        **kwargs,
    )

    original_close = ctx.close

    async def _close_with_cleanup() -> None:
        await original_close()
        await pw.stop()

    ctx.close = _close_with_cleanup  # type: ignore[method-assign]
    return ctx
