from ai_router.adapters.kimi.wait import is_rate_limited


def test_is_rate_limited_short_error_text():
    assert is_rate_limited("Rate limit exceeded") is True


def test_is_rate_limited_ignores_long_discussion():
    text = "rate limit " + ("x" * 500)
    assert is_rate_limited(text) is False
