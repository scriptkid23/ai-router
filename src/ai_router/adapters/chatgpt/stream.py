from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from ai_router.browser.profile import StreamDone

_RATE_LIMIT_STATUSES = (401, 403, 429)
_RATE_LIMIT_BODY_MARKERS = ("rate_limit", "too many requests")


def _iter_data_payloads(body: str) -> Iterator[dict[str, Any]]:
    """Yield each JSON object carried on a `data:` SSE line."""
    for raw in body.splitlines():
        line = raw.strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:") :].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            yield data


def _message_of(data: dict[str, Any]) -> dict[str, Any] | None:
    v = data.get("v")
    if isinstance(v, dict) and isinstance(v.get("message"), dict):
        return v["message"]
    return None


def _patch_ops(data: dict[str, Any]) -> list[dict[str, Any]]:
    if data.get("o") == "patch" and isinstance(data.get("v"), list):
        return [op for op in data["v"] if isinstance(op, dict)]
    return []


def _moderation_blocked(data: dict[str, Any]) -> bool:
    response = data.get("moderation_response")
    return isinstance(response, dict) and bool(response.get("blocked"))


def parse_stream_done(status: int, body: str) -> StreamDone:
    """Classify a finished /backend-api/f/conversation SSE body.

    Success requires message_stream_complete AND proof the final-channel
    assistant message ended well (finished_successfully + end_turn patch,
    or the last_token marker). Reasoning/system messages never count —
    they also carry finished_successfully but live outside channel "final".

    This is a signal-only verdict: the answer text itself is read from the
    DOM once StateReducer's hybrid gate (stream_end + quiet window + stable
    DOM text + stop button gone) is satisfied.
    """
    if status >= 400:
        lower = body.lower()
        if status in _RATE_LIMIT_STATUSES or any(
            m in lower for m in _RATE_LIMIT_BODY_MARKERS
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

    stream_complete = False
    saw_final_channel = False
    final_status_finished = False
    final_end_turn = False
    last_token_marker = False
    moderation = False
    error_text: str | None = None

    for data in _iter_data_payloads(body):
        dtype = data.get("type")
        if dtype == "message_stream_complete":
            stream_complete = True
            continue
        if dtype == "message_marker":
            if data.get("marker") == "last_token" and data.get("event") == "last":
                last_token_marker = True
            continue
        if dtype == "moderation":
            if _moderation_blocked(data):
                moderation = True
            continue
        if data.get("error") or data.get("error_code"):
            error_text = str(data.get("error") or data.get("error_code"))[:200]

        msg = _message_of(data)
        if msg is not None:
            author = msg.get("author") or {}
            if msg.get("channel") == "final" and author.get("role") == "assistant":
                saw_final_channel = True
                if msg.get("status") == "finished_successfully":
                    final_status_finished = True
                if msg.get("end_turn") is True:
                    final_end_turn = True
            continue

        if saw_final_channel:
            for op in _patch_ops(data):
                if (
                    op.get("p") == "/message/status"
                    and op.get("v") == "finished_successfully"
                ):
                    final_status_finished = True
                elif op.get("p") == "/message/end_turn" and op.get("v") is True:
                    final_end_turn = True

    if moderation:
        return StreamDone(
            done=True,
            ok=False,
            error_kind="moderation",
            error_text=error_text or "Blocked by moderation",
        )

    final_success = (final_status_finished and final_end_turn) or last_token_marker
    if stream_complete and final_success:
        return StreamDone(done=True, ok=True)
    if stream_complete:
        return StreamDone(
            done=True,
            ok=False,
            error_kind="incomplete",
            error_text=error_text or "Stream completed without a successful final message",
        )
    if error_text:
        return StreamDone(done=True, ok=False, error_kind="error", error_text=error_text)
    return StreamDone(done=False, ok=False)


def parse_ws_frame(payload: str) -> StreamDone | None:
    """Classify one conduit WebSocket frame.

    When the account/cluster streams with resume_with_websockets, the POST to
    /backend-api/f/conversation returns only a resume token and the turn is
    delivered over a WebSocket. The turn-end marker there is a message whose
    payload.type == "conversation-turn-complete". Errors surfaced in the UI are
    still caught by the DOM error markers, so a complete turn counts as ok.
    """
    if "conversation-turn-complete" not in payload:
        return None
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    items = data if isinstance(data, list) else [data]
    for item in items:
        if not isinstance(item, dict):
            continue
        inner = item.get("payload")
        if isinstance(inner, dict) and inner.get("type") == "conversation-turn-complete":
            return StreamDone(done=True, ok=True)
    return None
