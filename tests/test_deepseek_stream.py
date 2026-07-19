from ai_router.adapters.deepseek.stream import parse_stream_done


def _sse(*lines: str) -> str:
    return "\n".join(lines)


def test_close_after_finished_is_success():
    body = _sse(
        'data: {"p":"response/status","o":"SET","v":"FINISHED"}',
        "event: close",
        'data: {"click_behavior":"none","auto_resume":false}',
    )
    result = parse_stream_done(200, body)
    assert result.done is True
    assert result.ok is True
    assert result.error_kind is None


def test_finished_without_close_not_done():
    body = _sse(
        'data: {"p":"response/status","o":"SET","v":"FINISHED"}',
    )
    result = parse_stream_done(200, body)
    assert result.done is False
    assert result.ok is False


def test_batch_quasi_finished_plus_close_is_success():
    body = _sse(
        'data: {"p":"response","o":"BATCH","v":[{"p":"quasi_status","v":"FINISHED"}]}',
        "event: close",
        'data: {}',
    )
    result = parse_stream_done(200, body)
    assert result.done is True
    assert result.ok is True


def test_status_error_is_failure():
    body = _sse(
        'data: {"p":"response/status","o":"SET","v":"ERROR"}',
    )
    result = parse_stream_done(200, body)
    assert result.done is True
    assert result.ok is False
    assert result.error_kind == "error"


def test_close_without_finished_not_done():
    body = _sse(
        "event: close",
        'data: {"click_behavior":"none"}',
    )
    result = parse_stream_done(200, body)
    assert result.done is False
    assert result.ok is False


def test_think_only_partial_not_done():
    body = _sse(
        'data: {"v":{"response":{"fragments":[{"type":"THINK","content":"We"}]}}}',
        'data: {"p":"response/fragments/-1/content","o":"APPEND","v":" think"}',
    )
    result = parse_stream_done(200, body)
    assert result.done is False
    assert result.ok is False


def test_http_429_is_rate_limit():
    result = parse_stream_done(429, "too many requests")
    assert result.done is True
    assert result.ok is False
    assert result.error_kind == "rate_limit"
