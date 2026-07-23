import json

from ai_router.adapters.kimi.stream import parse_stream_done


def _connect(*frames: dict, end_stream: bool = False) -> bytes:
    out = bytearray()
    for i, obj in enumerate(frames):
        payload = json.dumps(obj).encode("utf-8")
        flags = 0x80 if end_stream and i == len(frames) - 1 else 0x00
        out.append(flags)
        out.extend(len(payload).to_bytes(4, "big"))
        out.extend(payload)
    return bytes(out)


def test_completed_status_is_success():
    body = _connect({"status": "MESSAGE_STATUS_COMPLETED"})
    result = parse_stream_done(200, body)
    assert result.done is True
    assert result.ok is True


def test_nested_message_status_completed():
    body = _connect({"message": {"status": "MESSAGE_STATUS_COMPLETED"}})
    result = parse_stream_done(200, body)
    assert result.done is True
    assert result.ok is True


def test_failed_status_is_error():
    body = _connect({"status": "MESSAGE_STATUS_FAILED"})
    result = parse_stream_done(200, body)
    assert result.done is True
    assert result.ok is False
    assert result.error_kind == "error"


def test_partial_stream_not_done():
    body = _connect({"status": "MESSAGE_STATUS_RUNNING"})
    result = parse_stream_done(200, body)
    assert result.done is False


def test_http_429_is_rate_limit():
    result = parse_stream_done(429, b"too many requests")
    assert result.done is True
    assert result.ok is False
    assert result.error_kind == "rate_limit"


def test_substring_fallback_on_plain_text():
    text = b'{"status":"MESSAGE_STATUS_COMPLETED"}'
    result = parse_stream_done(200, text)
    assert result.done is True
    assert result.ok is True
