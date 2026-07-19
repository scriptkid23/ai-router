from ai_router.adapters.registry import build_registry


def test_build_registry_includes_deepseek():
    registry = build_registry()
    ids = [a.id for a in registry.list_all()]
    assert "deepseek" in ids


def test_deepseek_adapter_is_available():
    registry = build_registry()
    ds = registry.get("deepseek")
    assert ds.status == "available"
    assert ds.name == "DeepSeek"
