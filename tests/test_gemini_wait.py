from ai_router.adapters.gemini.wait import braces_balanced, is_rate_limited


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
