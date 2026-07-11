from pathlib import Path

import tomllib


def test_console_script_is_ai_router_only():
    data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    scripts = data["tool"]["poetry"]["scripts"]
    assert "ai-router" in scripts
    assert scripts["ai-router"] == "ai_router.cli.main:app"
    assert "ai" not in scripts


def test_mcp_dependency_has_upper_bound():
    deps = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["tool"]["poetry"][
        "dependencies"
    ]
    assert deps["mcp"] == ">=1.6,<2"


def test_python_requires_311_to_4():
    deps = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["tool"]["poetry"][
        "dependencies"
    ]
    assert deps["python"] == ">=3.11,<4"
