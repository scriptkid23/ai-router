from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from ai_router.adapters.kimi.selectors import (
    COMPLETED_STATUS,
    FAILURE_STATUSES,
    RATE_LIMIT_MARKERS,
)
from ai_router.browser.profile import StreamDone

_RATE_LIMIT_STATUSES = (401, 403, 429)


def _as_bytes(body: str | bytes) -> bytes:
    if isinstance(body, bytes):
        return body
    return body.encode("utf-8", errors="replace")


def iter_connect_json_frames(raw: bytes) -> Iterator[dict[str, Any]]:
    offset = 0
    while offset + 5 <= len(raw):
        length = int.from_bytes(raw[offset + 1 : offset + 5], "big")
        offset += 5
        if offset + length > len(raw):
            break
        payload = raw[offset : offset + length]
        offset += length
        try:
            obj = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(obj, dict):
            yield obj


def _status_values(obj: dict[str, Any]) -> list[str]:
    out: list[str] = []
    val = obj.get("status")
    if isinstance(val, str):
        out.append(val)
    for nested in ("message", "result"):
        inner = obj.get(nested)
        if isinstance(inner, dict):
            nested_val = inner.get("status")
            if isinstance(nested_val, str):
                out.append(nested_val)
    return out


def _scan_objects(objects: list[dict[str, Any]]) -> StreamDone | None:
    saw_completed = False
    for obj in objects:
        for status in _status_values(obj):
            if status in FAILURE_STATUSES:
                return StreamDone(
                    done=True,
                    ok=False,
                    error_kind="error",
                    error_text=f"Stream status: {status}",
                )
            if status == COMPLETED_STATUS:
                saw_completed = True
    if saw_completed:
        return StreamDone(done=True, ok=True)
    return None


def parse_stream_done(status: int, body: str | bytes) -> StreamDone:
    if status >= 400:
        text = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else body
        lower = text.lower()
        if status in _RATE_LIMIT_STATUSES or any(m in lower for m in RATE_LIMIT_MARKERS):
            return StreamDone(
                done=True,
                ok=False,
                error_kind="rate_limit",
                error_text=f"HTTP {status}: {text[:200]}",
            )
        return StreamDone(
            done=True,
            ok=False,
            error_kind="error",
            error_text=f"HTTP {status}: {text[:200]}",
        )

    raw = _as_bytes(body)
    objects = list(iter_connect_json_frames(raw))
    if objects:
        verdict = _scan_objects(objects)
        if verdict is not None:
            return verdict
        return StreamDone(done=False, ok=False)

    text = raw.decode("utf-8", errors="replace")
    if COMPLETED_STATUS in text:
        return StreamDone(done=True, ok=True)
    for fail in FAILURE_STATUSES:
        if fail in text:
            return StreamDone(
                done=True,
                ok=False,
                error_kind="error",
                error_text=f"Stream status: {fail}",
            )
    return StreamDone(done=False, ok=False)
