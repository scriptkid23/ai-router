from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

LOG_DIR = Path.home() / ".ai-router" / "logs"

logger = logging.getLogger("ai_router")
logger.setLevel(logging.DEBUG)
logger.propagate = False

_configured = False


def configure(level: str | None = None) -> None:
    global _configured
    if _configured:
        return
    _configured = True

    log_level = (level or os.environ.get("AI_ROUTER_LOG_LEVEL", "INFO")).upper()
    numeric = getattr(logging, log_level, logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    stderr = logging.StreamHandler(sys.stderr)
    stderr.setLevel(numeric)
    stderr.setFormatter(formatter)
    logger.addHandler(stderr)

    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_file = LOG_DIR / f"ai-router-{datetime.now(timezone.utc).strftime('%Y%m%d')}.log"
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        logger.debug("log_file=%s", log_file)
    except OSError:
        pass

    logger.setLevel(logging.DEBUG)
    trace("logger_ready", level=log_level)


def trace(event: str, **fields: object) -> None:
    parts = [event]
    for key, value in fields.items():
        if value is None:
            continue
        text = str(value).replace("\n", " ")
        if len(text) > 120:
            text = text[:117] + "..."
        parts.append(f"{key}={text}")
    logger.info(" | ".join(parts))
