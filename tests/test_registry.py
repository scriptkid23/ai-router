from ai_router.adapters.registry import build_registry


def test_build_registry_includes_deepseek():
    registry = build_registry()
    ids = [a.id for a in registry.list_all()]
    assert "deepseek" in ids


def test_build_registry_includes_kimi():
    registry = build_registry()
    ids = [a.id for a in registry.list_all()]
    assert "kimi" in ids


def test_kimi_adapter_is_available():
    registry = build_registry()
    kimi = registry.get("kimi")
    assert kimi.status == "available"
    assert kimi.name == "Kimi"


def test_kimi_profile_reads_response_bytes():
    from ai_router.config import load_config

    registry = build_registry()
    profile = registry.get("kimi").build_profile(load_config())
    assert profile.read_response_bytes is True
    assert profile.answer_timeout_s == 600.0


def test_deepseek_adapter_is_available():
    registry = build_registry()
    ds = registry.get("deepseek")
    assert ds.status == "available"
    assert ds.name == "DeepSeek"


def test_deepseek_profile_supports_search_waits():
    from ai_router.config import load_config

    registry = build_registry()
    profile = registry.get("deepseek").build_profile(load_config())
    assert profile.generating_start_timeout_s == 120.0
    assert profile.after_submit is not None
    assert profile.is_generating_started is not None
