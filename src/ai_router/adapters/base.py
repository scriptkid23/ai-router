from __future__ import annotations

from enum import Enum
from typing import Literal, Protocol

from playwright.async_api import Page


class SessionStatus(str, Enum):
    LOGGED_IN = "logged_in"
    LOGGED_OUT = "logged_out"
    UNKNOWN = "unknown"


ProviderStatus = Literal["available", "coming_soon"]


class ProviderAdapter(Protocol):
    id: str
    name: str
    keywords: list[str]
    status: ProviderStatus

    async def check_session(self, page: Page) -> SessionStatus: ...
    async def open_new_chat(self, page: Page) -> None: ...
    async def ask(self, page: Page, prompt: str, *, timeout_s: int) -> str: ...
