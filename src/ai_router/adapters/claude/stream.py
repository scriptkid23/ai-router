from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from ai_router.adapters.claude.selectors import RATE_LIMIT_MARKERS
from ai_router.browser.profile import StreamDone

_RATE_LIMIT_STATUSES = (401, 403, 429)


def _iter_data_payloads(body: str) -> Iterator[dict[str, Any]]:
    """Yield each JSON object carried on a `data:` SSE line."""
    for raw in body.splitlines():
        line = raw.strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:") :].strip()
        if not payload:
            continue
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            yield data


def _is_out_of_quota(data: dict[str, Any]) -> bool:
    if data.get("type") != "message_limit":
        return False
    ml = data.get("message_limit")
    if not isinstance(ml, dict):
        return False
    if ml.get("type") not in (None, "within_limit"):
        return True
    windows = ml.get("windows")
    if isinstance(windows, dict):
        for window in windows.values():
            if isinstance(window, dict) and window.get("status") not in (
                None,
                "within_limit",
            ):
                return True
    resolved = ml.get("resolved")
    if isinstance(resolved, dict) and resolved.get("status") not in (None, "ok"):
        return True
    return False


def parse_stream_done(status: int, body: str) -> StreamDone:
    """Classify a finished claude.ai /completion SSE body.

    Success requires message_stop or message_delta with stop_reason end_turn.
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

    saw_end_turn = False
    saw_message_stop = False

    for data in _iter_data_payloads(body):
        if _is_out_of_quota(data):
            return StreamDone(
                done=True,
                ok=False,
                error_kind="rate_limit",
                error_text="Claude usage limit reached",
            )
        dtype = data.get("type")
        if dtype == "message_stop":
            saw_message_stop = True
        elif dtype == "message_delta":
            delta = data.get("delta")
            if isinstance(delta, dict) and delta.get("stop_reason") == "end_turn":
                saw_end_turn = True

    if saw_message_stop or saw_end_turn:
        return StreamDone(done=True, ok=True)
    return StreamDone(done=False, ok=False)
