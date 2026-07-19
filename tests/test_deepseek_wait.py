from ai_router.adapters.deepseek.wait import is_rate_limited


def test_rate_limit_english():
    assert is_rate_limited("Rate limit exceeded, try again later") is True


def test_rate_limit_negative():
    assert is_rate_limited("The answer is 42") is False
