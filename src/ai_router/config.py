from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

CONFIG_DIR = Path.home() / ".ai-router"
DEFAULT_CONFIG_PATH = CONFIG_DIR / "config.yaml"


@dataclass
class ProviderConfig:
    url: str


@dataclass
class AppConfig:
    profile_dir: Path
    default_provider: str
    host: str
    port: int
    answer_timeout_s: int
    providers: dict[str, ProviderConfig] = field(default_factory=dict)


def _expand(path: str) -> Path:
    return Path(path).expanduser().resolve()


def _defaults() -> AppConfig:
    return AppConfig(
        profile_dir=CONFIG_DIR / "profile",
        default_provider="gemini",
        host="127.0.0.1",
        port=8087,
        answer_timeout_s=120,
        providers={
            "gemini": ProviderConfig(url="https://gemini.google.com/app"),
        },
    )


def load_config(path: Path | None = None) -> AppConfig:
    cfg = _defaults()
    config_path = path or DEFAULT_CONFIG_PATH
    if config_path.exists():
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if "profile_dir" in raw:
            cfg.profile_dir = _expand(raw["profile_dir"])
        if "default_provider" in raw:
            cfg.default_provider = raw["default_provider"]
        if "host" in raw:
            cfg.host = raw["host"]
        if "port" in raw:
            cfg.port = int(raw["port"])
        if "answer_timeout_s" in raw:
            cfg.answer_timeout_s = int(raw["answer_timeout_s"])
        if "providers" in raw:
            for pid, pdata in raw["providers"].items():
                cfg.providers[pid] = ProviderConfig(url=pdata["url"])

    if v := os.getenv("AI_ROUTER_PROFILE_DIR"):
        cfg.profile_dir = _expand(v)
    if v := os.getenv("AI_ROUTER_DEFAULT_PROVIDER"):
        cfg.default_provider = v
    if v := os.getenv("AI_ROUTER_HOST"):
        cfg.host = v
    if v := os.getenv("AI_ROUTER_PORT"):
        cfg.port = int(v)
    if v := os.getenv("AI_ROUTER_ANSWER_TIMEOUT_S"):
        cfg.answer_timeout_s = int(v)

    return cfg
