from ai_router.adapters.gemini.wait import (
    braces_balanced,
    extract_stream_answer,
    is_rate_limited,
    is_stream_end,
)


def test_braces_balanced_true():
    assert braces_balanced('{"a": 1}') is True


def test_braces_balanced_false():
    assert braces_balanced('{"a": 1') is False


def test_braces_balanced_no_braces():
    assert braces_balanced("hello world") is True


def test_rate_limit_english():
    assert is_rate_limited("Too many requests, try again later") is True


def test_rate_limit_vietnamese():
    assert is_rate_limited("Bạn đã đạt đến giới hạn, thử lại sau") is True


def test_rate_limit_negative():
    assert is_rate_limited("Here is a normal answer about Python") is False


def test_stream_end_marker_detected():
    body = """]
]26[
    [
        "e",
        9,
        null,
        null,
        5347
    ]
]"""
    assert is_stream_end(body) is True


def test_stream_end_marker_negative():
    assert is_stream_end('["rc_123", "some chunk"]') is False


def test_extract_stream_answer_finds_longest_text():
    body = (
        'null "short" '
        '"This is the final Gemini answer with enough length." '
        '["e", null, null, null]'
    )
    text = extract_stream_answer(body)
    assert text == "This is the final Gemini answer with enough length."
