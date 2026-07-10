from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from playwright.async_api import Page


@dataclass(frozen=True)
class StreamDone:
    """Verdict from parsing one finished provider stream response.

    error_kind: None | "rate_limit" | "moderation" | "incomplete" | "error"
    """

    done: bool
    ok: bool
    error_kind: str | None = None
    error_text: str | None = None


@dataclass(frozen=True)
class ProviderSelectors:
    prompt_input: str
    submit_button: str


@dataclass
class ProviderProfile:
    """Everything provider-specific the browser layer needs, in one place."""

    provider_id: str
    stream_url_re: re.Pattern[str]
    parse_stream_done: Callable[[int, str], StreamDone]
    is_stop_visible: Callable[[Page], Awaitable[bool]]
    read_response_snapshot: Callable[[Page], Awaitable[tuple[int, str]]]
    is_rate_limited: Callable[[str], bool]
    submit_ready: Callable[[Page], Awaitable[bool]]
    planner: Any
    selectors: ProviderSelectors
    error_markers: tuple[str, ...]
    recoverable_codes: tuple[str, ...]
    answer_timeout_s: float | None = None
    # Optional second completion source: providers that stream the turn over a
    # WebSocket (e.g. ChatGPT conduit) return a verdict per frame; None = skip.
    parse_ws_frame: Callable[[str], StreamDone | None] | None = None
