from ai_router.config import load_config


def test_load_config_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("AI_ROUTER_DEFAULT_PROVIDER", raising=False)
    cfg = load_config()
    assert cfg.default_provider == "gemini"
    assert cfg.port == 8087
    assert cfg.host == "127.0.0.1"
    assert cfg.answer_timeout_s == 120
    assert "gemini" in cfg.providers


def test_env_override_default_provider(monkeypatch):
    monkeypatch.setenv("AI_ROUTER_DEFAULT_PROVIDER", "gemini")
    cfg = load_config()
    assert cfg.default_provider == "gemini"


def test_env_override_port(monkeypatch):
    monkeypatch.setenv("AI_ROUTER_PORT", "9090")
    cfg = load_config()
    assert cfg.port == 9090


def test_browser_queue_defaults():
    cfg = load_config()
    assert cfg.idle_streak_required == 6
    assert cfg.generating_streak_required == 2
    assert cfg.answer_stable_ticks == 4
    assert cfg.dom_tick_interval_ms == 500
    assert cfg.max_pages == 10
