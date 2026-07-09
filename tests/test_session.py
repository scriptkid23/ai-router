from ai_router.session.manager import SessionManager


def test_normalize_chat_url_with_slug() -> None:
    url = "https://gemini.google.com/app/647cb443e1811fb1?hl=vi"
    assert (
        SessionManager.normalize_chat_url(url)
        == "https://gemini.google.com/app/647cb443e1811fb1"
    )


def test_normalize_chat_url_bare_app() -> None:
    assert SessionManager.normalize_chat_url("https://gemini.google.com/app") is None
