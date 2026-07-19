from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from ai_router.adapters.deepseek.selectors import FAILURE_STATUSES, RATE_LIMIT_MARKERS
from ai_router.browser.profile import StreamDone

_RATE_LIMIT_STATUSES = (401, 403, 429)


def _iter_sse_events(body: str) -> Iterator[tuple[str | None, dict[str, Any] | None]]:
    """Yield (event_name, data_payload) pairs from an SSE body."""
    current_event: str | None = None
    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("event:"):
            current_event = line[len("event:") :].strip()
            continue
        if line.startswith("data:"):
            payload = line[len("data:") :].strip()
            data: dict[str, Any] | None = None
            if payload:
                try:
                    parsed = json.loads(payload)
                    if isinstance(parsed, dict):
                        data = parsed
                except json.JSONDecodeError:
                    pass
            yield current_event, data
            current_event = None


def _patch_failure(data: dict[str, Any]) -> bool:
    if data.get("o") != "SET":
        return False
    path = data.get("p")
    value = data.get("v")
    if path == "response/status" and isinstance(value, str):
        return value.upper() in FAILURE_STATUSES
    return False


def _patch_finished(data: dict[str, Any]) -> bool:
    if data.get("o") == "SET":
        if data.get("p") == "response/status" and data.get("v") == "FINISHED":
            return True
    if data.get("o") == "BATCH" and isinstance(data.get("v"), list):
        for item in data["v"]:
            if isinstance(item, dict) and item.get("v") == "FINISHED":
                if item.get("p") in ("quasi_status", "response/status", "status"):
                    return True
    return False


def parse_stream_done(status: int, body: str) -> StreamDone:
    """Classify a finished chat.deepseek.com /completion SSE body.

    Success requires a FINISHED status signal AND event: close.
    Answer text is read from the DOM by StateReducer — not from this parser.
    """
    if status >= 400:
        lower = body.lower()
        if status in _RATE_LIMIT_STATUSES or any(
            m in lower for m in RATE_LIMIT_MARKERS
        ):
            return StreamDone(
                done=True,
                ok=False,
                error_kind="rate_limit",
                error_text=f"HTTP {status}: {body[:200]}",
            )
        return StreamDone(
            done=True,
            ok=False,
            error_kind="error",
            error_text=f"HTTP {status}: {body[:200]}",
        )

    saw_finished = False
    saw_close = False

    for event_name, data in _iter_sse_events(body):
        if event_name == "close":
            saw_close = True
        if data is None:
            continue
        if _patch_failure(data):
            return StreamDone(
                done=True,
                ok=False,
                error_kind="error",
                error_text=f"Stream status: {data.get('v')}",
            )
        if _patch_finished(data):
            saw_finished = True

    if saw_close and saw_finished:
        return StreamDone(done=True, ok=True)
    if saw_close and not saw_finished:
        return StreamDone(done=False, ok=False)
    return StreamDone(done=False, ok=False)
